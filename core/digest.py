#!/usr/bin/env python3
"""Everything a sweep needs to read off the board, in one call.

A refresh used to open `state/board.json` fifteen times: a one-liner to list the
tickets, another for the threads, another to remember what an item said, a dump
into `/tmp` and a read back out of it. Each of those is a round trip worth
several seconds, and together they were most of an eight minute refresh, none of
it spent talking to Asana or Slack.

So this prints the whole readable half of the board at once: what is on it, what
is open, where each ticket stands, which threads to read and when each was last
read. `--full` unclips the prose of the tickets with open work, which is what a
prep writes from. `--everything` adds closed items, every event and the finished
tickets, for the rare sweep that needs them.

It never writes. The writing half is one patch file and `./apply.py`, and the
runner rebuilds the pages.

The default print keeps itself under BUDGET characters by tightening its clips
as the board grows. Over about 30k the harness persists a tool result to a file
and the model spends a turn reading it back, which is the round trip this file
exists to remove.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import audit
import board as B
import plugins
import prioritize
import calendar_sync
import scout
import settings

WRAP = 160
ROOT = Path(__file__).resolve().parent
BUDGET = 27_000
SCALES = (1.0, 0.8, 0.65, 0.5, 0.35, 0.25, 0.2)
_scale = 1.0
_lines: list[str] = []


def _project_gids() -> list[str]:
    """The client-shared Asana project gids the gate query runs against, from config.json."""
    return [p["gid"] for p in settings.section("asana").get("projects") or []
            if isinstance(p, dict) and p.get("gid")]


def _asana_since(stamp: str) -> str:
    """A board watermark as a UTC ISO-8601 timestamp (`YYYY-MM-DDTHH:MM:SSZ`), 2 minutes early."""
    raw = str(stamp or "").strip()
    if raw:
        try:
            when = datetime.fromisoformat(raw.replace(" ", "T"))
            if len(raw) <= 10:
                when = datetime(when.year, when.month, when.day, tzinfo=settings.tz())
            elif when.tzinfo is None:
                when = when.replace(tzinfo=settings.tz())
            return (when.astimezone(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")


def _age(stamp: str) -> str:
    """How stale the board is, in the words the owner would use out loud."""
    if not stamp:
        return "never"
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    secs = (datetime.now().astimezone() - then).total_seconds()
    if secs < 0:
        return "in the future"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m ago"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h {mins}m ago"
    return f"{hours // 24}d {hours % 24}h ago"


def _clip(text: object, limit: int, full: bool = False) -> str:
    s = " ".join(str(text or "").split())
    if not full:
        floor = 18 if _scale <= 0.35 else 24 if _scale <= 0.5 else 30
        limit = max(floor, int(limit * _scale))
    if full or len(s) <= limit:
        return s
    if not full and _scale <= 0.65:
        return s[:limit].rstrip() + "..."
    return s[:limit].rstrip() + f"... (+{len(s) - limit} chars)"


def _out(line: str = "") -> None:
    _lines.append(line)


def _prep_summary(prep: dict) -> None:
    """Compact summary of a ticket's existing `prep` block for `--full` mode."""
    bits = [
        f"{k}={prep[k]}"
        for k in ("order", "board_position", "client_ask_needed", "estimate", "built_at")
        if prep.get(k) not in (None, "")
    ]
    _out("    prep: " + ("  ".join(bits) if bits else "present"))
    if prep.get("issue"):
        _out("      issue: " + " ".join(str(x) for x in prep["issue"]))
    if prep.get("why_it_matters"):
        _out(f"      why_it_matters: {prep['why_it_matters']}")
    cons = prep.get("consequences") or {}
    c_bits = [f"{k}={cons[k]}" for k in ("fix_covers", "falls_outside", "accumulates",
                                          "who_owns_it", "done_means", "still_open") if cons.get(k)]
    if c_bits:
        _out("      consequences: " + " | ".join(c_bits))
    for blk in prep.get("script") or []:
        head = blk.get("heading") or blk.get("heading_en") or "script"
        for ln in blk.get("lines") or []:
            _out(f"      script [{head}] ja_ruby: {ln.get('ja_ruby', '')}")
            if ln.get("en"):
                _out(f"      script [{head}] en: {ln.get('en', '')}")
    for q in prep.get("open_questions") or []:
        _out(f"      open_question [{q.get('who', '?')}]: {q.get('ja_ruby') or q.get('en', '')}")
    if prep.get("unknowns"):
        _out("      unknowns: " + "; ".join(str(u) for u in prep["unknowns"]))


def _ticket_head(t: dict, full: bool, everything: bool = False) -> None:
    ref = t.get("ref") or t.get("id")
    asana = t.get("asana") or {}
    _out(f"=== {ref}   gid {t.get('id', '?')} ===")
    _out(f"    title_ja: {_clip(t.get('title_ja'), 90, full)}")
    if full:
        _out(f"    title_en: {_clip(t.get('title_en'), 90, full)}")
    if t.get("asana_url") and full:
        _out(f"    url: {t['asana_url']}")
    if asana:
        bits = [
            f"{k}={asana[k]}"
            for k in ("status", "status_should", "section", "priority", "severity",
                      "assignee", "completed")
            if asana.get(k) not in (None, "")
        ]
        if bits:
            _out("    asana: " + "  ".join(bits))
    if t.get("no_ticket_yet"):
        _out(f"    NO TICKET YET: {_clip(t['no_ticket_yet'], 120, full)}")

    it = t.get("internal_ticket") or {}
    if it.get("none_yet"):
        _out(f"    internal: none, because {_clip(it['none_yet'], 110, full)}")
    elif it:
        bld = it.get("build") or {}
        bits = [f"{k}={bld[k]}" for k in ("kt", "stage", "engineer", "size") if bld.get(k)]
        _out(f"    internal: {_clip(it.get('name', '?'), 95, full)} {' '.join(bits)}")
        if bld.get("waiting_on"):
            _out(f"      waiting_on: {_clip(bld['waiting_on'], 110 if full else 80, full)}")

    la = t.get("last_activity") or {}
    if la:
        _out(f"    last_activity: {la.get('at', '?')} {la.get('who', '')} ({la.get('where', '')})")
    _out(f"    where_it_stands: {_clip(t.get('where_it_stands'), 400 if full else 160, full)}")

    gates = t.get("closes_when") or []
    # A gate already passed changes nothing a sweep or a prep does; only --everything prints it.
    shown_gates = gates if everything else [g for g in gates if g.get("state") != "done"]
    if shown_gates and (full or _scale > 0.2 or any(i.get("state") == "todo" for i in t.get("items") or [])):
        if not full and _scale <= 0.5:
            summary = "; ".join(
                f"({g.get('state', '?')}){' [' + g['who'] + ']' if g.get('who') else ''} {_clip(g.get('what'), 55, False)}"
                for g in shown_gates[:2]
            )
            more = f" (+{len(shown_gates) - 2} more)" if len(shown_gates) > 2 else ""
            _out(f"    closes_when ({len(gates)}, {len(gates) - len(shown_gates)} done): {summary}{more}")
        else:
            _out(f"    closes_when ({len(gates)}, {len(gates) - len(shown_gates)} done hidden):"
                 if not everything else f"    closes_when ({len(gates)}):")
            for g in shown_gates:
                who = f" [{g['who']}]" if g.get("who") else ""
                _out(f"      ({g.get('state', '?'):<7}){who} {_clip(g.get('what'), 100, full)}")
                if everything and g.get("note"):
                    _out(f"          note: {_clip(g['note'], 200, True)}")

    threads = t.get("threads") or []
    if threads and everything:
        _out(f"    threads ({len(threads)}), read the ones with live discussion:")
        for th in threads:
            last = f"{th.get('last_from', '?')} @ {th.get('last_at', '?')}"
            _out(f"      - [{th.get('where', '?')}] {_clip(th.get('label'), 60, full)} | last: {last}")
            _out(f"        {th.get('url', '')}")
            if th.get("gist"):
                _out(f"        gist: {_clip(th['gist'], 200, True)}")

    events = t.get("events") or []
    if events:
        if everything:
            shown = events
        elif full:
            shown = events[-5:]
        elif _scale > 0.5:
            shown = events[-2:]
        elif _scale > 0.25:
            shown = events[-1:]
        else:
            shown = []
        if shown:
            _out(f"    events ({len(events)} total, showing {len(shown)}):")
            for e in shown:
                _out(
                    f"      - {e.get('on', '?')} {e.get('at', '')} {e.get('who', '?')}: "
                    f"{_clip(e.get('what'), 130 if full else 85, full)}"
                )
    if full and isinstance(t.get("prep"), dict) and t["prep"]:
        _prep_summary(t["prep"])


def _item_line(i: dict, full: bool) -> None:
    state = i.get("state", "todo")
    mark = {"todo": " ", "waiting": "~", "hold": "H", "done": "x", "sent": "x", "dropped": "-"}
    flag = mark.get(state, "?")
    waits = i.get("waits_on") or {}
    tail = f" -> {waits.get('who')}" if waits.get("who") else ""
    if waits.get("chase_on"):
        tail += f" (chase {waits['chase_on']})"
    if i.get("due_by"):
        tail += f" (due by {i['due_by']})"
    _out(
        f"      [{flag}] {str(i.get('id', '?')):>3}  {state:<8} {i.get('urgency', '-'):<10}"
        f" {i.get('est_minutes', '?')}m  {_clip(i.get('title'), 90, full)}{tail}"
    )
    meta = []
    if i.get("after"):
        meta.append(f"after={i['after']}")
    if i.get("at_standup"):
        meta.append("at_standup")
    if i.get("hold"):
        meta.append(f"HOLD: {_clip(i['hold'], 70, full)}")
    # Only the drafts still to send: a sent one is history, and naming it here
    # had sweeps re-reading and rewriting messages that had already gone.
    live = [d for d in B.drafts(i) if B.unsent(d)]
    gone = len(B.drafts(i)) - len(live)
    for d in live:
        meta.append(f"draft={d.get('language', '?')}->{_clip(d.get('target'), 40, full)}")
    if gone:
        meta.append(f"{gone} sent")
    p = i.get("prepared")
    if p:
        bits = [f"{len(p[k])} {k}" for k in ("findings", "unanswered", "files") if p.get(k)]
        meta.append(f"prepared({', '.join(bits) or 'conclusion only'}, built {p.get('built_at', '?')})")
    if meta:
        _out("            " + " | ".join(meta))
    # A parked waiting/monitor item needs only its header, meta and note in compact mode;
    # --item N prints it whole when its thread actually moves.
    if not full and state == "waiting" and i.get("urgency") == "monitor" and _scale <= 0.65:
        if i.get("state_note"):
            _out(f"            state_note: {_clip(i.get('state_note'), 70, False)}")
        return
    if i.get("where") and full:
        _out(f"            where: {_clip(i.get('where'), 90, full)}")
    if i.get("where_it_stands") and (full or _scale > 0.35 or state != "waiting"):
        _out(f"            stands: {_clip(i.get('where_it_stands'), 200 if full else 100, full)}")
    steps = i.get("steps") or []
    max_steps = len(steps) if full else (1 if _scale <= 0.35 and state == "waiting" else 2)
    for n, s in enumerate(steps[:max_steps], 1):
        ticked = " [ticked done by the owner]" if B.step_done(i, s) else ""
        _out(f"            step {n}{ticked}: {_clip(s, 150 if full else 80, full)}")
    if not full and len(steps) > max_steps:
        _out(f"            (+{len(steps) - max_steps} more; --item {i.get('id')})")
    if i.get("done_when") and (full or _scale > 0.35 or state != "waiting"):
        _out(f"            done_when: {_clip(i.get('done_when'), 150 if full else 80, full)}")
    if i.get("progress_note"):
        # The newest bracketed entry is the one a sweep needs; the history is
        # what --full is for.
        note = str(i["progress_note"])
        if not full and "[" in note:
            note = note[note.rfind("["):]
        _out(f"            progress: {_clip(note, 200 if full else 120, full)}")
    if i.get("state_note"):
        _out(f"            state_note: {_clip(i.get('state_note'), 120 if full else 70, full)}")


SLACK_LIVE_DAYS = 30


def _slack_ts(url: str) -> tuple[str, str] | None:
    """(channel, parent ts) from a Slack link, the two things read_thread takes.

    A reply's link carries the parent in `thread_ts`; a parent's link carries it
    in the `p` segment, with the decimal point taken out.
    """
    m = re.search(r"slack\.com/archives/([A-Z0-9]+)/p(\d{10})(\d{6})", url or "")
    if not m:
        return None
    parent = re.search(r"thread_ts=(\d{10}\.\d{6})", url)
    return m.group(1), parent.group(1) if parent else f"{m.group(2)}.{m.group(3)}"


def _slack_oldest(last_at: str, past_minute: bool = True) -> str:
    """A thread's recorded `last_at` (the owner's wall time) as a Slack ts, past the last seen message."""
    raw = str(last_at or "").strip()
    try:
        when = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=settings.tz())
    if not past_minute:
        return f"{when.timestamp() - 60:.6f}"
    # A `last_at` stored as "YYYY-MM-DD HH:MM" has minute precision, so any reply inside
    # that minute was already on the thread when the watermark was recorded; advance to
    # the end of that minute (+60s), or +1s when seconds were recorded.
    step = 60 if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", raw) else 1
    return f"{when.timestamp() + step:.6f}"


def _slack_plan(b: dict, checked: str) -> None:
    """Every live Slack thread, read every sweep, from its newest seen message on.

    Asana has a gate: a comment moves `modified_at`, so the census and the gate
    find it. Slack has none. A reply in a thread moves nothing on the board, so a
    rule that skipped threads "looked at since the ticket last moved" skipped
    them for good: the ticket only moves when a sweep sees the reply. That is how
    two replies on one job, one of them the owner's own, were once missed. So a Slack thread on a
    ticket with open work is read whenever it has spoken in the last month, with
    `oldest` set past its recorded `last_at` so a quiet one costs almost nothing.
    """
    me = settings.section("owner").get("slack_user_id", "")
    try:
        day = datetime.fromisoformat(checked).date()
    except ValueError:
        day = None
    _out("  S. Slack, in the same message as 0, 1 and M. No thread here is skippable: Slack has")
    _out("     no gate, so a reply moves nothing until a sweep reads it.")
    after = ((day - timedelta(days=1)) if day else (datetime.now().date() - timedelta(days=14))).isoformat()
    if me:
        _out(f"     search from:<@{me}> after:{after}  = everything the owner sent, so their own sends land too.")
        _out(f"     search keywords=[\"<@{me}>\"] after:{after}  = everything that mentions the owner.")
    lead_dm = settings.prioritization().get("slack_user_id", "")
    if lead_dm:
        dm_oldest = _slack_oldest(checked, past_minute=False)
        _out(f"     read_channel channel_id={lead_dm}"
             + (f" oldest={dm_oldest}" if dm_oldest else "")
             + " limit=20  = prioritization lead DM follow-ups.")
    cut = (day or datetime.now().date()) - timedelta(days=SLACK_LIVE_DAYS)
    # Key thread watermarks by (channel_id, parent_ts) across the whole board so
    # draft links and alternate Slack domains share the newest recorded `last_at`.
    lasts: dict[tuple[str, str], str] = {}
    for t in b.get("tickets", []):
        for th in t.get("threads") or []:
            k = _slack_ts(th.get("url", ""))
            la = str(th.get("last_at") or "").strip()
            if k and la and la > lasts.get(k, ""):
                lasts[k] = la
    seen: set = set()
    old: set[str] = set()
    for t in b.get("tickets", []):
        has_open = any(B.is_open(i) for i in t.get("items") or [])
        b_stage = str(((t.get("internal_ticket") or {}).get("build") or {}).get("stage") or "").strip().lower()
        in_refinement = b_stage == "refining" and not (t.get("asana") or {}).get("completed")
        if not has_open and not in_refinement:
            continue
        links = [
            th.get("url", "")
            for th in (t.get("threads") or [])
            if has_open
            or "refinement" in str(th.get("where") or "").lower()
            or "refinement" in str(th.get("label") or "").lower()
        ]
        links += [d.get("link", "") for i in t.get("items") or [] if B.is_open(i) for d in B.drafts(i)]
        for url in links:
            key = _slack_ts(url)
            if not key or key in seen:
                continue
            seen.add(key)
            last_str = lasts.get(key, "")
            last = audit.as_date(last_str) or datetime.fromtimestamp(float(key[1])).date()
            if last < cut:
                old.add(key[0])
                continue
            # From past the newest message the board has recorded, so already-seen
            # replies are not returned again.
            oldest = _slack_oldest(last_str)
            _out(f"     read_thread channel_id={key[0]} message_ts={key[1]}"
                 + (f" oldest={oldest}" if oldest else "")
                 + f"  ({t.get('ref')}, last {last_str or '?'})")
    # A thread quiet for a month is not read whole, but its channel is searched,
    # so a reply that wakes it is still found.
    for channel in sorted(old):
        _out(f"     search in:<#{channel}> after:{after}  = wakes any thread here quiet over {SLACK_LIVE_DAYS} days")
    _out("     A reply in any of these is an event; the owner's own message is an event \"You\" (section 5).")
    _out("     Put each read thread's newest message in the patch's \"threads\" (last_at, last_from).")


def _sweep_plan(b: dict) -> None:
    """The worklist for a sweep, made from the watermarks rather than reasoned out.

    A sweep used to work out what to read by dumping the board through a dozen
    one-liners: which tickets, which threads, from when. All of that is already on
    the board, so this prints it as instructions the sweep can follow without
    opening `state/board.json` at all. Read exactly what this names, from the
    timestamps it names, and nothing turns into reconnaissance.
    """
    checked = b.get("checked_at", "")
    since_utc = _asana_since(checked)
    gids = _project_gids()
    _out("SWEEP PLAN  (built from the watermarks; read this, not the raw board)")
    proj = f" projects_any={','.join(gids)}" if gids else ""
    _out("  Make calls 0, 1, M and every S together, in ONE message: they do not depend on each other.")
    _out(f"  0. Census, NO date filter: search_tasks assignee_any=me completed=false{proj}"
         " opt_fields=name,completed,assignee.name,memberships.section.name,custom_fields.name,custom_fields.display_value")
    _out("     = what BELONGS on the board. An assigned incomplete gid not below is MISSING: add it, deep-read it.")
    _out(f"  1. Gate: search_tasks modified_at_after={since_utc}{proj}"
         " opt_fields=name,modified_at,assignee.name,completed")
    _out(f"     = tickets modified since {checked or 'last sweep'}. Deep-read ONLY its gids, census additions,")
    _out(f"     and tickets whose thread moved in S/2: batch stories in ONE call with")
    _out(f"     get_task_stories task_ids=[<moved gids>] created_after={since_utc}")
    _out("     Nothing else; an unchanged known ticket costs zero get_task / get_task_stories calls.")
    for ms in b.get("milestones") or []:
        if ms.get("status") != "done":
            _out(f"  M. Milestone {ms.get('id')}: search_tasks text=\"{ms.get('name_ja') or ms.get('name')}\""
                 f" completed=false{proj} modified_at_after={since_utc}"
                 " opt_fields=name,assignee.name,modified_at,completed,custom_fields.name,custom_fields.display_value")
            _out("     = its child tickets that moved. Update the milestone's tickets/phases rows; a child assigned to")
            _out("     the owner becomes a ticket card too (new_tickets), anything else stays a row on the milestone.")
    _slack_plan(b, checked)
    _out("  2. Other threads to read (not looked at since the ticket last moved); mark them in the patch's \"checked\".")
    quiet_refs: list[str] = []
    for t in b.get("tickets", []):
        threads = [th for th in t.get("threads") or [] if not _slack_ts(th.get("url", ""))]
        if not threads:
            continue
        events = t.get("events") or []
        newest = max(
            (audit.as_date(e.get("on")) for e in events if audit.as_date(e.get("on"))),
            default=None,
        )
        need, quiet = [], 0
        for th in threads:
            looked = max(
                (d for d in (audit.as_date(th.get("checked")), audit.as_date(th.get("last_at"))) if d),
                default=None,
            )
            if newest is not None and looked is not None and looked >= newest:
                quiet += 1
            else:
                need.append(th)
        if not need:
            quiet_refs.append(t.get("ref", "?"))
            continue
        _out(f"     {t.get('ref')}:" + (f"  ({quiet} quiet, skip)" if quiet else ""))
        for th in need:
            _out(
                f"       - [{th.get('where', '?')}] from {th.get('last_at', '?')}"
                f"  {th.get('url', '')}"
            )
    if quiet_refs:
        _out(f"     every thread looked at since the last event, skip: {' '.join(quiet_refs)}")
    builds = [
        f"{bld['kt']}({t.get('ref')}:{bld.get('stage', '?')})"
        for t in b.get("tickets", [])
        for bld in [(t.get("internal_ticket") or {}).get("build") or {}]
        if bld.get("kt")
    ]
    _out("  3. Build tickets (re-read only if P's modified_at_after query or a thread flags them): "
         + (" ".join(builds) if builds else "none"))
    # Worked out from the board as it stands; the sweep re-checks each against
    # the stage it reads now, since a build moved in P changes the answer.
    behind = [(t, B.status_from_build(t)) for t in b.get("tickets", [])]
    behind = [(t, want) for t, want in behind if want and not B.status_behind(t)]
    if behind:
        _out("     Status behind the build (refresh.md, \"Status follows the build\"): write")
        _out("     asana.status_should + status_why, and the started-work comment where it applies.")
        for t, want in behind:
            bld = (t.get("internal_ticket") or {}).get("build") or {}
            _out(f"       - {t.get('ref')}: Asana {(t.get('asana') or {}).get('status') or '-'}, "
                 f"{bld.get('kt')} {bld.get('stage')} -> {want}")
    # Sent, marked by the owner's own press since the last sweep. The mark stands; the
    # sweep only confirms it against the thread, the way a read receipt would.
    marked = []
    for t in b.get("tickets", []):
        for i in t.get("items") or []:
            last = (i.get("history") or [{}])[-1]
            if (i.get("state") == "waiting" and i.get("sent_by_you")
                    and last.get("state") == "waiting" and str(last.get("at", "")) > checked):
                marked.append((t, i, last))
    if marked:
        _out("  4. Marked sent since the last sweep: find the owner's message in the thread (it is in")
        _out("     2's reads, or the draft link). Answered: item back to todo, per section 6.")
        _out("     Not found: say so in the item's state \"note\", never move it back yourself.")
        for t, i, last in marked:
            link = next((d.get("link") for d in B.drafts(i) if d.get("link")), "")
            _out(f"       - {i.get('id')} ({t.get('ref')}) -> {(i.get('waits_on') or {}).get('who', '?')}"
                 f" at {str(last.get('at', ''))[:16]}  {link}")
    # Meetings that ended with Gemini notes attached and no note record on the
    # board yet, read from the calendar the desk keeps. This is how a room the
    # owner sat in reaches the board without them pasting anything.
    _prioritization_plan(b)
    taken = _standup_plan(b)
    unread = [e for e in calendar_sync.unread_notes(b)
              if not {n["url"] for n in e.get("notes") or []} & taken]
    if unread:
        _out("  N. Meeting notes nobody has read: drive_read_file each doc, in the same message as 0/1/S.")
        _out("     Fold what was said into the tickets it touches (events, items), then add one")
        _out("     other_notes row per meeting {date, title, url, found, gist} (board \"other_notes+\"), or set")
        _out("     migration_note for one of the programme-wide meetings in config.json meeting.also.")
        _out("     A meeting with nothing for the owner's tickets still gets its row, gist")
        _out("     \"nothing for the owner's tickets\", so it stops showing.")
        for e in unread[:8]:
            doc = next((n for n in e.get("notes") or [] if calendar_sync.GEMINI.search(n.get("title", ""))), {})
            _out(f"       - {e['date']} {e['at']} {e['title'][:60]}  {doc.get('url', '')}")
    _out()


def _standup_plan(b: dict) -> set[str]:
    """Step D: the main standup has run and `meeting_note` has not caught up.

    The post run is meant to fold it in, but it can fail or still be polling,
    and a refresh after the standup that leaves it out shows the owner a
    morning-old page. Returns the doc links this step covers, so step N does
    not offer the same doc again as a programme meeting.
    """
    st = calendar_sync.standup_notes(b)
    if not st:
        return set()
    note = b.get("meeting_note") or {}
    was = note.get("date") or "never"
    _out(f"  D. The standup ({st['title'][:50]}, {st['date']} {st['at']}) is not on the board yet:")
    _out(f"     meeting_note is from {was}. refresh.md \"The standup\" says how to fold it in.")
    if not st["ready"]:
        _out(f"     Its Meet call runs until {st['until']} and Gemini writes the notes when it ends,")
        _out("     so do not read them yet and leave meeting_note alone. A refresh after that folds it in.")
        return {d["url"] for d in st["docs"]}
    if st["docs"]:
        if st["shared"]:
            _out(f"     It shares one Meet call with {', '.join(t[:40] for t in st['shared'])}, so")
            _out("     the Gemini doc on that event is the standup's too:")
        for d in st["docs"]:
            _out(f"       - {d['at']} {d['on'][:40]}  {d['url']}")
        _out("     Set meeting_note {date, found, url, gist} to that doc.")
        if st["shared"]:
            _out("     It covers the shared room as well: set migration_note to the same url and date.")
    else:
        _out("     No Gemini doc on the calendar for it. Look where the profile's rooms section says the")
        _out(f"     notes land (Notion query-meeting-notes for \"{settings.notes_title()}\" on {st['date']}).")
        _out("     Nothing there: leave meeting_note alone and name it in gaps.")
    return {d["url"] for d in st["docs"]}


def _prioritization_plan(b: dict) -> None:
    """Step P: the owner's CE builds, gated by `modified_at_after` from `prioritization.checked_at`.

    Only when `config.json` has a `prioritization` block; with none, step P is
    not printed at all. Asana ignores a CDL-Owner filter on search (it once came
    back with every CDL's builds), and the CE projects read whole are 120k
    characters. So one `modified_at_after` search across `ce_projects` finds
    both new builds and any known build that moved since `prioritization.checked_at`.
    CDL-Owner is the only rule: another CDL's build never goes in, however close
    to the owner's tickets it sits.
    """
    if not settings.prioritization():
        return
    cfg = prioritize.config()
    if not cfg:
        return
    block = b.get("prioritization") or {}
    nxt = prioritize.next_round(cfg)
    p_since_utc = _asana_since(block.get("checked_at") or b.get("checked_at") or "")
    _out(f"  P. Prioritization: next thread {nxt.get('date', '?')} {nxt.get('at', '')},"
         f" raise by {nxt.get('ask_by', '?')}. In the same message as 0/1/S:")
    gids = [x.get("gid") for x in block.get("builds") or [] if x.get("gid")]
    _out(f"     search_tasks projects_any={','.join(cfg.get('ce_projects') or [])} modified_at_after={p_since_utc}"
         " opt_fields=name,due_on,completed,modified_at,custom_fields.gid,custom_fields.name,custom_fields.display_value")
    owner = settings.owner_name() or "the owner (config.json owner.name)"
    _out(f"     Known build gids (see PRIORITIZATION below): {' '.join(gids) or 'none'}.")
    _out(f"     Only call get_task(include_comments=false) on a known gid if the search returned it or it moved.")
    _out(f"     Keep only CDL-Owner ({cfg.get('cdl_owner_field')}) = {owner}. Update changed rows in")
    _out("     \"prioritization.builds\" (section 7c) and set \"prioritization.checked_at\" = now.")
    ran = prioritize.previous(cfg).get("date") or ""
    if ran and (block.get("last_round") or {}).get("date", "") < ran:
        _out(f"     A round has run: read {cfg.get('who')}'s thread in {cfg.get('channel')} ({cfg.get('channel_id')})")
        _out("     and set prioritization.last_round {date, url, ordered, not_pushed} for the owner's builds only.")


CHEATSHEET = """WRITE  (the whole of a sweep's writing -- no python, no help(board), no board.json dump)

Write ONE patch file with the Write tool, then apply it with ONE command:

  Write  tmp/patch.json           (inside this folder, outside state/, .json never .py)
  ./core/apply.py tmp/patch.json  (add --dry first only if unsure; a clean return is the confirmation)

  {"events":  [{"ticket": "HOLD-RELEASE", "on": "2026-10-07", "at": "12:08", "who": "Alex Kim, Kraken CE",
                "what": "...", "so_what": "...", "where": "Slack", "source_url": "https://..."}],
   "checked": {"HOLD-RELEASE": "*", "INVOICE-REPRINT": ["slack"]},   threads looked at (all, or url contains)
   "threads": [{"ticket": "...", "url": "...", "last_at": "2026-10-07 12:08", "last_from": "Alex"}],
   "items":   {"32": {"state": "todo", "note": "Alex replied 12:08",            moves like ./core/tick.py
                      "draft.body_ruby": "...", "progress_note": "...", "steps": ["..."]},
                      draft.reply_from / draft.expects_reply: false say what Posted it does;
                      never a "./core/tick.py N" step, the page has a button for it
               "45": {"state": "waiting", "who": "Sato-san, client", "chase_on": "2026-10-09"}},
   "new_items": [{"ticket": "...", "title": "...", "why": "...", "steps": ["..."],
                  "done_when": "...", "urgency": "today"}],        numbered for you
   "new_tickets": [{"id": "1200...", "ref": "SHORT-TAG", "title_ja": "...", "title_en": "...",
                    "asana_url": "https://app.asana.com/0/.../...",
                    "asana": {"status": "...", "section": "...", "assignee": "...", "completed": false},
                    "where_it_stands": "...", "closes_when": [{"what": "...", "done": false}]}],
   "tickets": {"HOLD-RELEASE": {"where_it_stands": "...", "asana.status": "...", "closes_when": [...]}},
   "board":   {"news": [...whole list, five or so...], "brief": {...}, "alert": null,
               "milestones.price-update.phases.1.state": "done",
               "milestones.price-update.items+": [58]},
   "glossary": {"<client term>": "open the day after close"},
   "swept": true}                                             sets checked_at = now, once, last

  A dotted key reaches inside; "key+" appends to a list; null removes. A list of records with
  ids (milestones) is addressed by id, any other list by position. Every key is optional.
  apply.py saves through board.save, which locks, merges with an ask that wrote meanwhile,
  keeps prepared.earlier for you, and refuses a shape no renderer survives. So: never
  Edit/sed/json.dump board.json, never delete an item (state "dropped" with a note).

EVERY JOB NEEDS A DAY: urgency is exactly today | this-week | monitor (hyphen), or
  waits_on.chase_on is an ISO date (a condition goes in waits_on.what). The owner's
  job with a deadline ("by Fri 6 Nov") also carries due_by, an ISO date.

NEED A RECORD WHOLE (a stale prepared block, a draft to rewrite)?  ./core/digest.py --item 42 52
  or --ticket HOLD-RELEASE prints it as JSON, which is also its shape. Never read output/ or *.py for it.
SHAPES (all a sweep writes; no need to look further):
  draft    = {language: "ja"|"en", target: "who and where", link, body_ruby: ja text with {word|reading},
              body_en: en text (or the translation under a ja one)}        null removes it
  prepared = {conclusion, unanswered:[...], findings:[<=4], files:[{path, label, draft: N}],
              sources:[<=3 {label, url}], built_at}   rewrite whole; save() keeps what you cut.
              The card shows conclusion + unanswered; the rest folds as working notes, so
              write only what the owner acts on or sends. files = only what goes out or is
              opened, each tagged with the draft it is attached to.
  news row = {topic, what, why, on, source_url}       why = the route to one of the owner's tickets
  brief    = {for_date, built_at, lines:[{kind: moved|do|watch|room|big, what, items:[n], ref, source_url, at}]}

Do not rebuild: the runner redraws both pages when the run ends."""


def _whole(b: dict, items: list[str], tickets: list[str]) -> int:
    """One record exactly as the board holds it, which is also its shape.

    A sweep fixing a stale `prepared` block needs that block whole, and without
    this it went to output/desk.md and board.py with grep and awk for it: five
    turns where one call does. The bookkeeping (`history`, `earlier`) stays out,
    because nothing a sweep writes needs it and it is most of the bytes.
    """
    skip = {"history", "earlier"}

    def trim(node):
        if isinstance(node, dict):
            return {k: trim(v) for k, v in node.items() if k not in skip}
        if isinstance(node, list):
            return [trim(v) for v in node]
        return node

    for ident in items:
        t, item = B.by_id(b, ident)
        if item is None:
            _out(f"no item numbered {ident}")
            continue
        _out(f"ITEM {ident} on {t.get('ref')}:")
        _out(json.dumps(trim(item), ensure_ascii=False, indent=1))
    for ref in tickets:
        t = next((x for x in b.get("tickets", []) if x.get("ref") == ref or str(x.get("id")) == ref), None)
        if t is None:
            _out(f"no ticket {ref}")
            continue
        body = {k: v for k, v in t.items() if k not in ("items", "events")}
        _out(f"TICKET {ref} (items and events left out; --item N for an item):")
        _out(json.dumps(trim(body), ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--full", action="store_true",
                    help="no truncation, for the tickets with open work")
    ap.add_argument("--everything", action="store_true",
                    help="--full, plus closed items and finished tickets")
    ap.add_argument("--items-only", action="store_true", help="just the work, no ticket context")
    ap.add_argument("--item", nargs="+", metavar="N",
                    help="print these items whole, as JSON: prepared, draft and all")
    ap.add_argument("--ticket", nargs="+", metavar="REF",
                    help="print these tickets whole, as JSON, without their items")
    ap.add_argument("--delta", nargs="?", const="auto", metavar="PATH",
                    help="compact delta view from scout.py's delta.json (expands only moved/audit tickets)")
    args = ap.parse_args()
    full = args.full or args.everything

    global _scale
    b = B.load()
    if args.item or args.ticket:
        code = _whole(b, args.item or [], args.ticket or [])
        print("\n".join(_lines))
        return code
    delta_data: dict | None = None
    if args.delta:
        dpath = settings.TMP_DIR / "delta.json" if args.delta == "auto" else Path(args.delta)
        if not dpath.exists() and (settings.TMP_DIR / dpath.name).exists():
            dpath = settings.TMP_DIR / dpath.name
        if dpath.exists():
            try:
                delta_data = json.loads(dpath.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                delta_data = None
    # --full is meant to be long; only the default print is held to the budget.
    for _scale in SCALES if not full else (1.0,):
        _lines.clear()
        code = _board(b, args, full, delta_data)
        if sum(len(x.encode("utf-8")) + 1 for x in _lines) <= BUDGET:
            break
    print("\n".join(_lines))
    return code


def _brief_and_rollups(b: dict, full: bool) -> None:
    """Compact summaries of `brief`, `script`, `huddle`, and `xws` so runs never grep for them."""
    brief = b.get("brief") or {}
    if brief:
        b_lines = brief.get("lines") or []
        _out(
            f"BRIEF (for_date={brief.get('for_date', '-')}, built_at={brief.get('built_at', '-')},"
            f" {len(b_lines)} line(s)):"
        )
        for ln in b_lines:
            if isinstance(ln, dict):
                items_str = f" items={ln.get('items')}" if ln.get("items") else ""
                ref_str = f" ref={ln.get('ref')}" if ln.get("ref") else ""
                _out(f"  [{ln.get('kind', '?')}]{ref_str}{items_str} {_clip(ln.get('what'), 160, full)}")
        _out()

    script = b.get("script") or {}
    if script:
        _out(
            f"SCRIPT: for_date={script.get('for_date', '-')} at={script.get('at', '-')}"
            f" built_at={script.get('built_at', '-')}"
            + (f" | {_clip(script.get('headline'), 140, full)}" if script.get("headline") else "")
        )

    huddle = b.get("huddle") or {}
    if huddle:
        h_ms = huddle.get("milestones") or []
        h_bl = huddle.get("blockers") or []
        h_pl = huddle.get("plate") or []
        _out(
            f"HUDDLE: for_date={huddle.get('for_date', '-')} at={huddle.get('at', '-')}"
            f" built_at={huddle.get('built_at', '-')}"
            f" ({len(h_ms)} milestones, {len(h_bl)} blockers, {len(h_pl)} plate)"
        )
        if full:
            for hm in h_ms:
                if isinstance(hm, dict):
                    _out(
                        f"  milestone {hm.get('id', '?')}: {hm.get('status', '?')}"
                        f" ({hm.get('confidence', '?')}) {_clip(hm.get('update'), 140, True)}"
                    )
            for hb in h_bl:
                if isinstance(hb, dict):
                    _out(f"  blocker: {_clip(hb.get('blocker'), 140, True)}")

    xws = b.get("xws") or {}
    if xws:
        raised = xws.get("raised") or []
        _out(
            f"XWS: for_date={xws.get('for_date', '-')} at={xws.get('at', '-')}"
            f" built_at={xws.get('built_at', '-')} ({len(raised)} raised)"
            + (f" | {_clip(xws.get('headline'), 120, full)}" if xws.get("headline") else "")
        )
    if script or huddle or xws:
        _out()


def _delta_plan(b: dict, delta: dict) -> set[str]:
    """Print pre-fetched Stage 1 delta findings and return ticket refs to expand in full."""
    expand: set[str] = set(str(r) for r in (delta.get("touched_tickets") or []) if r)
    since_utc = delta.get("stories_since_utc") or _asana_since(b.get("checked_at", ""))

    _out("DELTA FINDINGS  (pre-fetched by scout.py across all Asana & Slack sources)")
    _out("  Do NOT re-run the Asana census, Slack thread reads, or Slack searches already shown below.")
    _out("  Only call get_task_stories / get_task / drive_read_file for the items listed under TO FETCH.")

    story_gids: list[str] = []
    full_task_gids: list[str] = []

    if delta.get("census_added"):
        _out(f"  + NEW ASSIGNED TICKETS ({len(delta['census_added'])}):")
        for row in delta["census_added"]:
            gid = str(row.get("gid") or "")
            full_task_gids.append(gid)
            story_gids.append(gid)
            _out(
                f"    - gid={gid} status={row.get('status') or '-'} section={row.get('section') or '-'}"
                f" | {row.get('name', '')}"
            )

    if delta.get("census_missing"):
        _out(f"  ! BOARD TICKETS ABSENT FROM CENSUS ({len(delta['census_missing'])}; completed or reassigned):")
        for row in delta["census_missing"]:
            gid = str(row.get("gid") or "")
            if row.get("ref"):
                expand.add(str(row["ref"]))
            full_task_gids.append(gid)
            story_gids.append(gid)
            _out(
                f"    - {row.get('ref')} (gid={gid}, was status={row.get('was_status')},"
                f" assignee={row.get('was_assignee')}): call get_task + get_task_stories to check"
            )

    if delta.get("census_field_diffs"):
        _out(f"  ~ ASANA FIELD CHANGES ({len(delta['census_field_diffs'])}; write into patch 'tickets'):")
        for row in delta["census_field_diffs"]:
            ref = str(row.get("ref") or "")
            gid = str(row.get("gid") or "")
            if ref:
                expand.add(ref)
            story_gids.append(gid)
            changes = ", ".join(
                f"{k}: {v.get('board')!r} -> {v.get('asana')!r}"
                for k, v in (row.get("diffs") or {}).items()
            )
            _out(f"    - {ref} (gid={gid}): {changes}")

    if delta.get("moved_asana"):
        _out(f"  * ASANA TASKS MODIFIED SINCE LAST SWEEP ({len(delta['moved_asana'])}):")
        for row in delta["moved_asana"]:
            gid = str(row.get("gid") or "")
            ref = str(row.get("ref") or "")
            if ref and not ref.startswith("milestone:") and ref != "news/assigned":
                expand.add(ref)
            story_gids.append(gid)
            _out(f"    - {ref or gid} (gid={gid}, modified_at={row.get('modified_at')}): {row.get('name', '')[:80]}")

    if delta.get("moved_builds"):
        _out(f"  * CE / INTERNAL BUILDS MODIFIED ({len(delta['moved_builds'])}):")
        for row in delta["moved_builds"]:
            gid = str(row.get("gid") or "")
            ref = str(row.get("ticket") or "")
            if ref:
                expand.add(ref)
            story_gids.append(gid)
            fields_json = json.dumps(row.get("fields") or {}, ensure_ascii=False)
            _out(
                f"    - {row.get('kt') or gid} (gid={gid}, ticket={ref or '-'},"
                f" completed={row.get('completed')}): {row.get('name', '')[:70]}"
            )
            _out(f"      fields: {fields_json[:300]}")

    dedup_stories = list(dict.fromkeys(g for g in story_gids if g))
    dedup_tasks = list(dict.fromkeys(g for g in full_task_gids if g))
    if dedup_stories or dedup_tasks or delta.get("standup_notes") or delta.get("unread_notes"):
        _out("  TO FETCH IN ONE PARALLEL TURN:")
        if dedup_tasks:
            _out(f"    - get_task for new/missing gids: {', '.join(dedup_tasks)}")
        if dedup_stories:
            _out(f"    - get_task_stories task_ids={json.dumps(dedup_stories)} created_after={since_utc}")
        st = delta.get("standup_notes")
        if isinstance(st, dict) and st.get("docs"):
            for d in st["docs"]:
                _out(f"    - drive_read_file (standup notes {st.get('date')}): {d.get('url', '')}")
        for e in delta.get("unread_notes") or []:
            for n in e.get("notes") or []:
                if calendar_sync.GEMINI.search(n.get("title", "")):
                    _out(f"    - drive_read_file (meeting {e.get('date')} {e.get('title', '')[:45]}): {n.get('url', '')}")

    if delta.get("slack_replies"):
        _out(f"  * NEW SLACK THREAD REPLIES ({len(delta['slack_replies'])}; already fetched, no need to re-read):")
        for r in delta["slack_replies"]:
            if r.get("ticket"):
                expand.add(str(r["ticket"]))
            _out(f"    --- {r.get('ticket', '?')} | {r.get('url', '')} ---")
            for ln in str(r.get("messages") or "").splitlines():
                _out(f"      {ln}")

    if delta.get("slack_messages"):
        _out(f"  * NEW SLACK MESSAGES / SENDS / MENTIONS ({len(delta['slack_messages'])}; already fetched):")
        known_refs = [str(t.get("ref") or "") for t in b.get("tickets") or [] if t.get("ref")]
        for m in delta["slack_messages"]:
            txt = str(m.get("text") or "")
            for r in known_refs:
                if r and r.lower() in txt.lower():
                    expand.add(r)
            _out(
                f"    --- [{m.get('source')}] {m.get('channel') or m.get('channel_id')}"
                f" | from {m.get('from') or '?'} at {m.get('time') or m.get('ts')}"
                f" | {m.get('url', '')} ---"
            )
            for ln in txt.splitlines():
                _out(f"      {ln}")

    if delta.get("canvas_changes"):
        _out(f"  * SLACK CANVAS UPDATES ({len(delta['canvas_changes'])}):")
        for c in delta["canvas_changes"]:
            for r in c.get("tickets") or []:
                expand.add(str(r))
            _out(f"    - canvas {c.get('canvas_id')} (tickets: {', '.join(c.get('tickets') or [])}):")
            for ln in (c.get("matching_lines") or [])[:15]:
                _out(f"      {ln}")

    if delta.get("brief_missing"):
        _out("  * MORNING BRIEF: brief.for_date is not today yet; write today's brief in this patch.")

    prep_sync = delta.get("pre_room_prep_sync")
    if isinstance(prep_sync, dict) and prep_sync.get("active"):
        _out()
        _out(
            f"  !! PRE-ROOM SCRIPT SYNC ACTIVE: {prep_sync.get('room')} is in {prep_sync.get('mins_until')}m"
            f" ({prep_sync.get('date')} {prep_sync.get('at')}, kind={prep_sync.get('kind')}) and today's"
            " script is already built."
        )
        _out(
            "     For ONLY the ticket(s) whose state, timeline, or asks changed in this delta, also"
            " update `tickets.<ref>.prep` (or set `prep: null` if completed/settled) in the house"
            " style with `{word|reading}` readings when `language.readings` is true."
        )
        _out("     Leave unchanged tickets' `prep` blocks untouched.")

    _out()
    return expand


def _board(b: dict, args: argparse.Namespace, full: bool, delta: dict | None = None) -> int:
    all_items = B.items(b)
    open_items = [(t, i) for t, i in all_items if B.is_open(i)]
    by_state: dict[str, int] = {}
    for _, i in all_items:
        by_state[i.get("state", "todo")] = by_state.get(i.get("state", "todo"), 0) + 1

    _out("BOARD DIGEST  (state/board.json, read-only)")
    _out(f"checked_at: {b.get('checked_at', 'never')}  ({_age(b.get('checked_at', ''))})")
    seen_info = scout.load_seen()
    s_clean = str(seen_info.get("verified_clean_at") or "")
    if s_clean and s_clean > str(b.get("checked_at") or ""):
        _out(f"scout_verified_clean_at: {s_clean}  ({_age(s_clean)}, zero changes in Asana/Slack)")
    _out(f"next_id: {b.get('next_id')}   version: {b.get('version')}")
    _out(
        f"counts: {len(b.get('tickets', []))} tickets, {len(all_items)} items"
        f" ({len(open_items)} open) | " + ", ".join(f"{k}={v}" for k, v in sorted(by_state.items()))
    )
    id_counts = Counter(str(i.get("id")) for _, i in all_items)
    dupes = [ident for ident, n in id_counts.items() if n > 1]
    if dupes:
        _out(f"!! DUPLICATE ITEM NUMBERS: {sorted(dupes)} -- fix before writing")
    _out()

    # What the last sweep left behind, printed before anything else it might read.
    rep = audit.Report()
    for check in audit.CHECKS + plugins.audit_checks():
        check(b, rep)
    audit_refs: set[str] = set()
    if rep.total:
        _out(f"OUT OF DATE ({rep.total}) -- fix these in this sweep, they are not warnings")
        known_refs = [str(t.get("ref") or "") for t in b.get("tickets") or [] if t.get("ref")]
        for group, lines in rep.groups.items():
            _out(f"  {group}:")
            for line in lines:
                _out("  " + line)
                for r in known_refs:
                    if r and r in line:
                        audit_refs.add(r)
        _out("  Run ./core/audit.py when you are done. It should print nothing.")
        _out()

    delta_refs: set[str] = set()
    if not args.items_only:
        if delta is not None:
            delta_refs = _delta_plan(b, delta) | audit_refs
        else:
            _sweep_plan(b)

    if not args.items_only:
        sessions = b.get("sessions") or []
        script = b.get("script") or {}
        _out(f"SESSIONS ({len(sessions)}, soonest first)   script.for_date={script.get('for_date', '-')}")
        for s in sessions:
            skip = "  SKIPPED: " + str(s.get("reason", "")) if s.get("skipped") else ""
            _out(
                f"  {s.get('date', '?')} {s.get('at', ''):<6} {s.get('kind', '?'):<9}"
                f" {_clip(s.get('label') or s.get('title'), 70, full)}{skip}"
            )
            for row in s.get("timetable") or []:
                if row.get("mine") or full:
                    star = "*" if row.get("mine") else " "
                    _out(f"      {star} {row.get('at', '?')} {_clip(row.get('what'), 100, full)}")
        _out()

        _brief_and_rollups(b, full)

        alert = b.get("alert") or {}
        if alert.get("what"):
            _out(f"ALERT: {_clip(alert['what'], 300, full)}")
            _out(f"       {alert.get('source_url', '')}")
            _out()

        news = b.get("news") or []
        _out(f"NEWS ({len(news)})")
        for n in news:
            _out(f"  - {n.get('on', '?')} {n.get('topic', '')}")
            _out(f"    {_clip(n.get('what'), 240 if full else 90, full)}")
            if n.get("why"):
                _out(f"    reaches you: {_clip(n['why'], 200 if full else 70, full)}")
        _out()

        for key in ("meeting_note", "migration_note"):
            note = b.get(key) or {}
            if note:
                _out(
                    f"{key}: date={note.get('date', '-')} found={note.get('found', '-')}"
                    f" {note.get('url', '')}"
                )
        others = b.get("other_notes") or []
        for note in others if full else others[-2:]:
            _out(f"other_note: {note.get('date', '-')} {_clip(note.get('title'), 60, full)}")
        if len(others) > 2 and not full:
            _out(f"other_note: +{len(others) - 2} older (--full)")
        _out(f"glossary: {len(b.get('glossary') or {})} terms")
        _out()

        for ms in b.get("milestones") or []:
            phases = ms.get("phases") or []
            _out(f"MILESTONE {ms.get('id')}: {ms.get('name')} target={ms.get('target')}"
                 f" status={ms.get('status')} items={ms.get('items') or []}")
            done = [n for n, ph in enumerate(phases) if ph.get("state") == "done"]
            if done and not full:
                _out(f"  phases done: {', '.join(map(str, done))}")
            for n, ph in enumerate(phases, 1):
                if n - 1 in done and not full:
                    continue
                _out(f"  phases.{n - 1} ({ph.get('state', 'next'):<7}) {_clip(ph.get('what'), 90, full)}"
                     + (f" [{ph['who']}]" if ph.get("who") else ""))
            unassigned_rows = []
            for n, r in enumerate(ms.get("tickets") or []):
                gid = str(r.get("asana_url") or "").rstrip("/").rsplit("/", 1)[-1]
                assignee = r.get("assignee") or "none"
                if (delta is not None or (not full and _scale <= 0.35)) and not r.get("ref") and assignee in ("none", "unassigned"):
                    unassigned_rows.append(f"{n}:{gid or 'none'}")
                    continue
                _out(f"  tickets.{n} gid={gid or 'none'} ref={r.get('ref') or 'none'}"
                     f" assignee={assignee} state={_clip(r.get('state') or '?', 90, full)}"
                     f" | {_clip(r.get('title'), 60, full)}")
            if unassigned_rows:
                _out(f"  tickets unassigned ({len(unassigned_rows)}, index:gid): {' '.join(unassigned_rows)}")
        if b.get("milestones"):
            _out()

        prio = b.get("prioritization") or {}
        if settings.prioritization() and prio:
            p_builds = prio.get("builds") or []
            _out(f"PRIORITIZATION (checked_at={prio.get('checked_at', '-')}, {len(p_builds)} builds;"
                 " address as prioritization.builds.<n>.<field>):")
            for n, pb in enumerate(p_builds):
                if not isinstance(pb, dict):
                    continue
                _out(
                    f"  builds.{n} kt={pb.get('kt', '?')} gid={pb.get('gid', '?')}"
                    f" ticket={pb.get('ticket') or '-'} status={pb.get('status') or '-'}"
                    f" ref={pb.get('refinement') or '-'} size={pb.get('size') or '-'}"
                    f" order={pb.get('client_order') or '-'} wave={pb.get('wave') or '-'}"
                    f" squad={pb.get('squad') or '-'} due={pb.get('due_on') or '-'}"
                    f" eng={pb.get('engineer') or '-'}"
                )
                if full or delta is None:
                    _out(f"    name: {_clip(pb.get('name'), 110, full)} | why: {_clip(pb.get('why'), 140, full)}")
                if full and pb.get("url"):
                    _out(f"    url: {pb['url']}")
            _out()

    _out("TICKETS AND WORK")
    _out()
    quiet: list[str] = []
    unchanged_open: list[str] = []
    aliases = settings.owner_aliases()
    prep_sync_on = bool(delta and isinstance(delta.get("pre_room_prep_sync"), dict) and delta["pre_room_prep_sync"].get("active"))
    for t in b.get("tickets", []):
        ref = str(t.get("ref") or t.get("id") or "?")
        asana = t.get("asana") or {}
        open_t_items = [i for i in t.get("items", []) if B.is_open(i)]
        has_open = bool(open_t_items)
        mine_open = (
            not asana.get("completed")
            and str(asana.get("assignee") or "").strip().lower() in aliases
        )
        if not args.everything and not has_open and not (full and (mine_open or t.get("prep"))) and ref not in delta_refs:
            status = asana.get("status", "?")
            quiet.append(f"{ref} {t.get('id', '?')} ({status})")
            continue
        if delta is not None and ref not in delta_refs and not args.everything:
            item_bits = ", ".join(
                f"#{i.get('id')} {i.get('state', 'todo')}({i.get('urgency', '-')}"
                + (f", due {i['due_by']}" if i.get("due_by") else "")
                + (f", after={i['after']}" if i.get("after") else "")
                + f") \"{_clip(i.get('title'), 48, False)}\""
                for i in open_t_items
            )
            unchanged_open.append(
                f"  - {ref} (gid={t.get('id', '?')}, status={asana.get('status', '?')}): "
                + (item_bits or "no open items")
            )
            continue
        if not args.items_only:
            _ticket_head(t, full or (delta is not None), args.everything)
            if prep_sync_on and not full and isinstance(t.get("prep"), dict) and t["prep"]:
                _prep_summary(t["prep"])
        else:
            _out(f"=== {ref} ===")
        rows = [i for i in t.get("items", []) if args.everything or B.is_open(i)]
        closed = len(t.get("items", [])) - len(rows)
        _out(f"    items ({len(rows)} shown{'' if args.everything else f', {closed} closed hidden'}):")
        for i in rows:
            _item_line(i, full or (delta is not None))
        _out()

    if unchanged_open:
        _out(f"UNCHANGED OPEN TICKETS ({len(unchanged_open)}; use `./core/digest.py --ticket REF` or `--item N` if needed):")
        for ln in unchanged_open:
            _out(ln)
        _out()

    if quiet:
        _out("NOTHING OPEN (one line each; --everything prints them): " + "; ".join(quiet))
        _out()
    _out(CHEATSHEET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
