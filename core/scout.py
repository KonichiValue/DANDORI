#!/usr/bin/env python3
"""Stage 1 of auto-delta refresh: check every Asana and Slack watermark cheaply.

A full refresh runs Opus across ~37 tool calls and ~100k tokens even when
nothing in Asana or Slack has moved. Separating *detection* from *reasoning*
fixes both the wait and the cost without skipping a single source:

1. This module runs Haiku 4.5 with read-only Asana and Slack MCP tools, no
   project prompt (`--setting-sources user`), no file/bash built-ins, and
   `compact.py` stripping noise. All independent checks go out in one parallel
   turn, and oversized responses saved to disk are parsed here in Python so the
   model never reads them (~1.5 cents, ~20 seconds).
2. Python diffs the exact responses against `state/board.json` and
   `state/scout-seen.json`:
   - Asana unfiltered census (new assigned tasks, missing/reassigned/completed
     board tasks, or Status/section/priority changes).
   - Asana `modified_at_after` gate on shared projects, milestone searches,
     owned CE builds, and internal build tickets.
   - Every live Slack thread (`oldest` past `last_at`), old-thread channel
     wakes, prioritization lead DM, the owner's own sends (`from:<@me>`),
     mentions (`<@me>`), and Slack canvases referenced on open `todo` items.
   - Unread Gemini meeting notes from `calendar_sync.py`, and missing morning
     brief on a new working day.
3. When nothing moved, it updates `state/scout-seen.json` (kept out of
   `render.SOURCES`, so the open page never reloads) and finishes quietly.
   When something moved, it writes `tmp/delta.json` with the exact pre-fetched
   Slack messages, census diffs, and moved Asana GIDs so Stage 2 (Opus 5.5
   delta refresh) only deep-reads what actually changed.

    ./core/scout.py             run the scout now and print what moved (or quiet)
    ./core/scout.py --status    print the last scout watermark without calling MCPs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import audit  # noqa: E402
import board as B  # noqa: E402
import calendar_sync  # noqa: E402
import plugins  # noqa: E402
import prioritize  # noqa: E402
import settings  # noqa: E402

SEEN = settings.STATE_DIR / "scout-seen.json"
DELTA = settings.TMP_DIR / "delta.json"
LOCK = settings.STATE_DIR / "scout.lock"
LOG = settings.LOGS_DIR / "scout.log"

SLACK_LIVE_DAYS = 30
CANVAS_RE = re.compile(
    r"slack\.com/docs/[^/\s\"')]+/(F[A-Z0-9]{8,})"
    r"|(?i:canvas)[^.\n]{0,90}?\b(F[0-9A-Z]{9,12})\b"
)
ASANA_GID_RE = re.compile(r"asana\.com/(?:0/\d+/|1/\d+(?:/project/\d+)?/task/)(\d{10,})")
SLACK_TS_RE = re.compile(r"slack\.com/archives/([A-Z0-9]+)/p(\d{10})(\d{6})")
THREAD_TS_RE = re.compile(r"thread_ts=(\d{10}\.\d{6})")
SAVED_FILE_RE = re.compile(r"saved to (\S+?\.(?:txt|json))")
BOT_NAMES = {"held bills bot", "slackbot", "asana", "github", "datadog", "sentry"}


def model() -> str:
    """The cheap model Stage 1 runs on: Haiku 4.5 in the owner's region."""
    return settings.env("SCOUT_MODEL") or settings.model_id("haiku")


def load_seen() -> dict:
    try:
        data = json.loads(SEEN.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_seen(data: dict) -> None:
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(SEEN)


def last_checked_at(board: dict | None = None) -> str:
    """Newest timestamp at which the board was verified current (sweep or scout)."""
    b_at = str((board or {}).get("checked_at") or "")
    if not b_at and B.BOARD.exists():
        try:
            head = B.BOARD.read_bytes()[:4096].decode("utf-8", "ignore")
            m = re.search(r'"checked_at"\s*:\s*"([^"]+)"', head)
            if m:
                b_at = m.group(1)
        except OSError:
            pass
    s_at = str(load_seen().get("checked_at") or "")
    return max(b_at, s_at) if (b_at and s_at) else (s_at or b_at)


def _parse_iso(stamp: str) -> datetime | None:
    raw = str(stamp or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace(" ", "T"))
        if len(raw) <= 10:
            dt = datetime(dt.year, dt.month, dt.day, tzinfo=settings.tz())
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=settings.tz())
        return dt
    except ValueError:
        return None


def _utc_since(stamp: str, overlap_secs: int = 120) -> str:
    dt = _parse_iso(stamp)
    if dt is not None:
        return (dt.astimezone(timezone.utc) - timedelta(seconds=overlap_secs)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")


def _slack_ts(url: str) -> tuple[str, str] | None:
    m = SLACK_TS_RE.search(url or "")
    if not m:
        return None
    parent = THREAD_TS_RE.search(url or "")
    return m.group(1), parent.group(1) if parent else f"{m.group(2)}.{m.group(3)}"


def _slack_msg_key(url: str) -> str:
    """Canonical `CHANNEL:TS` from a Slack permalink (reply or parent)."""
    m = SLACK_TS_RE.search(url or "")
    if not m:
        return ""
    return f"{m.group(1)}:{m.group(2)}.{m.group(3)}"


def _slack_oldest(last_at: str, past_minute: bool = True) -> str:
    raw = str(last_at or "").strip()
    dt = _parse_iso(raw)
    if dt is None:
        return ""
    if not past_minute:
        return f"{dt.timestamp() - 60:.6f}"
    step = 60 if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", raw) else 1
    return f"{dt.timestamp() + step:.6f}"


def _hhmm_mins(hhmm: str) -> int | None:
    try:
        h, m = (int(x) for x in str(hhmm).strip().split(":")[:2])
        return h * 60 + m
    except ValueError:
        return None


def pre_room_sync(b: dict, now: datetime | None = None) -> dict | None:
    """Return room metadata when inside the 90-minute pre-room window AND today's
    script has already been built for that room."""
    now = now or settings.now()
    today = now.date().isoformat()
    script = b.get("script") or {}
    if str(script.get("for_date") or "") != today:
        return None
    script_at = str(script.get("at") or "").strip()
    now_mins = now.hour * 60 + now.minute
    dow = settings.DAYS[now.weekday()]

    rooms: list[tuple[str, str, str]] = []
    for s in b.get("sessions") or []:
        if not s.get("skipped") and s.get("date") == today and s.get("at"):
            rooms.append((
                str(s["at"]),
                str(s.get("title") or s.get("label") or s.get("kind") or "session"),
                str(s.get("kind") or "standup"),
            ))
    m = settings.meeting()
    if dow in (m.get("days") or []) and m.get("time_local"):
        rooms.append((str(m["time_local"]), str(m.get("name") or "Standup"), "standup"))
    for a in m.get("also") or []:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("kind") or "other")
        if kind == "huddle":
            continue
        if dow in (a.get("days") or []) and a.get("time_local"):
            rooms.append((str(a["time_local"]), str(a.get("name") or kind), kind))

    seen_at: set[str] = set()
    for at_str, title, kind in sorted(rooms, key=lambda r: r[0]):
        if at_str in seen_at:
            continue
        seen_at.add(at_str)
        r_mins = _hhmm_mins(at_str)
        if r_mins is None:
            continue
        mins_until = r_mins - now_mins
        if not (0 <= mins_until <= 90):
            continue
        if at_str >= "12:30" and script_at and script_at != at_str:
            continue
        return {
            "active": True,
            "date": today,
            "at": at_str,
            "room": title,
            "kind": kind,
            "mins_until": mins_until,
        }
    return None


def _sync_seen_with_board(b: dict, seen: dict) -> dict:
    """Promote pending watermarks to `swept` once `board.checked_at` has advanced."""
    b_checked = str(b.get("checked_at") or "")
    prev_b = str(seen.get("board_checked_at") or "")
    swept_mod = dict(seen.get("swept_asana_mod") or {})
    swept_slack = set(seen.get("swept_slack_keys") or [])
    swept_canvas = dict(seen.get("swept_canvas_hash") or {})

    if b_checked and b_checked != prev_b:
        swept_mod.update(seen.get("pending_asana_mod") or {})
        swept_slack.update(seen.get("pending_slack_keys") or [])
        swept_canvas.update(seen.get("pending_canvas_hash") or {})
        seen["pending_asana_mod"] = {}
        seen["pending_slack_keys"] = []
        seen["pending_canvas_hash"] = {}
        seen["board_checked_at"] = b_checked

    # Bound state size over months of running.
    if len(swept_mod) > 400:
        for k in list(swept_mod.keys())[:-400]:
            swept_mod.pop(k, None)
    swept_slack_list = sorted(swept_slack)[-600:]
    seen["swept_asana_mod"] = swept_mod
    seen["swept_slack_keys"] = swept_slack_list
    seen["swept_canvas_hash"] = swept_canvas
    return seen


def _board_known_slack_keys(b: dict) -> set[str]:
    """Every `CHANNEL:TS` already recorded in ticket events or threads on the board."""
    keys: set[str] = set()
    for t in b.get("tickets") or []:
        for ev in t.get("events") or []:
            k = _slack_msg_key(str(ev.get("source_url") or ""))
            if k:
                keys.add(k)
        for th in t.get("threads") or []:
            k = _slack_msg_key(str(th.get("url") or ""))
            if k:
                keys.add(k)
    return keys


def build_queries(b: dict, seen: dict) -> tuple[list[dict], dict]:
    """Construct the exact list of read-only MCP calls for Stage 1."""
    checked = str(b.get("checked_at") or "")
    verified = str(seen.get("verified_clean_at") or "")
    gate_base = max(checked, verified) if (checked and verified) else (checked or verified)
    overlap = 30 if (verified and verified > checked) else 180
    gate_since = _utc_since(gate_base, overlap_secs=overlap)

    proj_gids = [
        str(p["gid"])
        for p in settings.section("asana").get("projects") or []
        if isinstance(p, dict) and p.get("gid")
    ]
    excl_gids = {
        str(p["gid"])
        for p in settings.section("asana").get("exclude_projects") or []
        if isinstance(p, dict) and p.get("gid")
    }

    queries: list[dict] = []

    # 1. Asana unfiltered membership census.
    census_args: dict = {
        "assignee_any": "me",
        "completed": False,
        "opt_fields": (
            "name,completed,modified_at,assignee.name,projects.gid,"
            "memberships.section.name,custom_fields.name,custom_fields.display_value"
        ),
        "limit": 100,
    }
    if proj_gids:
        census_args["projects_any"] = ",".join(proj_gids)
    elif settings.section("asana").get("workspace_gid"):
        census_args["workspace"] = str(settings.section("asana")["workspace_gid"])
    queries.append({"tag": "census", "tool": "mcp__asana__search_tasks", "args": census_args})

    # 2. Asana incremental gate on shared projects.
    if proj_gids:
        queries.append({
            "tag": "gate",
            "tool": "mcp__asana__search_tasks",
            "args": {
                "projects_any": ",".join(proj_gids),
                "modified_at_after": gate_since,
                "opt_fields": "name,modified_at,assignee.name,completed,projects.gid",
                "limit": 100,
            },
        })

    # 3. Milestone child ticket searches.
    for ms in b.get("milestones") or []:
        if ms.get("status") == "done":
            continue
        ms_text = str(ms.get("name_ja") or ms.get("name") or "").strip()
        if not ms_text:
            continue
        ms_args: dict = {
            "text": ms_text,
            "completed": False,
            "modified_at_after": gate_since,
            "opt_fields": "name,assignee.name,modified_at,completed,custom_fields.name,custom_fields.display_value",
        }
        if proj_gids:
            ms_args["projects_any"] = ",".join(proj_gids)
        queries.append({"tag": f"milestone:{ms.get('id')}", "tool": "mcp__asana__search_tasks", "args": ms_args})

    # 4. Prioritization & internal CE build tickets.
    prio_cfg = prioritize.config() if settings.prioritization() else {}
    ce_projs = [str(g) for g in (prio_cfg.get("ce_projects") or []) if g]
    p_block = b.get("prioritization") or {}
    p_base = str(p_block.get("checked_at") or checked)
    if verified and verified > p_base:
        p_since = _utc_since(verified, overlap_secs=30)
    else:
        p_since = _utc_since(p_base, overlap_secs=180)
    if ce_projs:
        queries.append({
            "tag": "ce_builds",
            "tool": "mcp__asana__search_tasks",
            "args": {
                "projects_any": ",".join(ce_projs),
                "modified_at_after": p_since,
                "opt_fields": "name,due_on,completed,modified_at,custom_fields.gid,custom_fields.name,custom_fields.display_value",
                "limit": 100,
            },
        })
    else:
        # When no CE build projects are configured, check open tickets' internal_ticket GIDs directly.
        seen_int: set[str] = set()
        for t in b.get("tickets") or []:
            if (t.get("asana") or {}).get("completed") or not any(B.is_open(i) for i in t.get("items") or []):
                continue
            u = str((t.get("internal_ticket") or {}).get("url") or "")
            m_gid = ASANA_GID_RE.search(u) or re.search(r"/(\d{10,})(?:[/?#]|$)", u)
            if m_gid and m_gid.group(1) not in seen_int:
                gid = m_gid.group(1)
                seen_int.add(gid)
                queries.append({
                    "tag": f"internal:{t.get('ref')}:{gid}",
                    "tool": "mcp__asana__get_task",
                    "args": {
                        "task_id": gid,
                        "include_comments": False,
                        "opt_fields": "name,modified_at,completed,custom_fields.name,custom_fields.display_value",
                    },
                })

    # 5. Slack: owner sends, mentions, prioritization lead DM, live threads, old channels, canvases.
    me_slack = str(settings.section("owner").get("slack_user_id") or "").strip()
    base_dt = _parse_iso(gate_base)
    after_day = ((base_dt.date() - timedelta(days=1)) if base_dt else (settings.now().date() - timedelta(days=14))).isoformat()

    if me_slack:
        queries.append({
            "tag": "slack_from_me",
            "tool": "mcp__slack__slack_search_public_and_private",
            "args": {
                "query": f"from:<@{me_slack}> after:{after_day}",
                "sort": "timestamp",
                "include_context": False,
                "response_format": "detailed",
            },
        })
        queries.append({
            "tag": "slack_mentions",
            "tool": "mcp__slack__slack_search_public_and_private",
            "args": {
                "query": f"<@{me_slack}> after:{after_day}",
                "sort": "timestamp",
                "include_context": False,
                "response_format": "detailed",
            },
        })

    lead_dm = str(prio_cfg.get("slack_user_id") or "").strip()
    if lead_dm:
        dm_args: dict = {"channel_id": lead_dm, "limit": 20, "response_format": "detailed"}
        dm_oldest = _slack_oldest(gate_base, past_minute=False)
        if dm_oldest:
            dm_args["oldest"] = dm_oldest
        queries.append({"tag": "slack_lead_dm", "tool": "mcp__slack__slack_read_channel", "args": dm_args})

    cut = (base_dt.date() if base_dt else settings.now().date()) - timedelta(days=SLACK_LIVE_DAYS)
    lasts: dict[tuple[str, str], str] = {}
    all_board_threads: dict[tuple[str, str], str] = {}
    for t in b.get("tickets") or []:
        ref = str(t.get("ref") or "")
        for th in t.get("threads") or []:
            k = _slack_ts(str(th.get("url") or ""))
            if not k:
                continue
            all_board_threads[k] = ref
            la = str(th.get("last_at") or "").strip()
            if la and la > lasts.get(k, ""):
                lasts[k] = la

    seen_threads: set[tuple[str, str]] = set()
    old_channels: set[str] = set()
    checked_urls: list[str] = []
    thread_meta: dict[str, dict] = {}
    canvases: dict[str, list[str]] = {}

    for t in b.get("tickets") or []:
        open_items = [i for i in t.get("items") or [] if B.is_open(i)]
        if not open_items:
            continue
        ref = str(t.get("ref") or "")
        links = [str(th.get("url") or "") for th in t.get("threads") or []]
        for i in open_items:
            for d in B.drafts(i):
                if d.get("link"):
                    links.append(str(d["link"]))
                if B.unsent(d):
                    for m in CANVAS_RE.finditer(str(d.get("link") or "") + " " + str(d.get("target") or "")):
                        cid = m.group(1) or m.group(2)
                        if cid:
                            canvases.setdefault(cid, []).append(ref)
            if i.get("state") == "todo":
                for st_text in B.open_steps(i):
                    for m in CANVAS_RE.finditer(str(st_text)):
                        cid = m.group(1) or m.group(2)
                        if cid:
                            canvases.setdefault(cid, []).append(ref)

        for url in links:
            key = _slack_ts(url)
            if not key or key in seen_threads:
                continue
            seen_threads.add(key)
            last_str = lasts.get(key, "")
            try:
                fallback_day = datetime.fromtimestamp(float(key[1])).date()
            except (ValueError, OSError):
                fallback_day = settings.now().date()
            last_day = audit.as_date(last_str) or fallback_day
            if last_day < cut:
                old_channels.add(key[0])
                continue
            oldest = _slack_oldest(last_str)
            t_args: dict = {"channel_id": key[0], "message_ts": key[1], "response_format": "detailed"}
            if oldest:
                t_args["oldest"] = oldest
            tag = f"thread:{key[0]}:{key[1]}"
            queries.append({"tag": tag, "tool": "mcp__slack__slack_read_thread", "args": t_args})
            checked_urls.append(url)
            thread_meta[tag] = {"ticket": ref, "channel_id": key[0], "message_ts": key[1], "url": url, "last_at": last_str}

    # Prioritization channel check if a round just ran and is not recorded yet.
    if prio_cfg:
        ran = prioritize.previous(prio_cfg).get("date") or ""
        if ran and str((p_block.get("last_round") or {}).get("date") or "") < ran and prio_cfg.get("channel_id"):
            old_channels.add(str(prio_cfg["channel_id"]))

    for ch in sorted(old_channels):
        queries.append({
            "tag": f"slack_chan:{ch}",
            "tool": "mcp__slack__slack_search_public_and_private",
            "args": {
                "query": f"in:<#{ch}> after:{after_day}",
                "sort": "timestamp",
                "include_context": False,
                "include_bots": False,
                "response_format": "detailed",
            },
        })

    # Also scan configured client/ops channels (`slack.channel_hints`) for any new
    # unlinked thread mentioning one of the owner's active ticket tags, GIDs, KT
    # builds, or tracked account IDs, without requiring an `@` mention.
    seen_hints: set[str] = set()
    for raw_ch in settings.section("slack").get("channel_hints") or []:
        clean_ch = re.sub(r"^#+", "", str(raw_ch or "").strip())
        if not clean_ch or clean_ch.lower() in seen_hints:
            continue
        if re.fullmatch(r"[CDG][A-Z0-9]{8,}", clean_ch):
            if clean_ch in old_channels:
                continue
            seen_hints.add(clean_ch.lower())
            q_in = f"in:<#{clean_ch}>"
        else:
            seen_hints.add(clean_ch.lower())
            q_in = f"in:#{clean_ch}"
        queries.append({
            "tag": f"slack_hint:{clean_ch}",
            "tool": "mcp__slack__slack_search_public_and_private",
            "args": {
                "query": f"{q_in} after:{after_day}",
                "sort": "timestamp",
                "include_context": False,
                "include_bots": False,
                "response_format": "detailed",
            },
        })

    for cid in sorted(canvases):
        queries.append({
            "tag": f"canvas:{cid}",
            "tool": "mcp__slack__slack_read_canvas",
            "args": {"canvas_id": cid},
        })

    meta = {
        "gate_base": gate_base,
        "gate_since": gate_since,
        "stories_since": _utc_since(checked, overlap_secs=180),
        "excl_gids": excl_gids,
        "checked_urls": checked_urls,
        "thread_meta": thread_meta,
        "all_board_threads": all_board_threads,
        "canvases": canvases,
    }
    return queries, meta


def _run_hook() -> int:
    """PostToolUse hook mode (`scout.py --hook <dir>`): compact and save the MCP
    output for Python diffing, and hand the model `"ok"` (1 token) instead of JSON."""
    try:
        import compact  # noqa: E402

        out_dir = Path(sys.argv[2])
        event = json.loads(sys.stdin.read())
        tool = str(event.get("tool_name") or "")
        if not tool.startswith("mcp__"):
            return 0
        tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
        raw = compact.response_text(event.get("tool_response"))
        saved = SAVED_FILE_RE.search(raw)
        if saved:
            try:
                raw = Path(saved.group(1)).read_text(encoding="utf-8")
            except OSError:
                pass
        else:
            raw = compact.compact_text(raw, tool=tool, tool_input=tool_input)
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{time.time_ns()}-{os.getpid()}.json"
        (out_dir / fname).write_text(
            json.dumps({"tool": tool, "input": tool_input, "raw": raw}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedMCPToolOutput": "ok",
            }
        }))
    except Exception:  # noqa: BLE001
        return 0
    return 0


def _parse_stream(stdout: str, queries: list[dict], hook_dir: Path | None = None) -> tuple[list[tuple[dict, str]], dict]:
    """Match every `tool_use` with its raw MCP response (from `hook_dir` or stream)
    and sum deduplicated API turn token usage."""
    by_sig: dict[tuple[str, str], list[dict]] = {}
    for q in queries:
        sig = (q["tool"], json.dumps(q["args"], sort_keys=True))
        by_sig.setdefault(sig, []).append(q)

    call_map: dict[str, dict] = {}
    paired: list[tuple[dict, str]] = []
    msg_usage: dict[str, dict] = {}
    errors: list[str] = []

    for raw_line in stdout.splitlines():
        clean = raw_line.strip()
        if not clean.startswith("{"):
            continue
        try:
            row = json.loads(clean)
        except ValueError:
            continue
        rtype = row.get("type")
        if rtype == "assistant":
            msg = row.get("message") or {}
            mid = str(msg.get("id") or f"turn-{len(msg_usage)}")
            u = msg.get("usage") or {}
            if u:
                msg_usage[mid] = {
                    "input_tokens": int(u.get("input_tokens") or 0),
                    "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
                    "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens") or 0),
                    "output_tokens": int(u.get("output_tokens") or 0),
                }
            for part in msg.get("content") or []:
                if not isinstance(part, dict) or part.get("type") != "tool_use":
                    continue
                tname = str(part.get("name") or "")
                if not tname.startswith("mcp__"):
                    continue
                inp = part.get("input") or {}
                sig = (tname, json.dumps(inp, sort_keys=True))
                q_list = by_sig.get(sig)
                q_meta = q_list.pop(0) if q_list else {"tag": tname, "tool": tname, "args": inp}
                call_map[str(part.get("id") or "")] = q_meta
        elif rtype == "user" and hook_dir is None:
            for part in (row.get("message") or {}).get("content") or []:
                if not isinstance(part, dict) or part.get("type") != "tool_result":
                    continue
                tid = str(part.get("tool_use_id") or "")
                q_meta = call_map.get(tid)
                if not q_meta:
                    continue
                body = part.get("content")
                if isinstance(body, list):
                    body = "".join(str(x.get("text", "")) for x in body if isinstance(x, dict))
                text = str(body or "")
                saved = SAVED_FILE_RE.search(text)
                if saved:
                    try:
                        text = Path(saved.group(1)).read_text(encoding="utf-8")
                    except OSError:
                        pass
                if part.get("is_error") and "exceeds maximum allowed tokens" not in text:
                    errors.append(f"{q_meta['tag']}: {text[:160]}")
                paired.append((q_meta, text))

    if hook_dir is not None and hook_dir.exists():
        # Match hook-captured raw responses by (tool, args) signature so Python
        # receives 100% unclipped data while the model only saw "ok".
        by_sig_hook: dict[tuple[str, str], list[dict]] = {}
        for q in queries:
            sig = (q["tool"], json.dumps(q["args"], sort_keys=True))
            by_sig_hook.setdefault(sig, []).append(q)
        for fpath in sorted(hook_dir.glob("*.json")):
            try:
                rec = json.loads(fpath.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            tname = str(rec.get("tool") or "")
            inp = rec.get("input") or {}
            sig = (tname, json.dumps(inp, sort_keys=True))
            q_list = by_sig_hook.get(sig)
            q_meta = q_list.pop(0) if q_list else {"tag": tname, "tool": tname, "args": inp}
            paired.append((q_meta, str(rec.get("raw") or "")))

    usage = {"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 0}
    for u in msg_usage.values():
        for k in usage:
            usage[k] += u[k]

    return paired, {"usage": usage, "errors": errors, "calls": len(paired), "turns": len(msg_usage)}


def _unwrap_json(text: str) -> dict | list | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        got = json.loads(raw)
    except ValueError:
        return None
    if isinstance(got, list) and got and isinstance(got[0], dict) and "text" in got[0]:
        try:
            return json.loads(got[0]["text"])
        except ValueError:
            return got
    return got


def _task_cf_map(task: dict) -> dict[str, str]:
    cf = task.get("custom_fields")
    if isinstance(cf, dict):
        return {str(k): str(v) for k, v in cf.items() if v not in (None, "")}
    if isinstance(cf, list):
        out: dict[str, str] = {}
        for f in cf:
            if isinstance(f, dict) and f.get("display_value") not in (None, ""):
                key = str(f.get("name") or f.get("gid") or "")
                if key:
                    out[key] = str(f["display_value"])
                if f.get("gid"):
                    out[str(f["gid"])] = str(f["display_value"])
        return out
    return {}


def _task_assignee(task: dict) -> str:
    a = task.get("assignee")
    if isinstance(a, dict):
        return str(a.get("name") or "")
    return str(a or "")


def _task_sections(task: dict) -> list[str]:
    secs: list[str] = []
    for m in task.get("memberships") or []:
        if not isinstance(m, dict):
            continue
        s = m.get("section")
        if isinstance(s, dict):
            name = str(s.get("name") or "").strip()
        else:
            name = str(s or "").strip()
        if name:
            secs.append(name)
    return secs


def _task_proj_gids(task: dict) -> set[str]:
    out: set[str] = set()
    for p in task.get("projects") or []:
        if isinstance(p, dict) and p.get("gid"):
            out.add(str(p["gid"]))
        elif isinstance(p, str) and p.isdigit():
            out.add(p)
    return out


def _parse_slack_search_blocks(results_text: str) -> list[dict]:
    """Parse Slack detailed search output into structured message dicts."""
    if not results_text or "No results found" in results_text:
        return []
    blocks = re.split(r"\n*### Result \d+ of \d+\n", results_text)
    out: list[dict] = []
    for blk in blocks[1:]:
        ch_m = re.search(r"^Channel:\s*(.*?)\s*\(ID:\s*([A-Z0-9]+)\)", blk, re.M)
        fr_m = re.search(r"^From:\s*(.*?)\s*\(ID:\s*([A-Z0-9]+)\)", blk, re.M)
        tm_m = re.search(r"^Time:\s*(.+)$", blk, re.M)
        ts_m = re.search(r"^Message_ts:\s*(\d+\.\d+)", blk, re.M)
        lk_m = re.search(r"^Permalink:\s*(?:\[[^\]]*\]\((https?://[^)]+)\)|(https?://\S+))", blk, re.M)
        tx_m = re.search(r"^Text:\s*\n([\s\S]*?)(?:\n---\s*$|\Z)", blk, re.M)
        ch_id = ch_m.group(2) if ch_m else ""
        msg_ts = ts_m.group(1) if ts_m else ""
        url = (lk_m.group(1) or lk_m.group(2)) if lk_m else ""
        if not ch_id and url:
            parsed = _slack_ts(url)
            if parsed:
                ch_id = parsed[0]
        if not (ch_id and msg_ts):
            continue
        thread_m = THREAD_TS_RE.search(url)
        out.append({
            "key": f"{ch_id}:{msg_ts}",
            "channel": ch_m.group(1).strip() if ch_m else "",
            "channel_id": ch_id,
            "from": fr_m.group(1).strip() if fr_m else "",
            "from_id": fr_m.group(2).strip() if fr_m else "",
            "time": tm_m.group(1).strip() if tm_m else "",
            "ts": msg_ts,
            "thread_ts": thread_m.group(1) if thread_m else "",
            "url": url,
            "text": (tx_m.group(1).strip() if tx_m else blk.strip())[:1200],
        })
    return out


def _parse_channel_messages(messages_text: str, channel_id: str) -> list[dict]:
    """Parse `slack_read_channel` output (detailed or concise) into message dicts."""
    text = str(messages_text or "").strip()
    if not text or re.fullmatch(r"Channel:\s*[^\n]+", text):
        return []
    out: list[dict] = []
    if "--- Message " in text or "Message TS:" in text:
        for blk in re.split(r"\n*(?=--- Message \d+)", text):
            ts_m = re.search(r"Message TS:\s*(\d+\.\d+)", blk)
            fr_m = re.search(r"From:\s*(.+)", blk)
            tm_m = re.search(r"Time:\s*(.+)", blk)
            if ts_m:
                ts = ts_m.group(1)
                out.append({
                    "key": f"{channel_id}:{ts}",
                    "channel_id": channel_id,
                    "from": fr_m.group(1).strip() if fr_m else "",
                    "time": tm_m.group(1).strip() if tm_m else "",
                    "ts": ts,
                    "text": blk.strip()[:1200],
                })
        return out
    # Fallback for concise output when non-empty after header line.
    lines = [ln for ln in text.splitlines() if not ln.startswith("Channel:")]
    body = "\n".join(lines).strip()
    if body:
        digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]
        out.append({
            "key": f"{channel_id}:hash-{digest}",
            "channel_id": channel_id,
            "from": "",
            "time": "",
            "ts": "",
            "text": body[:1200],
        })
    return out


def analyze_results(b: dict, seen: dict, meta: dict, paired: list[tuple[dict, str]],
                    now: datetime | None = None) -> dict:
    """Compare all MCP results against `board.json` and `seen` deterministically."""
    now = now or settings.now()
    today = now.date().isoformat()
    aliases = settings.owner_aliases()
    excl_gids: set[str] = meta["excl_gids"]
    swept_mod: dict[str, str] = seen.get("swept_asana_mod") or {}
    swept_slack: set[str] = set(seen.get("swept_slack_keys") or []) | _board_known_slack_keys(b)
    swept_canvas: dict[str, str] = seen.get("swept_canvas_hash") or {}

    checked_dt = _parse_iso(str(b.get("checked_at") or ""))
    floor_epoch = (checked_dt.timestamp() - 240.0) if checked_dt else 0.0
    floor_iso_utc = (
        (checked_dt.astimezone(timezone.utc) - timedelta(seconds=240)).strftime("%Y-%m-%dT%H:%M:%S")
        if checked_dt else ""
    )

    tickets_by_gid: dict[str, dict] = {}
    internal_gid_to_ref: dict[str, str] = {}
    board_needles: set[str] = set()
    acct_rx = plugins.account_pattern()
    for t in b.get("tickets") or []:
        tid = str(t.get("id") or "")
        ref = str(t.get("ref") or "")
        if tid.isdigit():
            tickets_by_gid[tid] = t
        if not (t.get("asana") or {}).get("completed"):
            if ref:
                board_needles.add(ref.lower())
            if tid.isdigit():
                board_needles.add(tid)
            u = str((t.get("internal_ticket") or {}).get("url") or "")
            m_gid = ASANA_GID_RE.search(u) or re.search(r"/(\d{10,})(?:[/?#]|$)", u)
            if m_gid:
                internal_gid_to_ref[m_gid.group(1)] = ref
            kt = str(((t.get("internal_ticket") or {}).get("build") or {}).get("kt") or "").strip()
            if kt:
                board_needles.add(kt.lower())
            blob_parts = [str(t.get("title_en") or ""), str(t.get("title_ja") or ""), str(t.get("where_it_stands") or "")]
            for it in t.get("items") or []:
                if B.is_open(it):
                    blob_parts.extend([
                        str(it.get("title") or ""),
                        str(it.get("where_it_stands") or ""),
                        " ".join(str(s) for s in (it.get("steps") or [])),
                    ])
            for acct in acct_rx.findall(" ".join(blob_parts)):
                if len(acct) >= 5:
                    board_needles.add(acct.lower())

    milestone_gids: dict[str, str] = {}
    for ms in b.get("milestones") or []:
        ms_id = str(ms.get("id") or "")
        for row in ms.get("tickets") or []:
            gid = str(row.get("asana_url") or "").rstrip("/").rsplit("/", 1)[-1]
            if gid.isdigit():
                milestone_gids[gid] = ms_id

    news_gids: set[str] = set()
    for n in b.get("news") or []:
        m_gid = ASANA_GID_RE.search(str(n.get("source_url") or ""))
        if m_gid:
            news_gids.add(m_gid.group(1))

    prio_gids = {
        str(x.get("gid")): str(x.get("kt") or "")
        for x in ((b.get("prioritization") or {}).get("builds") or [])
        if isinstance(x, dict) and x.get("gid")
    }
    for kt in prio_gids.values():
        if kt:
            board_needles.add(kt.lower())

    census_added: list[dict] = []
    census_missing: list[dict] = []
    census_field_diffs: list[dict] = []
    moved_asana: dict[str, dict] = {}
    moved_builds: list[dict] = []
    slack_replies: list[dict] = []
    slack_messages: list[dict] = []
    canvas_changes: list[dict] = []

    new_pending_mod: dict[str, str] = {}
    new_pending_slack: set[str] = set()
    new_pending_canvas: dict[str, str] = {}
    census_ran = False

    def is_new_mod(gid: str, mod_str: str) -> bool:
        if not mod_str:
            return False
        if swept_mod.get(gid) == mod_str:
            return False
        if floor_iso_utc and mod_str[:19] < floor_iso_utc:
            return False
        return True

    for q_meta, raw_text in paired:
        tag = q_meta["tag"]
        obj = _unwrap_json(raw_text)

        if tag == "census" and isinstance(obj, dict):
            census_ran = True
            rows = [r for r in (obj.get("data") or []) if isinstance(r, dict)]
            seen_census_gids: set[str] = set()
            for r in rows:
                gid = str(r.get("gid") or "")
                if not gid or (_task_proj_gids(r) & excl_gids):
                    continue
                seen_census_gids.add(gid)
                cf = _task_cf_map(r)
                secs = _task_sections(r)
                status = cf.get("Status", "").strip()
                prio_val = (cf.get("Priority (Kraken resource allocation)") or cf.get("Priority") or "").strip()
                cat_val = cf.get("Category", "").strip()
                sev_val = (cf.get("Severity 深刻度") or cf.get("Severity") or "").strip()
                assignee = _task_assignee(r).strip()
                mod = str(r.get("modified_at") or "")

                if gid not in tickets_by_gid:
                    census_added.append({
                        "gid": gid,
                        "name": str(r.get("name") or ""),
                        "status": status,
                        "section": secs[0] if secs else "",
                        "priority": prio_val,
                        "category": cat_val,
                        "severity": sev_val,
                        "assignee": assignee,
                        "modified_at": mod,
                    })
                    if mod:
                        new_pending_mod[gid] = mod
                    continue

                bt = tickets_by_gid[gid]
                ba = bt.get("asana") or {}
                diffs: dict[str, dict] = {}
                if status and status != str(ba.get("status") or "").strip():
                    diffs["status"] = {"board": ba.get("status"), "asana": status}
                b_sec = str(ba.get("section") or "").strip()
                if secs and b_sec not in secs:
                    diffs["section"] = {"board": b_sec, "asana": secs[0]}
                if prio_val and prio_val != str(ba.get("priority") or "").strip():
                    diffs["priority"] = {"board": ba.get("priority"), "asana": prio_val}
                if ba.get("completed") is True:
                    diffs["completed"] = {"board": True, "asana": False}
                if diffs:
                    census_field_diffs.append({
                        "ref": bt.get("ref"),
                        "gid": gid,
                        "diffs": diffs,
                        "modified_at": mod,
                    })
                    if mod:
                        new_pending_mod[gid] = mod
                elif is_new_mod(gid, mod):
                    moved_asana[gid] = {
                        "gid": gid,
                        "ref": bt.get("ref"),
                        "name": str(r.get("name") or ""),
                        "modified_at": mod,
                        "why": "modified in Asana since last sweep",
                    }
                    new_pending_mod[gid] = mod

            # Check board tickets that claim to be incomplete and assigned to the owner,
            # but vanished from the unfiltered census (completed or silently reassigned).
            for gid, bt in tickets_by_gid.items():
                if bt.get("no_ticket_yet"):
                    continue
                ba = bt.get("asana") or {}
                if ba.get("completed"):
                    continue
                b_assignee = str(ba.get("assignee") or "").strip().lower()
                if b_assignee in aliases and gid not in seen_census_gids:
                    census_missing.append({
                        "ref": bt.get("ref"),
                        "gid": gid,
                        "was_status": ba.get("status"),
                        "was_assignee": ba.get("assignee"),
                    })

        elif tag == "gate" and isinstance(obj, dict):
            for r in obj.get("data") or []:
                if not isinstance(r, dict):
                    continue
                gid = str(r.get("gid") or "")
                if not gid or (_task_proj_gids(r) & excl_gids):
                    continue
                mod = str(r.get("modified_at") or "")
                assignee = _task_assignee(r).strip()
                mine = assignee.lower() in aliases
                bt = tickets_by_gid.get(gid)
                is_active_board = bt is not None and (
                    not (bt.get("asana") or {}).get("completed")
                    or any(B.is_open(i) for i in bt.get("items") or [])
                )
                in_ms = gid in milestone_gids
                in_news = gid in news_gids
                if not (mine or is_active_board or in_ms or in_news):
                    continue
                if not is_new_mod(gid, mod):
                    continue
                moved_asana[gid] = {
                    "gid": gid,
                    "ref": bt.get("ref") if bt else (f"milestone:{milestone_gids[gid]}" if in_ms else "news/assigned"),
                    "name": str(r.get("name") or ""),
                    "assignee": assignee,
                    "completed": bool(r.get("completed")),
                    "modified_at": mod,
                    "why": "gate modified_at_after",
                }
                new_pending_mod[gid] = mod

        elif tag.startswith("milestone:") and isinstance(obj, dict):
            ms_id = tag.split(":", 1)[1]
            for r in obj.get("data") or []:
                if not isinstance(r, dict):
                    continue
                gid = str(r.get("gid") or "")
                mod = str(r.get("modified_at") or "")
                if not gid or not is_new_mod(gid, mod):
                    continue
                cf = _task_cf_map(r)
                moved_asana[gid] = {
                    "gid": gid,
                    "ref": f"milestone:{ms_id}",
                    "name": str(r.get("name") or ""),
                    "assignee": _task_assignee(r).strip(),
                    "completed": bool(r.get("completed")),
                    "status": cf.get("Status", ""),
                    "modified_at": mod,
                    "why": f"milestone {ms_id} child moved",
                }
                new_pending_mod[gid] = mod

        elif tag == "ce_builds" and isinstance(obj, dict):
            prio_cfg = prioritize.config()
            cdl_field = str(prio_cfg.get("cdl_owner_field") or "")
            owner = settings.owner_name().strip().lower()
            for r in obj.get("data") or []:
                if not isinstance(r, dict):
                    continue
                gid = str(r.get("gid") or "")
                mod = str(r.get("modified_at") or "")
                if not gid or not is_new_mod(gid, mod):
                    continue
                cf = _task_cf_map(r)
                cdl_val = (cf.get(cdl_field) or cf.get("CDL-Owner") or "").strip().lower()
                is_mine = bool(owner and cdl_val == owner)
                is_known_prio = gid in prio_gids
                is_internal = gid in internal_gid_to_ref
                if not (is_mine or is_known_prio or is_internal):
                    continue
                moved_builds.append({
                    "gid": gid,
                    "kt": cf.get("KT") or prio_gids.get(gid, ""),
                    "ticket": internal_gid_to_ref.get(gid, ""),
                    "name": str(r.get("name") or ""),
                    "completed": bool(r.get("completed")),
                    "modified_at": mod,
                    "fields": cf,
                })
                new_pending_mod[gid] = mod

        elif tag.startswith("internal:") and isinstance(obj, dict):
            _, ref, gid = tag.split(":", 2)
            task = obj.get("data") if isinstance(obj.get("data"), dict) else obj
            mod = str(task.get("modified_at") or "")
            if is_new_mod(gid, mod):
                moved_builds.append({
                    "gid": gid,
                    "ticket": ref,
                    "name": str(task.get("name") or ""),
                    "completed": bool(task.get("completed")),
                    "modified_at": mod,
                    "fields": _task_cf_map(task),
                })
                new_pending_mod[gid] = mod

        elif tag.startswith("thread:"):
            msg_text = str(obj.get("messages") if isinstance(obj, dict) else raw_text).strip()
            if (
                msg_text
                and not msg_text.startswith("no new replies")
                and "No thread mess" not in msg_text
                and "=== THREAD REPLIES (0 total) ===" not in msg_text
            ):
                tmeta = meta["thread_meta"].get(tag, {})
                ts_hits = re.findall(r"Message TS:\s*(\d+\.\d+)", msg_text)
                keys = [f"{tmeta.get('channel_id')}:{ts}" for ts in ts_hits]
                if keys and all(k in swept_slack for k in keys):
                    continue
                for k in keys:
                    new_pending_slack.add(k)
                slack_replies.append({
                    **tmeta,
                    "messages": msg_text[:3000],
                })

        elif tag in ("slack_from_me", "slack_mentions") or tag.startswith(("slack_chan:", "slack_hint:")):
            res_str = str(obj.get("results") if isinstance(obj, dict) else raw_text)
            hits = _parse_slack_search_blocks(res_str)
            prio_cfg = prioritize.config() if settings.prioritization() else {}
            prio_chan = str(prio_cfg.get("channel_id") or "")
            prio_lead_id = str(prio_cfg.get("slack_user_id") or "")
            prio_lead_name = str(prio_cfg.get("who") or "").strip().lower()

            for h in hits:
                if h["key"] in swept_slack:
                    continue
                try:
                    ts_f = float(h["ts"])
                except ValueError:
                    ts_f = 0.0
                if floor_epoch and ts_f and ts_f < floor_epoch:
                    continue
                if tag.startswith(("slack_chan:", "slack_hint:")):
                    sender = h["from"].lower()
                    if sender in BOT_NAMES:
                        swept_slack.add(h["key"])
                        continue
                    wakes_thread = (
                        bool(h["thread_ts"])
                        and (h["channel_id"], h["thread_ts"]) in meta["all_board_threads"]
                    )
                    is_prio_lead = (
                        h["channel_id"] == prio_chan
                        and (
                            (prio_lead_id and h["from_id"] == prio_lead_id)
                            or (prio_lead_name and prio_lead_name in sender)
                        )
                    )
                    low_text = h["text"].lower()
                    mentions_board = any(n in low_text for n in board_needles if len(n) >= 2)
                    if not (wakes_thread or is_prio_lead or mentions_board):
                        swept_slack.add(h["key"])
                        continue
                new_pending_slack.add(h["key"])
                slack_messages.append({"source": tag, **h})

        elif tag == "slack_lead_dm":
            msg_text = str(obj.get("messages") if isinstance(obj, dict) else raw_text)
            ch_id = str(q_meta["args"].get("channel_id") or "")
            for m_item in _parse_channel_messages(msg_text, ch_id):
                if m_item["key"] in swept_slack:
                    continue
                if m_item["ts"]:
                    try:
                        if floor_epoch and float(m_item["ts"]) < floor_epoch:
                            continue
                    except ValueError:
                        pass
                new_pending_slack.add(m_item["key"])
                slack_messages.append({"source": "lead_dm", **m_item})

        elif tag.startswith("canvas:"):
            cid = tag.split(":", 1)[1]
            refs = meta["canvases"].get(cid) or []
            md = ""
            if isinstance(obj, dict):
                md = str(obj.get("markdown_content") or obj.get("content") or "")
            else:
                md = raw_text
            head = md[:40_000]
            relevant_lines = [
                ln for ln in head.splitlines()
                if any(r.lower() in ln.lower() for r in refs if r)
                or any(n in ln.lower() for n in board_needles if len(n) >= 3)
            ]
            digest = hashlib.sha256("\n".join(relevant_lines).encode("utf-8")).hexdigest()[:16]
            prev_hash = swept_canvas.get(cid)
            if prev_hash is None:
                # Baseline the canvas on first sight unless today's date + ticket ref newly appeared
                # since the sweep's floor; record baseline in swept_canvas so quiet polls stay quiet.
                swept_canvas[cid] = digest
            elif prev_hash != digest:
                new_pending_canvas[cid] = digest
                canvas_changes.append({
                    "canvas_id": cid,
                    "tickets": refs,
                    "matching_lines": relevant_lines[:25],
                })

    # Local checks: unread Gemini meeting notes and missing morning brief on a new weekday.
    standup_st = calendar_sync.standup_notes(b)
    standup_ready = standup_st if (standup_st and standup_st.get("ready") and standup_st.get("docs")) else None
    taken_urls = {d["url"] for d in (standup_st.get("docs") or [])} if standup_st else set()
    unread_docs = [
        e for e in calendar_sync.unread_notes(b)
        if not ({n["url"] for n in e.get("notes") or []} & taken_urls)
    ]

    brief_missing = (
        now.weekday() < 5
        and now.hour >= 8
        and str((b.get("brief") or {}).get("for_date") or "") != today
        and seen.get("brief_triggered_for") != today
    )

    touched_refs: set[str] = set()
    for x in census_field_diffs + census_missing:
        if x.get("ref"):
            touched_refs.add(str(x["ref"]))
    for x in moved_asana.values():
        r = str(x.get("ref") or "")
        if r and not r.startswith("milestone:") and r != "news/assigned":
            touched_refs.add(r)
    for x in moved_builds:
        if x.get("ticket"):
            touched_refs.add(str(x["ticket"]))
    for x in slack_replies:
        if x.get("ticket"):
            touched_refs.add(str(x["ticket"]))
    for x in canvas_changes:
        for r in x.get("tickets") or []:
            touched_refs.add(str(r))

    changed = bool(
        census_added
        or census_missing
        or census_field_diffs
        or moved_asana
        or moved_builds
        or slack_replies
        or slack_messages
        or canvas_changes
        or standup_ready
        or unread_docs
        or brief_missing
    )

    return {
        "changed": changed,
        "census_ran": census_ran,
        "checked_at": now.isoformat(timespec="seconds"),
        "stories_since_utc": meta["stories_since"],
        "touched_tickets": sorted(touched_refs),
        "census_added": census_added,
        "census_missing": census_missing,
        "census_field_diffs": census_field_diffs,
        "moved_asana": list(moved_asana.values()),
        "moved_builds": moved_builds,
        "slack_replies": slack_replies,
        "slack_messages": slack_messages,
        "canvas_changes": canvas_changes,
        "standup_notes": standup_ready,
        "unread_notes": unread_docs[:6],
        "brief_missing": brief_missing,
        "checked_urls": meta["checked_urls"],
        "pre_room_prep_sync": pre_room_sync(b, now) if changed else None,
        "_pending": {
            "asana_mod": new_pending_mod,
            "slack_keys": sorted(new_pending_slack),
            "canvas_hash": new_pending_canvas,
            "swept_slack": sorted(swept_slack)[-600:],
            "swept_canvas": swept_canvas,
            "brief_date": today if brief_missing else seen.get("brief_triggered_for", ""),
        },
    }


def _log_run(outcome: str, took: float, stats: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        u = stats.get("usage") or {}
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{outcome}\t{took:.1f}s\t"
                f"calls={stats.get('calls', 0)}\tin={u.get('input_tokens', 0)}\t"
                f"cache={u.get('cache_read_input_tokens', 0)}\tout={u.get('output_tokens', 0)}\n"
            )
    except OSError:
        pass


def check(
    timeout: int = 180,
    on_proc: Callable[[subprocess.Popen | None], None] | None = None,
) -> dict:
    """Run Stage 1 scout now. Writes `scout-seen.json` (quiet) or `tmp/delta.json` (changed)."""
    import agent  # noqa: E402

    if not B.BOARD.exists():
        return {"error": "no board yet; run a full refresh first"}
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 240:
        return {"error": "a scout check is already running"}

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(f"{os.getpid()} {time.strftime('%H:%M:%S')}\n", encoding="utf-8")
    began = time.time()
    started_iso = settings.now().isoformat(timespec="seconds")

    try:
        kind = agent.pick("claude")
        if kind is None or kind.name != "claude":
            return {"error": "Claude Code is not available"}

        if calendar_sync.stale_for_sweep():
            calendar_sync.fetch()

        b = B.load()
        seen = _sync_seen_with_board(b, load_seen())
        queries, meta = build_queries(b, seen)

        hook_dir = Path(tempfile.mkdtemp(prefix="dandori-scout-"))
        needed_tools = sorted({q["tool"] for q in queries})
        cmd = [
            kind.binary, "--print", "--model", model(),
            "--effort", "low",
            "--tools", "ToolSearch",
            "--setting-sources", "user",
            "--settings", json.dumps({
                "apiKeyHelper": "printenv ANTHROPIC_API_KEY",
                "env": {"CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "0"},
                "hooks": {
                    "PostToolUse": [{
                        "matcher": "mcp__.*",
                        "hooks": [{
                            "type": "command",
                            "command": f"python3 {shlex.quote(str(settings.CORE / 'scout.py'))} --hook {shlex.quote(str(hook_dir))}",
                            "timeout": 20,
                        }],
                    }],
                },
            }),
            "--output-format", "stream-json", "--verbose",
        ]
        for t in needed_tools:
            cmd += ["--allowedTools", t]

        db = settings.database_mcp()
        disallowed = [
            "mcp__google__*", "mcp__notion__*", "mcp__glean*",
            *((f"mcp__{db}__*",) if db else ()),
            "mcp__asana__add_*", "mcp__asana__create*", "mcp__asana__update*",
            "mcp__asana__delete*", "mcp__asana__*portfolio*", "mcp__asana__*agent*",
            "mcp__asana__*preview*", "mcp__asana__get_teams", "mcp__asana__get_workspace*",
            "mcp__asana__get_status_overview", "mcp__asana__get_task_stories",
            "mcp__slack__slack_send*", "mcp__slack__slack_schedule*",
            "mcp__slack__slack_create*", "mcp__slack__slack_update*",
            "mcp__slack__slack_add*", "mcp__slack__slack_get_reactions",
        ]
        if "mcp__asana__get_task" not in needed_tools:
            disallowed.append("mcp__asana__get_task")
        if "mcp__slack__slack_read_canvas" not in needed_tools:
            disallowed.append("mcp__slack__slack_read_canvas")
        for gone in disallowed:
            cmd += ["--disallowedTools", gone]

        call_lines = "\n".join(
            f"{idx}. {q['tool']} with {json.dumps(q['args'], ensure_ascii=False)}"
            for idx, q in enumerate(queries, 1)
        )
        prompt = (
            "Execute ALL of the following read-only MCP tool calls in parallel in a single turn "
            "(load them first via ToolSearch in one select call). "
            "Do NOT read any saved files, do NOT run extra queries, and do NOT summarize the results. "
            "As soon as the tool calls return, reply with only the single word DONE.\n\n"
            f"{call_lines}\n"
        )

        proc = subprocess.Popen(
            cmd,
            cwd="/tmp",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=kind.env(),
        )
        if on_proc:
            on_proc(proc)
        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            return {"error": f"scout timed out after {timeout}s"}
        finally:
            if on_proc:
                on_proc(None)

        if proc.returncode != 0 and not stdout.strip():
            shutil.rmtree(hook_dir, ignore_errors=True)
            return {"error": f"scout exited {proc.returncode}: {(stderr or '')[-200:].strip()}"}

        try:
            paired, stats = _parse_stream(stdout, queries, hook_dir=hook_dir)
        finally:
            shutil.rmtree(hook_dir, ignore_errors=True)
        if not paired:
            tail = (stderr or stdout)[-240:].strip()
            return {"error": f"scout received no MCP results ({tail})"}

        analysis = analyze_results(b, seen, meta, paired)
        if not analysis["census_ran"] and any(q["tag"] == "census" for q in queries):
            _log_run("incomplete", time.time() - began, stats)
            return {"error": "scout did not complete the Asana census"}

        pending = analysis.pop("_pending")
        took = round(time.time() - began, 1)
        seen["checked_at"] = analysis["checked_at"]
        seen["swept_slack_keys"] = pending["swept_slack"]
        seen["swept_canvas_hash"] = pending["swept_canvas"]
        seen["last_stats"] = {"took_secs": took, **stats["usage"], "calls": stats["calls"]}

        if not analysis["changed"]:
            seen["verified_clean_at"] = started_iso
            save_seen(seen)
            DELTA.unlink(missing_ok=True)
            _log_run("quiet", took, stats)
            return {
                "changed": False,
                "checked_at": analysis["checked_at"],
                "took_secs": took,
                "calls": stats["calls"],
                "usage": stats["usage"],
            }

        # Record pending watermarks; they promote to `swept_*` only after `board.checked_at` moves.
        seen["board_checked_at"] = str(b.get("checked_at") or "")
        seen["pending_asana_mod"] = {**(seen.get("pending_asana_mod") or {}), **pending["asana_mod"]}
        seen["pending_slack_keys"] = sorted(set(seen.get("pending_slack_keys") or []) | set(pending["slack_keys"]))
        seen["pending_canvas_hash"] = {**(seen.get("pending_canvas_hash") or {}), **pending["canvas_hash"]}
        if pending["brief_date"]:
            seen["brief_triggered_for"] = pending["brief_date"]
        save_seen(seen)

        DELTA.parent.mkdir(parents=True, exist_ok=True)
        tmp_d = DELTA.with_suffix(".tmp")
        tmp_d.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_d.replace(DELTA)

        reasons: list[str] = []
        if analysis["census_added"]:
            reasons.append(f"{len(analysis['census_added'])} new ticket(s)")
        if analysis["census_missing"]:
            reasons.append(f"{len(analysis['census_missing'])} reassigned/closed ticket(s)")
        if analysis["census_field_diffs"]:
            reasons.append(f"{len(analysis['census_field_diffs'])} status/field change(s)")
        if analysis["moved_asana"]:
            reasons.append(f"{len(analysis['moved_asana'])} Asana update(s)")
        if analysis["moved_builds"]:
            reasons.append(f"{len(analysis['moved_builds'])} build update(s)")
        if analysis["slack_replies"]:
            reasons.append(f"{len(analysis['slack_replies'])} thread reply(s)")
        if analysis["slack_messages"]:
            reasons.append(f"{len(analysis['slack_messages'])} Slack message(s)")
        if analysis["canvas_changes"]:
            reasons.append(f"{len(analysis['canvas_changes'])} canvas edit(s)")
        if analysis["standup_notes"] or analysis["unread_notes"]:
            n_docs = (1 if analysis["standup_notes"] else 0) + len(analysis["unread_notes"])
            reasons.append(f"{n_docs} meeting note(s)")
        if analysis["brief_missing"]:
            reasons.append("morning brief due")

        summary = ", ".join(reasons) or "board delta"
        _log_run(f"delta ({summary})", took, stats)
        return {
            "changed": True,
            "summary": summary,
            "delta_path": str(DELTA),
            "checked_at": analysis["checked_at"],
            "took_secs": took,
            "calls": stats["calls"],
            "usage": stats["usage"],
            "pre_room_prep_sync": analysis.get("pre_room_prep_sync"),
        }
    finally:
        LOCK.unlink(missing_ok=True)


def record_full_refresh(when_iso: str = "") -> None:
    """Record a completed Full Refresh so `daily_full_refresh_due` knows today is covered."""
    seen = load_seen()
    seen["last_full_refresh_at"] = when_iso or settings.now().isoformat(timespec="seconds")
    seen.pop("last_full_attempt_at", None)
    save_seen(seen)


def record_full_attempt(when_iso: str = "") -> None:
    """Record an automatic Full Refresh start so transient errors retry no faster than 30m."""
    seen = load_seen()
    seen["last_full_attempt_at"] = when_iso or settings.now().isoformat(timespec="seconds")
    save_seen(seen)


def last_full_refresh_at() -> str:
    """ISO timestamp of the newest completed Full Refresh (`refresh`), falling back
    to `logs/refresh-YYYY-MM-DD.log` modification time when upgrading."""
    seen = load_seen()
    stamp = str(seen.get("last_full_refresh_at") or "").strip()
    if stamp:
        return stamp
    logs_dir = settings.LOGS_DIR
    if logs_dir.exists():
        files = sorted(logs_dir.glob("refresh-20*.log"))
        if files:
            try:
                mtime = files[-1].stat().st_mtime
                return datetime.fromtimestamp(mtime, tz=settings.tz()).isoformat(timespec="seconds")
            except OSError:
                pass
    return ""


def daily_full_refresh_due(now: datetime | None = None) -> bool:
    """True when a weekday morning has arrived (or >=22h have passed) without a
    completed Full Refresh today.

    A Delta Refresh collapses unchanged tickets to one line, so time-driven
    checks (7-day client status comments, stalled CE builds, stale news pruning)
    depend on one Full Refresh per workday even when the Mac was powered off at
    `08:15` and booted later in the morning.
    """
    if settings.auto_sweep_mins() <= 0 or not B.BOARD.exists():
        return False
    now = now or settings.now()
    if now.weekday() >= 5 or not (8 <= now.hour < 20):
        return False
    m_mins = _hhmm_mins(settings.morning_sweep() or "08:15")
    if m_mins is None:
        m_mins = 8 * 60 + 15
    now_mins = now.hour * 60 + now.minute
    last_dt = _parse_iso(last_full_refresh_at())
    needs_today = now_mins >= m_mins and (last_dt is None or last_dt.date() < now.date())
    needs_22h = last_dt is not None and (now - last_dt) >= timedelta(hours=22)
    if not (needs_today or needs_22h):
        return False
    attempt_dt = _parse_iso(str(load_seen().get("last_full_attempt_at") or ""))
    if attempt_dt is not None and (now - attempt_dt) < timedelta(minutes=30):
        return False
    return True


def due(now: datetime | None = None) -> bool:
    """Whether the background server loop should run a scout or daily full refresh now."""
    mins = settings.auto_sweep_mins()
    if mins <= 0 or not B.BOARD.exists():
        return False
    now = now or settings.now()
    if now.weekday() >= 5 or not (8 <= now.hour < 20):
        return False
    if daily_full_refresh_due(now):
        return True
    last_dt = _parse_iso(last_checked_at())
    if last_dt is None:
        return True
    return (now - last_dt) >= timedelta(minutes=mins)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "--hook":
        return _run_hook()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="show last scout watermark and token stats")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args(argv)

    if args.status:
        seen = load_seen()
        b = B.load() if B.BOARD.exists() else {}
        out = {
            "auto_sweep_mins": settings.auto_sweep_mins(),
            "board_checked_at": b.get("checked_at", ""),
            "last_full_refresh_at": last_full_refresh_at(),
            "daily_full_refresh_due": daily_full_refresh_due(),
            "scout_checked_at": seen.get("checked_at", ""),
            "verified_clean_at": seen.get("verified_clean_at", ""),
            "last_stats": seen.get("last_stats") or {},
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"Auto-delta interval: {out['auto_sweep_mins'] or 'off'} min")
            print(f"Last full refresh:   {out['last_full_refresh_at'] or 'never'} (due today: {out['daily_full_refresh_due']})")
            print(f"Board last swept:    {out['board_checked_at'] or 'never'}")
            print(f"Scout last checked:  {out['scout_checked_at'] or 'never'}")
            if out["last_stats"]:
                st = out["last_stats"]
                print(
                    f"Last scout run:      {st.get('took_secs', '?')}s, {st.get('calls', 0)} calls "
                    f"(in={st.get('input_tokens', 0)}, cache={st.get('cache_read_input_tokens', 0)}, "
                    f"out={st.get('output_tokens', 0)})"
                )
        return 0

    res = check()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 1 if res.get("error") else 0
    if res.get("error"):
        print(f"scout error: {res['error']}", file=sys.stderr)
        return 1
    u = res.get("usage") or {}
    tok = f"in={u.get('input_tokens', 0)}, cache={u.get('cache_read_input_tokens', 0)}, out={u.get('output_tokens', 0)}"
    if not res.get("changed"):
        print(f"Up to date at {res['checked_at']} ({res['took_secs']}s, {res['calls']} calls; {tok}).")
    else:
        print(
            f"Delta detected at {res['checked_at']}: {res['summary']} "
            f"-> {settings.client_rel(Path(res['delta_path']))} ({res['took_secs']}s, {res['calls']} calls; {tok})."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
