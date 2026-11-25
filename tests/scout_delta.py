#!/usr/bin/env python3
"""Verify zero-loss detection and zero false-positive token drain in `scout.py`,
`digest.py --delta`, and `apply.py`."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import agent as AG  # noqa: E402
import apply as A  # noqa: E402
import digest as D  # noqa: E402
import prioritize as P  # noqa: E402
import scout  # noqa: E402
import serve as SV  # noqa: E402
import settings  # noqa: E402
import targets  # noqa: E402


SAMPLE_CFG = {
    "owner": {
        "name": "Alex Example",
        "email": "alex.example@kraken.tech",
        "asana_user_gid": "1200000000000099",
        "slack_user_id": "U00000099",
        "role": "Client Delivery Lead",
        "workstream": "billing",
    },
    "client": {"name": "Example Energy", "short": "EE", "domains": ["example-energy.co.jp"], "language": "ja"},
    "timezone": "Asia/Tokyo",
    "asana": {
        "workspace_gid": "1200000000000000",
        "projects": [{"gid": "1200000000000001", "name": "Shared Board"}],
        "exclude_projects": [{"gid": "1200000000000002", "name": "Onboarding"}],
    },
    "meeting": {
        "name": "Client Standup",
        "time_local": "10:30",
        "minutes": 30,
        "days": ["Mon", "Wed"],
        "also": [
            {"name": "Weekly Review", "kind": "weekly", "time_local": "13:00", "days": ["Wed"], "record": ""},
        ],
    },
    "slack": {
        "channel_hints": ["#ext-client-billing"],
    },
    "prioritization": {
        "who": "Sam Lead",
        "slack_user_id": "U00000088",
        "channel": "#team-cdl",
        "channel_id": "C00000088",
        "anchor": "2026-10-26",
        "every_days": 14,
        "thread_at": "08:30",
        "ce_projects": ["1200000000000055"],
        "cdl_owner_field": "1200000000000066",
    },
}


def sample_board() -> dict:
    return {
        "version": 1,
        "checked_at": "2026-11-11T09:05:00+09:00",
        "next_id": 10,
        "brief": {"for_date": "2026-11-11", "built_at": "2026-11-11T08:15:00+09:00", "lines": []},
        "script": {"for_date": "2026-11-11", "at": "10:30", "built_at": "2026-11-11T09:05:00+09:00", "headline": "Walk"},
        "sessions": [{"kind": "standup", "date": "2026-11-11", "at": "10:30", "title": "Client Standup"}],
        "prioritization": {
            "checked_at": "2026-11-11T09:05:00+09:00",
            "builds": [{"kt": "KT-100", "gid": "1200000000000500", "ticket": "HOLD-REL", "status": "Ready to Build"}],
        },
        "tickets": [
            {
                "id": "1200000000000101",
                "ref": "HOLD-REL",
                "title_ja": "ホールド解除",
                "title_en": "Hold release",
                "asana_url": "https://app.asana.com/0/1200000000000001/1200000000000101",
                "asana": {
                    "status": "調査中",
                    "section": "Current Cycle",
                    "priority": "Priority",
                    "assignee": "Alex Example",
                    "completed": False,
                },
                "internal_ticket": {
                    "name": "KT-100",
                    "url": "https://app.asana.com/0/1200000000000055/1200000000000500",
                    "build": {"kt": "KT-100", "stage": "queued"},
                },
                "where_it_stands": "Investigating the hold.",
                "threads": [
                    {
                        "label": "Refinement thread",
                        "where": "Slack",
                        "url": "https://kraken.enterprise.slack.com/archives/C00000011/p1794350000000000",
                        "last_at": "2026-11-11 08:30",
                        "last_from": "Sam",
                        "checked": "2026-11-11T09:05:00+09:00",
                    },
                    {
                        "label": "Old billing thread",
                        "where": "Slack",
                        "url": "https://kraken.enterprise.slack.com/archives/C00000022/p1780000000000000",
                        "last_at": "2026-09-01 10:00",
                        "last_from": "Client",
                        "checked": "2026-11-11T09:05:00+09:00",
                    },
                ],
                "events": [{"on": "2026-11-11", "at": "08:30", "who": "Sam", "what": "Checked logs", "where": "Slack"}],
                "prep": {
                    "order": 1,
                    "board_position": "1 of 5",
                    "script": [{"heading": "現状", "heading_en": "Where it stands", "lines": [{"ja_ruby": "調査中です。", "en": "Investigating."}]}],
                    "client_ask_needed": False,
                    "built_at": "2026-11-11T09:05:00+09:00",
                },
                "items": [
                    {
                        "id": 4,
                        "title": "Reply to Sam and update canvas",
                        "state": "todo",
                        "urgency": "today",
                        "steps": [
                            "Reply in Slack and add note to https://cispj.slack.com/docs/T0001/F00000099",
                        ],
                        "done_when": "Posted",
                    }
                ],
            }
        ],
    }


def check(name: str, ok: bool, detail: str = "") -> tuple[str, bool, str]:
    return (name, ok, detail)


def run_tests() -> list[str]:
    failures: list[str] = []

    def expect(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"FAIL: {name}: {detail}")

    with (
        mock.patch.object(settings, "conf", return_value=SAMPLE_CFG),
        mock.patch.object(targets, "configured_mcps", return_value={"asana", "slack", "google", "notion"}),
        mock.patch.object(calendar_sync := scout.calendar_sync, "standup_notes", return_value=None),
        mock.patch.object(calendar_sync, "unread_notes", return_value=[]),
        mock.patch.object(calendar_sync, "unfolded_room_notes", return_value=[]),
    ):
        tz = settings.tz()
        now_pre_room = datetime(2026, 11, 11, 9, 45, tzinfo=tz)  # Wed 09:45, 45m before 10:30 standup
        now_afternoon = datetime(2026, 11, 11, 15, 30, tzinfo=tz)  # Wed 15:30, after all rooms

        # 1. Quiet run with multi-project section membership and 20 unrelated tasks in shared project -> 0 false triggers.
        b = sample_board()
        seen = scout._sync_seen_with_board(b, {})
        queries, meta = scout.build_queries(b, seen)
        q_by_tag = {q["tag"]: q for q in queries}

        expect("census query built", "census" in q_by_tag)
        expect("live thread query built", "thread:C00000011:1794350000.000000" in q_by_tag)
        expect("old thread channel search built", "slack_chan:C00000022" in q_by_tag)
        expect("canvas read built", "canvas:F00000099" in q_by_tag)
        expect("gmail client delta query built", "gmail_client" in q_by_tag)

        quiet_paired = [
            (
                q_by_tag["census"],
                json.dumps({
                    "data": [{
                        "gid": "1200000000000101",
                        "name": "ホールド解除",
                        "completed": False,
                        "modified_at": "2026-11-10T23:30:00.000Z",
                        "assignee": "Alex Example",
                        "projects": [{"gid": "1200000000000001"}],
                        "memberships": [{"section": "Other Board"}, {"section": "Current Cycle"}],
                        "custom_fields": {"Status": "調査中 ", "Priority (Kraken resource allocation)": "Priority"},
                    }]
                }),
            ),
            (
                q_by_tag["gate"],
                json.dumps({
                    "data": [
                        # Unrelated other CDL task in shared project -> must NOT trigger!
                        {
                            "gid": "1200000000000999",
                            "name": "Other CDL task",
                            "completed": False,
                            "modified_at": "2026-11-11T00:40:00.000Z",
                            "assignee": "Someone Else",
                            "projects": [{"gid": "1200000000000001"}],
                        }
                    ]
                }),
            ),
            (q_by_tag["ce_builds"], json.dumps({"data": []})),
            (
                q_by_tag["thread:C00000011:1794350000.000000"],
                json.dumps({"messages": "no new replies since oldest=1794353460.000000"}),
            ),
            (q_by_tag["slack_from_me"], json.dumps({"results": "No results found."})),
            (q_by_tag["slack_mentions"], json.dumps({"results": "No results found."})),
            (q_by_tag["slack_lead_dm"], json.dumps({"messages": "Channel: DM (D0001)\n\n"})),
            (
                q_by_tag["slack_chan:C00000022"],
                json.dumps({
                    "results": (
                        "### Result 1 of 1\n"
                        "Channel: #ext-billing (ID: C00000022)\n"
                        "From: Held Bills Bot (ID: B0001)\n"
                        "Time: 2026-11-11 09:30:00 JST\n"
                        "Message_ts: 1794357000.000100\n"
                        "Permalink: [link](https://kraken.enterprise.slack.com/archives/C00000022/p1794357000000100)\n"
                        "Text:\nDaily automated bot stats\n---\n"
                    )
                }),
            ),
            (q_by_tag["canvas:F00000099"], json.dumps({"markdown_content": "# Agenda\nNothing on HOLD-REL yet\n"})),
            (q_by_tag["gmail_client"], json.dumps({"messages": []})),
        ]

        res_quiet = scout.analyze_results(b, seen, meta, quiet_paired, now=now_pre_room)
        expect("quiet run reports changed=False", res_quiet["changed"] is False, str(res_quiet))

        # 2. New mention + silent reassignment + pre-room sync active (45m before 10:30 standup).
        moved_paired = list(quiet_paired)
        # Empty census -> HOLD-REL vanished (reassigned or completed)!
        moved_paired[0] = (q_by_tag["census"], json.dumps({"data": []}))
        # New mention in Slack!
        moved_paired[5] = (
            q_by_tag["slack_mentions"],
            json.dumps({
                "results": (
                    "### Result 1 of 1\n"
                    "Channel: #random (ID: C00000077)\n"
                    "From: Colleague (ID: U00000044)\n"
                    "Time: 2026-11-11 09:40:00 JST\n"
                    "Message_ts: 1794357600.123456\n"
                    "Permalink: [link](https://kraken.enterprise.slack.com/archives/C00000077/p1794357600123456)\n"
                    "Text:\nHi <@U00000099> quick ping!\n---\n"
                )
            }),
        )
        res_moved = scout.analyze_results(b, seen, meta, moved_paired, now=now_pre_room)
        expect("reassignment + mention detected", res_moved["changed"] is True)
        expect("census_missing caught", len(res_moved["census_missing"]) == 1)
        expect("slack mention caught", len(res_moved["slack_messages"]) == 1)
        expect("pre-room prep sync active 45m before room",
               bool(res_moved["pre_room_prep_sync"] and res_moved["pre_room_prep_sync"]["active"]))

        # Outside the 90m window (at 15:30), pre_room_prep_sync must be None.
        res_afternoon = scout.analyze_results(b, seen, meta, moved_paired, now=now_afternoon)
        expect("pre-room prep sync inactive in afternoon", res_afternoon["pre_room_prep_sync"] is None)

        # 3. Pending -> Swept promotion:
        #    - Before board.checked_at advances (e.g. delta run failed), mention MUST re-trigger (zero lost info).
        #    - Once board.checked_at advances, mention promotes to swept_slack_keys and does NOT re-trigger!
        pending = res_moved["_pending"]
        seen_after_trigger = {
            "board_checked_at": b["checked_at"],
            "pending_asana_mod": pending["asana_mod"],
            "pending_slack_keys": pending["slack_keys"],
            "pending_canvas_hash": pending["canvas_hash"],
            "swept_slack_keys": pending["swept_slack"],
            "swept_canvas_hash": pending["swept_canvas"],
        }
        # Restore census to normal so only the Slack mention is tested.
        mention_only_paired = list(quiet_paired)
        mention_only_paired[5] = moved_paired[5]

        seen_unadvanced = scout._sync_seen_with_board(b, copy.deepcopy(seen_after_trigger))
        res_retry = scout.analyze_results(b, seen_unadvanced, meta, mention_only_paired, now=now_afternoon)
        expect("unapplied delta retries on next scout (zero loss)", res_retry["changed"] is True)

        b_advanced = copy.deepcopy(b)
        b_advanced["checked_at"] = "2026-11-11T09:50:00+09:00"
        seen_advanced = scout._sync_seen_with_board(b_advanced, copy.deepcopy(seen_after_trigger))
        res_after_sweep = scout.analyze_results(b_advanced, seen_advanced, meta, mention_only_paired, now=now_afternoon)
        expect("applied delta promotes watermark and never re-triggers",
               res_after_sweep["changed"] is False, str(res_after_sweep))

        # 4. Old >30d thread woken by a reply in `in:<#C00000022>` -> MUST trigger!
        wake_paired = list(quiet_paired)
        wake_paired[7] = (
            q_by_tag["slack_chan:C00000022"],
            json.dumps({
                "results": (
                    "### Result 1 of 1\n"
                    "Channel: #ext-billing (ID: C00000022)\n"
                    "From: Client Person (ID: U00000055)\n"
                    "Time: 2026-11-11 09:42:00 JST\n"
                    "Message_ts: 1794357720.000200\n"
                    "Permalink: [link](https://kraken.enterprise.slack.com/archives/C00000022/p1794357720000200?thread_ts=1780000000.000000&cid=C00000022)\n"
                    "Text:\nFollowing up on this thread!\n---\n"
                )
            }),
        )
        res_wake = scout.analyze_results(b, seen, meta, wake_paired, now=now_afternoon)
        expect("old >30d thread wake caught", res_wake["changed"] is True and len(res_wake["slack_messages"]) == 1)

        # 5. apply.py stamps scout-checked thread watermarks when swept=True.
        b_stamp = sample_board()
        b_stamp["tickets"][0]["threads"][0]["checked"] = "2026-11-01T00:00:00+09:00"
        with tempfile.TemporaryDirectory() as tmpd:
            tmp_path = Path(tmpd)
            delta_file = tmp_path / "delta.json"
            delta_file.write_text(
                json.dumps({"checked_urls": [b_stamp["tickets"][0]["threads"][0]["url"]]}),
                encoding="utf-8",
            )
            with mock.patch.object(settings, "TMP_DIR", tmp_path):
                A.apply(b_stamp, {"swept": True})
            expect("apply.py stamps scout-checked thread watermark",
                   b_stamp["tickets"][0]["threads"][0]["checked"] != "2026-11-01T00:00:00+09:00")

        # 6. slack.channel_hints catches unlinked posts naming a ticket/KT/account
        #    while ignoring unrelated channel chatter.
        expect("channel_hints query built", "slack_hint:ext-client-billing" in q_by_tag)
        hint_noise = list(quiet_paired) + [
            (
                q_by_tag["slack_hint:ext-client-billing"],
                json.dumps({
                    "results": (
                        "### Result 1 of 1\n"
                        "Channel: #ext-client-billing (ID: C00000033)\n"
                        "From: Other Person (ID: U00000077)\n"
                        "Time: 2026-11-11 09:44:00 JST\n"
                        "Message_ts: 1794357840.000111\n"
                        "Permalink: [link](https://kraken.enterprise.slack.com/archives/C00000033/p1794357840000111)\n"
                        "Text:\nUnrelated question on another workstream\n---\n"
                    )
                }),
            )
        ]
        res_hint_noise = scout.analyze_results(b, copy.deepcopy(seen), meta, hint_noise, now=now_afternoon)
        expect("unrelated channel_hints chatter stays quiet", res_hint_noise["changed"] is False)

        hint_match = list(quiet_paired) + [
            (
                q_by_tag["slack_hint:ext-client-billing"],
                json.dumps({
                    "results": (
                        "### Result 1 of 1\n"
                        "Channel: #ext-client-billing (ID: C00000033)\n"
                        "From: Client Ops (ID: U00000078)\n"
                        "Time: 2026-11-11 09:45:00 JST\n"
                        "Message_ts: 1794357900.000222\n"
                        "Permalink: [link](https://kraken.enterprise.slack.com/archives/C00000033/p1794357900000222)\n"
                        "Text:\nChecking status on HOLD-REL without tagging anyone\n---\n"
                    )
                }),
            )
        ]
        res_hint_match = scout.analyze_results(b, copy.deepcopy(seen), meta, hint_match, now=now_afternoon)
        expect("unlinked channel_hints post mentioning ticket ref caught",
               res_hint_match["changed"] is True and len(res_hint_match["slack_messages"]) == 1)

        # 7. Guaranteed once-per-workday Full Refresh (daily_full_refresh_due).
        with tempfile.TemporaryDirectory() as tmpd:
            tmp_path = Path(tmpd)
            fake_seen = tmp_path / "scout-seen.json"
            fake_board = tmp_path / "board.json"
            fake_board.write_text(json.dumps(b), encoding="utf-8")
            thu_morning = datetime(2026, 11, 12, 9, 7, tzinfo=tz)  # Thu 09:07 after missed 08:15 boot
            with (
                mock.patch.object(scout, "SEEN", fake_seen),
                mock.patch.object(scout.B, "BOARD", fake_board),
                mock.patch.object(settings, "LOGS_DIR", tmp_path),
                mock.patch.object(settings, "auto_sweep_mins", return_value=15),
            ):
                scout.record_full_refresh("2026-11-11T17:01:00+09:00")
                expect("daily full refresh is due on weekday boot after 08:15",
                       scout.daily_full_refresh_due(thu_morning) is True)
                scout.record_full_attempt("2026-11-12T09:07:05+09:00")
                expect("30m cooldown prevents duplicate trigger while running",
                       scout.daily_full_refresh_due(thu_morning) is False)
                scout.record_full_refresh("2026-11-12T09:10:00+09:00")
                expect("daily full refresh satisfied for rest of day once completed",
                       scout.daily_full_refresh_due(datetime(2026, 11, 12, 14, 0, tzinfo=tz)) is False)

        # 8. Client-domain email delta detection + RSVP noise filtering + watermark promotion.
        email_paired = list(quiet_paired)
        email_paired[-1] = (
            q_by_tag["gmail_client"],
            json.dumps({
                "messages": [
                    {"id": "msg-rsvp-1", "from": "calendar@example-energy.co.jp", "subject": "Accepted: Client Standup"},
                    {"id": "msg-real-1", "from": "sato@example-energy.co.jp", "subject": "HOLD-REL confirmation", "snippet": "We checked the 5 bills."},
                ]
            }),
        )
        res_email = scout.analyze_results(b, copy.deepcopy(seen), meta, email_paired, now=now_afternoon)
        expect("client email triggers delta while RSVP noise is ignored",
               res_email["changed"] is True and len(res_email["email_messages"]) == 1)

        # 9. Post-call Notion meeting notes detection when a room ended without Gemini doc.
        with mock.patch.object(
            calendar_sync,
            "unfolded_room_notes",
            return_value=[{
                "kind": "standup",
                "name": "Client Standup",
                "notes_title": "Client Standup",
                "record": "meeting_note",
                "date": "2026-11-11",
                "ended_at": "11:00",
                "link": "",
            }],
        ):
            q_room, m_room = scout.build_queries(b, seen)
            qr_map = {q["tag"]: q for q in q_room}
            expect("post-call Notion meeting note query built", "notion_notes:meeting_note:standup" in qr_map)
            room_paired = list(quiet_paired) + [
                (
                    qr_map["notion_notes:meeting_note:standup"],
                    json.dumps({"results": [{"url": "https://app.notion.com/p/abcd1234567890"}]}),
                )
            ]
            res_room = scout.analyze_results(b, copy.deepcopy(seen), m_room, room_paired, now=now_afternoon)
            expect("new Notion meeting note triggers delta",
                   res_room["changed"] is True and len(res_room["notion_meeting_notes"]) == 1)

        # 10. digest --delta expands tickets matched by build GID / KT id in prioritization Slack posts
        #     and prints last_round, raised, and build name/why inline (zero follow-up digest calls).
        b_prio = sample_board()
        b_prio["prioritization"]["last_round"] = {"date": "2026-11-09", "ordered": ["KT-100"]}
        b_prio["prioritization"]["builds"][0]["name"] = "Hold release fix"
        b_prio["prioritization"]["builds"][0]["why"] = "Unblocks 12 held bills"
        delta_gid_post = {
            "changed": True,
            "slack_messages": [{
                "source": "prioritization_lead",
                "channel": "#team-cdl",
                "channel_id": "C00000088",
                "from": "Sam Lead",
                "time": "2026-11-11 09:00",
                "url": "https://kraken.enterprise.slack.com/archives/C00000088/p1794355200000000",
                "text": "Pre-cycle list: https://app.asana.com/1/1200/project/1200000000000055/task/1200000000000500",
            }],
        }
        D._lines.clear()
        D._scale = 1.0
        ns = type("Args", (), {"items_only": False, "everything": False})()
        D._board(b_prio, ns, False, delta_gid_post)
        digest_txt = "\n".join(D._lines)
        expect("digest --delta expands ticket matched by build GID", "=== HOLD-REL   gid 1200000000000101 ===" in digest_txt)
        expect("digest --delta prints prioritization last_round & why inline",
               "last_round: date=2026-11-09" in digest_txt and "Unblocks 12 held bills" in digest_txt)

        # 11. prioritize.py keeps normal cutoff when round moves earlier to Friday before a Monday holiday,
        #     and keeps ordered builds scheduled across the holiday weekend handoff.
        prio_cfg = {
            **SAMPLE_CFG["prioritization"],
            "cutoff_at": "13:30",
            "holiday_cutoff_at": "10:00",
            "holidays": ["2026-11-23"],
            "moved": [{"from": "2026-11-23", "to": "2026-11-20", "why": "Mon holiday, moved to Fri"}],
        }
        r_fri = P.rounds(prio_cfg, start=datetime(2026, 11, 20, tzinfo=tz).date(), count=1)[0]
        expect("earlier Friday round uses normal 13:30 cutoff, not 10:00 holiday cutoff",
               r_fri["date"] == "2026-11-20" and r_fri["cutoff_at"] == "13:30", str(r_fri))
        with mock.patch.object(P, "config", return_value=prio_cfg), mock.patch.object(P, "clock", return_value="15:00"):
            b_fri = {
                "prioritization": {
                    "checked_at": "2026-11-20T09:05:00+09:00",
                    "last_round": {"date": "2026-11-20", "ordered": ["KT-100"]},
                    "builds": [{
                        "kt": "KT-100", "status": "Ready to Build", "refinement": "🟢",
                        "why": "Unblocks held bills",
                    }],
                }
            }
            s_tue = P.summary(b_fri, today=datetime(2026, 11, 24, tzinfo=tz).date())
            expect("build ordered on Fri stays ordered on Tue after Mon holiday (1 working day)",
                   s_tue["rows"][0]["kind"] == "ordered", str(s_tue["rows"]))
            s_thu = P.summary(b_fri, today=datetime(2026, 11, 26, tzinfo=tz).date())
            expect("un-Waved build re-surfaces as raise after >2 working days",
                   s_thu["rows"][0]["kind"] == "raise", str(s_thu["rows"]))

        # 12. Cold-server restart preserves gateway credential via Keychain without
        #     invoking apiKeyHelper, and throttles failed helper attempts cleanly.
        kc_store: dict[str, str] = {}
        fake_conf = {
            "apiKeyHelper": 'op read "op://Vault/Gateway/credential"',
            "env": {"ANTHROPIC_BASE_URL": "https://gateway.example.test"},
        }
        helper_runs: list[int] = []

        def fake_run(cmd, *a, **kw):
            helper_runs.append(1)
            return type("R", (), {"returncode": 0, "stdout": "sk-test-key-1\n", "stderr": ""})()

        saved_cred = AG.Claude._credential
        saved_last_ts = SV._last_cred_fetch_ts
        try:
            AG.Claude._credential = ""
            SV._last_cred_fetch_ts = 0.0
            with (
                mock.patch.dict("os.environ", {}, clear=False),
                mock.patch.object(AG.Claude, "_claude_settings", return_value=fake_conf),
                mock.patch.object(AG.Claude, "_keychain_get", side_effect=lambda a: kc_store.get(a, "")),
                mock.patch.object(AG.Claude, "_keychain_set", side_effect=lambda a, v: kc_store.__setitem__(a, v)),
                mock.patch.object(AG.Claude, "_keychain_delete", side_effect=lambda a="": kc_store.clear()),
                mock.patch("agent.subprocess.run", side_effect=fake_run),
            ):
                AG.os.environ.pop("ANTHROPIC_API_KEY", None)
                cl = AG.Claude()
                # Cold start with empty Keychain: background scout fetches once and caches in Keychain.
                expect("cold background check fetches once when Keychain is empty",
                       SV._credential_ready_quiet(cl, allow_fetch=False) is True and len(helper_runs) == 1)
                # Simulate serve.py restart (in-memory _credential wiped, Keychain intact).
                AG.Claude._credential = ""
                expect("restarted server recovers key from Keychain without calling apiKeyHelper",
                       SV._credential_ready_quiet(cl, allow_fetch=False) is True and len(helper_runs) == 1)
                # Key rotation: forget_credential clears memory + Keychain.
                cl.forget_credential()
                expect("forget_credential clears both memory and Keychain",
                       cl.credential_quiet() == "" and not kc_store)

            # Locked vault: failed fetch backs off on timer/focus, while force=True retries immediately.
            fail_runs: list[int] = []
            def fake_locked(*a, **kw):
                fail_runs.append(1)
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": "locked"})()

            AG.Claude._credential = ""
            SV._last_cred_fetch_ts = 0.0
            with (
                mock.patch.dict("os.environ", {}, clear=False),
                mock.patch.object(AG.Claude, "_claude_settings", return_value=fake_conf),
                mock.patch.object(AG.Claude, "_keychain_get", return_value=""),
                mock.patch("agent.subprocess.run", side_effect=fake_locked),
            ):
                AG.os.environ.pop("ANTHROPIC_API_KEY", None)
                cl = AG.Claude()
                expect("locked vault returns False on first attempt",
                       SV._credential_ready_quiet(cl, allow_fetch=False) is False and len(fail_runs) == 1)
                expect("locked vault is throttled on next 30s tick (no Touch ID spam)",
                       SV._credential_ready_quiet(cl, allow_fetch=False) is False and len(fail_runs) == 1)
                expect("explicit Check now (force=True) bypasses throttle immediately",
                       SV._credential_ready_quiet(cl, allow_fetch=True, force=True) is False and len(fail_runs) == 2)
        finally:
            AG.Claude._credential = saved_cred
            SV._last_cred_fetch_ts = saved_last_ts

        # 13. Notion meeting-note filter schema uses typed exact/relative objects,
        #     `slack_from_lead` + always-on CDL channel watch catch lead instructions,
        #     `apply.py` re-homes items across tickets preserving ID, and `script_state()`
        #     flags unscripted Priority tickets on `What I say`.
        import render_desk as RD

        with mock.patch.object(
            calendar_sync,
            "unfolded_room_notes",
            return_value=[{
                "kind": "standup",
                "name": "Billing Stand Up",
                "notes_title": "Billing Stand Up",
                "record": "meeting_note",
                "date": "2026-11-11",
                "ended_at": "11:00",
                "link": "",
            }],
        ):
            q_n, _ = scout.build_queries(b, seen)
            nq = next(q for q in q_n if q["tag"] == "notion_notes:meeting_note:standup")
            nfilters = nq["args"]["filter"]["filters"]
            expect(
                "notion-query-meeting-notes uses typed exact/relative filter objects",
                nfilters[0]["filter"]["value"] == {"type": "exact", "value": "Billing"}
                and nfilters[1]["filter"]["value"] == {"type": "relative", "value": "today"},
                str(nfilters),
            )
            expect("slack_from_lead query built", any(q["tag"] == "slack_from_lead" for q in q_n))
            expect("CDL channel watched even outside prioritization window",
                   any(q["tag"] == "slack_chan:C00000088" for q in q_n))

        b_rehome = sample_board()
        b_rehome["tickets"].append({
            "id": "1200000000000202",
            "ref": "NEW-PRIO",
            "title_ja": "新規優先チケット",
            "title_en": "New priority ticket",
            "asana_url": "https://app.asana.com/0/1200000000000001/1200000000000202",
            "asana": {
                "status": "調査中",
                "section": "Current Cycle",
                "priority": "Priority",
                "assignee": "Alex Example",
                "completed": False,
            },
            "where_it_stands": "Newly assigned.",
            "items": [],
        })
        with mock.patch.object(settings, "now", return_value=now_pre_room):
            stale_flag, needs_prep_flag = RD.script_state(
                b_rehome,
                b_rehome["tickets"],
                {"date": "2026-11-11", "at": "10:30"},
            )
        expect("unscripted Priority cycle ticket marks script stale / needs_prep",
               stale_flag is True and needs_prep_flag is True, f"({stale_flag}, {needs_prep_flag})")

        A.apply(b_rehome, {"items": {"4": {"ticket": "NEW-PRIO"}}})
        expect("apply.py re-homes item 4 onto NEW-PRIO while preserving item number",
               len(b_rehome["tickets"][0]["items"]) == 0
               and len(b_rehome["tickets"][1]["items"]) == 1
               and b_rehome["tickets"][1]["items"][0]["id"] == 4)

    return failures


def main() -> int:
    bad = run_tests()
    if bad:
        for ln in bad:
            print(ln)
        return 1
    print("All scout and delta invariants hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
