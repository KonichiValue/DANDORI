#!/usr/bin/env python3
"""Single source of truth for Refresh & Delta-Refresh targets across all sources.

Keeps `scout.py` (Stage 1 15m delta check), `digest.py` (Full & Delta sweep plan),
and `GET /api/targets` (the hidden Refresh Targets inspector dialog) in lockstep
without duplicating URL parsers or query builders:

1. **Asana**: unfiltered membership census, `modified_at_after` gate, active
   milestone child searches, and owned CE / internal build gates.
2. **Slack**: owner sends (`from:<@me>`), mentions (`<@me>`), prioritization
   lead DM, all live ticket threads (<30d, `oldest` watermark), >30d quiet-thread
   wake channels, `slack.channel_hints` unlinked ticket/account mentions, and
   active Slack canvases.
3. **Meetings (Google Calendar, Gemini Docs & Notion Meeting Notes)**: synced
   `calendar.json` events, unread Gemini docs (`drive_read_file`), and post-call
   Notion meeting notes (`notion-query-meeting-notes` for `meeting_note` and
   `migration_note`).
4. **Email (Gmail Read-Only)**: lightweight `gmail_search_messages` query scoped
   to `config.json` `client.domains` (or `email.query`) `after:<epoch>`, strictly
   read-only with zero send/draft tools permitted.
5. **Notion Docs, Miro Boards, Glean & Replica**: linked Notion pages/milestones
   and Miro boards on active tickets/items (swept on Full Refresh without 15m
   token bloat), opportunistic Glean cross-search, and on-demand DB X-Ray.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import audit  # noqa: E402
import board as B  # noqa: E402
import calendar_sync  # noqa: E402
import prioritize  # noqa: E402
import settings  # noqa: E402

SLACK_LIVE_DAYS = 30
SLACK_URL_RE = re.compile(
    r"(?:slack\.com/archives/|\b)([CDG][A-Z0-9]{8,})/p(\d{10})(\d{6})"
)
THREAD_TS_RE = re.compile(r"thread_ts=(\d{10}\.\d{6})")
CANVAS_RE = re.compile(
    r"slack\.com/docs/[^/\s\"')]+/(F[A-Z0-9]{8,})"
    r"|(?i:canvas)[^.\n]{0,90}?\b(F[0-9A-Z]{9,12})\b"
)
NOTION_URL_RE = re.compile(
    r"https?://(?:www\.notion\.so|app\.notion\.com)/[^\s\"')<>]+"
)
MIRO_URL_RE = re.compile(
    r"https?://miro\.com/app/board/([^/\s\"')<>]+)"
)
GDOC_URL_RE = re.compile(
    r"https?://(?:docs|drive)\.google\.com/[^\s\"')<>]+"
)


def parse_iso(stamp: str) -> datetime | None:
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


def slack_ts(url: str) -> tuple[str, str] | None:
    """`(channel_id, parent_ts)` from a Slack URL or `CHAN/p...` shorthand."""
    m = SLACK_URL_RE.search(str(url or ""))
    if not m:
        return None
    parent = THREAD_TS_RE.search(str(url or ""))
    return m.group(1), parent.group(1) if parent else f"{m.group(2)}.{m.group(3)}"


def configured_mcps() -> set[str]:
    """Server names configured in `~/.claude.json` (read in <1ms, no subprocess)."""
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
        return set((data.get("mcpServers") or {}).keys())
    except (OSError, ValueError):
        return set()


def email_query(gate_base: str, overlap_secs: int = 180) -> tuple[str, str]:
    """Return `(gmail_query, base_filter)` for incremental client email checks.

    Uses `config.json` `email.query` when explicitly provided; otherwise scopes
    to `client.domains` (`from:d1 OR from:d2`) so internal notification spam
    never triggers false deltas. Empty when neither is configured or `email.enabled`
    is explicitly `false`.
    """
    email_cfg = settings.section("email")
    if email_cfg.get("enabled") is False:
        return "", ""
    custom_q = str(email_cfg.get("query") or "").strip()
    domains = list(settings.client_domains())
    extra_senders = [str(s).strip() for s in (email_cfg.get("senders") or []) if str(s).strip()]
    if custom_q:
        base_q = custom_q
    else:
        terms = [f"from:{d}" for d in domains] + [f"from:{s}" for s in extra_senders]
        if not terms:
            return "", ""
        base_q = "(" + " OR ".join(terms) + ")"

    dt = parse_iso(gate_base)
    if dt is not None:
        epoch = max(0, int(dt.timestamp()) - overlap_secs)
    else:
        epoch = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    return f"{base_q} after:{epoch}", base_q


def active_canvases(board: dict) -> dict[str, list[str]]:
    """Map `canvas_id -> [ticket_refs]` referenced on open items/drafts."""
    canvases: dict[str, list[str]] = {}
    for t in board.get("tickets") or []:
        if (t.get("asana") or {}).get("completed"):
            continue
        ref = str(t.get("ref") or "")
        for i in t.get("items") or []:
            if not B.is_open(i):
                continue
            for d in B.drafts(i):
                if B.unsent(d):
                    for m in CANVAS_RE.finditer(f"{d.get('link', '')} {d.get('target', '')}"):
                        cid = m.group(1) or m.group(2)
                        if cid and ref not in canvases.setdefault(cid, []):
                            canvases[cid].append(ref)
            if i.get("state") == "todo":
                for st_text in B.open_steps(i):
                    for m in CANVAS_RE.finditer(str(st_text)):
                        cid = m.group(1) or m.group(2)
                        if cid and ref not in canvases.setdefault(cid, []):
                            canvases[cid].append(ref)
    return canvases


def linked_external_targets(board: dict) -> dict[str, list[dict]]:
    """Collect linked Notion pages, Miro boards, and Google Docs on active
    milestones, open tickets, and open items (for Full Refresh & inspector UI)."""
    notion_rows: list[dict] = []
    miro_rows: list[dict] = []
    gdoc_rows: list[dict] = []
    seen_notion: set[str] = set()
    seen_miro: set[str] = set()
    seen_gdoc: set[str] = set()

    def add_notion(url: str, scope: str, label: str, checked: str = "") -> None:
        clean = url.split("#", 1)[0].rstrip(").,")
        if not clean or clean in seen_notion:
            return
        seen_notion.add(clean)
        notion_rows.append({"url": clean, "scope": scope, "label": label, "checked": checked})

    def add_miro(url: str, board_id: str, scope: str, label: str, checked: str = "") -> None:
        if not board_id or board_id in seen_miro:
            return
        seen_miro.add(board_id)
        miro_rows.append({
            "url": f"https://miro.com/app/board/{board_id}/",
            "board_id": board_id,
            "scope": scope,
            "label": label,
            "checked": checked,
        })

    def add_gdoc(url: str, scope: str, label: str, checked: str = "") -> None:
        clean = url.split("#", 1)[0].rstrip(").,")
        if not clean or clean in seen_gdoc:
            return
        seen_gdoc.add(clean)
        gdoc_rows.append({"url": clean, "scope": scope, "label": label, "checked": checked})

    # Huddle / programme Notion links from config.json `meeting.also`
    for room in settings.meeting().get("also") or []:
        if isinstance(room, dict) and room.get("link"):
            u = str(room["link"])
            if NOTION_URL_RE.match(u):
                add_notion(u, "room", str(room.get("name") or "Room page"))

    # Active milestones
    for ms in board.get("milestones") or []:
        if not isinstance(ms, dict) or ms.get("status") == "done":
            continue
        u = str(ms.get("source_url") or "")
        if NOTION_URL_RE.match(u):
            add_notion(u, f"milestone:{ms.get('id')}", f"Milestone: {ms.get('name')}")

    # Open tickets & open items
    for t in board.get("tickets") or []:
        if (t.get("asana") or {}).get("completed"):
            continue
        ref = str(t.get("ref") or t.get("id") or "?")
        for th in t.get("threads") or []:
            u = str(th.get("url") or "").strip()
            lbl = str(th.get("label") or th.get("where") or ref)
            chk = str(th.get("checked") or th.get("last_at") or "")
            if NOTION_URL_RE.match(u):
                add_notion(u, ref, lbl, chk)
            elif m := MIRO_URL_RE.search(u):
                add_miro(u, m.group(1), ref, lbl, chk)
            elif GDOC_URL_RE.match(u):
                add_gdoc(u, ref, lbl, chk)

        for i in t.get("items") or []:
            if not B.is_open(i):
                continue
            scope = f"#{i.get('id')} ({ref})"
            prep = i.get("prepared") or {}
            for entry in (prep.get("files") or []) + (prep.get("sources") or []):
                if not isinstance(entry, dict):
                    continue
                u = str(entry.get("url") or entry.get("path") or "").strip()
                lbl = str(entry.get("label") or scope)
                if NOTION_URL_RE.match(u):
                    add_notion(u, scope, lbl, str(prep.get("built_at") or ""))
                elif m := MIRO_URL_RE.search(u):
                    add_miro(u, m.group(1), scope, lbl, str(prep.get("built_at") or ""))
                elif GDOC_URL_RE.match(u):
                    add_gdoc(u, scope, lbl, str(prep.get("built_at") or ""))

    return {
        "notion": notion_rows,
        "miro": miro_rows,
        "gdocs": gdoc_rows,
    }


def snapshot(board: dict | None = None, db_info: dict | None = None) -> dict:
    """Build the full Refresh & Delta Targets manifest for `/api/targets` and CLI."""
    import scout  # noqa: PLC0415

    b = board if board is not None else (B.load() if B.BOARD.exists() else {})
    seen = scout.load_seen()
    mcps = configured_mcps()
    checked = str(b.get("checked_at") or "")
    verified = str(seen.get("verified_clean_at") or "")
    gate_base = max(checked, verified) if (checked and verified) else (checked or verified)

    # 1. Asana targets
    asana_sec = settings.section("asana")
    projs = [
        {"gid": str(p.get("gid", "")), "name": str(p.get("name") or p.get("gid", ""))}
        for p in (asana_sec.get("projects") or [])
        if isinstance(p, dict) and p.get("gid")
    ]
    excl = [
        str(p.get("name") or p.get("gid", ""))
        for p in (asana_sec.get("exclude_projects") or [])
        if isinstance(p, dict) and p.get("gid")
    ]
    active_ms = [
        {"id": str(ms.get("id")), "query": str(ms.get("name_ja") or ms.get("name") or "")}
        for ms in (b.get("milestones") or [])
        if isinstance(ms, dict) and ms.get("status") != "done"
    ]
    prio_cfg = prioritize.config() if settings.prioritization() else {}
    ce_projs = [str(g) for g in (prio_cfg.get("ce_projects") or []) if g]
    tracked_builds = [
        str(x.get("kt") or x.get("gid"))
        for x in ((b.get("prioritization") or {}).get("builds") or [])
        if isinstance(x, dict) and (x.get("kt") or x.get("gid"))
    ]

    # 2. Slack targets
    queries, meta = scout.build_queries(b, seen) if b.get("tickets") is not None else ([], {})
    thread_targets = [
        {
            "ticket": v.get("ticket", ""),
            "channel_id": v.get("channel_id", ""),
            "last_at": v.get("last_at") or "unread",
            "url": v.get("url", ""),
        }
        for v in (meta.get("thread_meta") or {}).values()
    ]
    hint_chans = [str(c) for c in (settings.section("slack").get("channel_hints") or []) if c]
    wake_chans = [q["tag"].split(":", 1)[1] for q in queries if q["tag"].startswith("slack_chan:")]
    canvases = active_canvases(b)
    lead_who = str(prio_cfg.get("who") or "") if prio_cfg.get("slack_user_id") else ""

    # 3. Meetings (Google Calendar + Gemini + Notion)
    cal_raw = calendar_sync.load_raw()
    cal_seen = calendar_sync.seen_at()
    st_notes = calendar_sync.standup_notes(b) if b else None
    unread_gem = calendar_sync.unread_notes(b) if b else []
    unfolded_rooms = calendar_sync.unfolded_room_notes(b) if b else []
    m_cfg = settings.meeting()
    room_watchers = [{
        "name": str(m_cfg.get("name") or "Standup"),
        "schedule": f"{'/'.join(m_cfg.get('days') or [])} {m_cfg.get('time_local', '')}".strip(),
        "record": "meeting_note",
        "last_date": str((b.get("meeting_note") or {}).get("date") or "never"),
        "source": f"Gemini Notes + Notion \"{settings.notes_title()}\"",
    }]
    for a in m_cfg.get("also") or []:
        if isinstance(a, dict) and a.get("record"):
            rec = str(a["record"])
            room_watchers.append({
                "name": str(a.get("name") or a.get("kind") or "Room"),
                "schedule": f"{'/'.join(a.get('days') or [])} {a.get('time_local', '')}".strip(),
                "record": rec,
                "last_date": str((b.get(rec) or {}).get("date") or "never"),
                "source": f"Gemini Notes + Notion \"{a.get('notes_title') or a.get('name')}\"",
            })

    # 4. Email (Gmail read-only)
    gmail_q, gmail_base = email_query(gate_base)

    # 5. Linked Notion / Miro / Drive + DB Replica + Glean
    ext = linked_external_targets(b)
    db_mcp = settings.database_mcp()
    last_stats = seen.get("last_stats") or {}

    sources = [
        {
            "id": "asana",
            "name": "Asana",
            "status": "ok" if "asana" in mcps else "warn",
            "cadence": "Every 15m delta + Full refresh",
            "cost": f"{2 + len(active_ms) + (1 if ce_projs else 0)} parallel read calls",
            "summary": (
                f"{len(projs)} shared project(s) unfiltered census + modified_at gate"
                + (f", {len(active_ms)} milestone search(es)" if active_ms else "")
                + (f", {len(ce_projs)} CE project(s) ({len(tracked_builds)} builds)" if ce_projs else "")
            ),
            "items": [
                *(f"Census & gate: {p['name']} ({p['gid']})" for p in projs),
                *([f"Excluded: {', '.join(excl)}"] if excl else []),
                *(f"Milestone watch: \"{m['query']}\" ({m['id']})" for m in active_ms),
                *(
                    [f"CE builds gate: {', '.join(ce_projs)} ({', '.join(tracked_builds[:6]) or 'none yet'})"]
                    if ce_projs else []
                ),
            ],
        },
        {
            "id": "slack",
            "name": "Slack",
            "status": "ok" if "slack" in mcps else "warn",
            "cadence": "Every 15m delta + Full refresh (no skip gate)",
            "cost": f"{len(thread_targets)} incremental thread(s) + {2 + (1 if lead_who else 0) + len(hint_chans) + len(wake_chans) + len(canvases)} search/canvas call(s)",
            "summary": (
                f"Your sends & @mentions"
                + (f", {lead_who} DM" if lead_who else "")
                + f", {len(thread_targets)} live thread(s), {len(hint_chans)} channel hint(s)"
                + (f", {len(canvases)} canvas(es)" if canvases else "")
            ),
            "items": [
                "Owner sends (from:<@me>) & direct mentions (<@me>)",
                *([f"Prioritization lead DM: {lead_who}"] if lead_who else []),
                *(f"Live thread [{t['ticket']}]: last message {t['last_at']}" for t in thread_targets[:12]),
                *([f"+ {len(thread_targets) - 12} more live threads"] if len(thread_targets) > 12 else []),
                *([f"Channel hints (unlinked ticket/KT/account scan): {', '.join(hint_chans)}"] if hint_chans else []),
                *([f"Quiet >30d thread wake channels: {', '.join(wake_chans)}"] if wake_chans else []),
                *(f"Slack Canvas {cid}: {', '.join(refs)}" for cid, refs in canvases.items()),
            ],
        },
        {
            "id": "meetings",
            "name": "Meetings (Google Calendar, Gemini & Notion)",
            "status": "ok" if ("google" in mcps and "notion" in mcps) else "warn",
            "cadence": "Hourly calendar + post-meeting 15m Gemini/Notion poll",
            "cost": "0 calls when idle; 1 post-call check when a room ends",
            "summary": (
                f"Calendar synced {calendar_sync.ago(cal_seen) if cal_seen else 'never'} "
                f"({len(cal_raw.get('events') or [])} events)"
                + (f" · {len(unread_gem) + len(unfolded_rooms)} note(s) waiting" if (unread_gem or unfolded_rooms) else " · all room notes folded in")
            ),
            "items": [
                *(f"{rw['name']} ({rw['schedule']}): {rw['source']} · last folded {rw['last_date']}" for rw in room_watchers),
                *(f"Unread Gemini doc waiting: {e.get('date')} {e.get('title')}" for e in unread_gem[:4]),
                *(f"Post-call Notion check active: {r['name']} (ended {r['ended_at']})" for r in unfolded_rooms),
            ],
        },
        {
            "id": "email",
            "name": "Email (Gmail Read-Only)",
            "status": "ok" if ("google" in mcps and gmail_q) else ("idle" if not gmail_q else "warn"),
            "cadence": "Every 15m delta + Full refresh" if gmail_q else "Inactive (no client domains configured)",
            "cost": "1 lightweight watermark query (0 send/draft tools allowed)",
            "summary": (
                f"Read-only delta filter: {gmail_base} since last watermark"
                if gmail_q
                else "Add client.domains in config.json to watch client emails"
            ),
            "items": (
                [
                    f"Query: {gmail_q}",
                    "Strict CLI guard: send, draft, delete, archive & label tools are hard-blocked",
                ]
                if gmail_q else []
            ),
        },
        {
            "id": "notion_miro",
            "name": "Notion Docs & Miro Diagrams",
            "status": "ok" if "notion" in mcps else "warn",
            "cadence": "Full refresh & Huddle/Milestone prep (zero 15m poll overhead)",
            "cost": f"{len(ext['notion'])} Notion page(s), {len(ext['miro'])} Miro board(s), {len(ext['gdocs'])} Drive doc(s) tracked",
            "summary": (
                f"{len(ext['notion'])} linked Notion page(s)/databases & {len(ext['miro'])} Miro board(s) on active work"
            ),
            "items": [
                *(f"Notion [{n['scope']}]: {n['label']}" for n in ext["notion"][:8]),
                *(f"Miro [{m['scope']}]: {m['label']} ({m['board_id']})" for m in ext["miro"][:6]),
                *(f"Google Doc [{g['scope']}]: {g['label']}" for g in ext["gdocs"][:4]),
            ],
        },
        {
            "id": "glean_db",
            "name": "Database Replica & Glean Search",
            "status": "ok" if (not db_mcp or (db_info or {}).get("state") == "ok" or db_mcp in mcps) else "warn",
            "cadence": "On-demand only (never polled in background)",
            "cost": "0 background tokens; runs only when diagnosing accounts or cross-client context",
            "summary": (
                (f"Replica `{db_mcp}` ({(db_info or {}).get('said') or 'configured'})" if db_mcp else "No DB replica configured")
                + (" · Glean available in Cursor / optional MCP" if "glean_default" in mcps else " · Glean available in Cursor chat")
            ),
            "items": [
                *(
                    [f"Read-only Postgres replica: {db_mcp} ({settings.section('database').get('conn', '')})"]
                    if db_mcp else []
                ),
                "Glean enterprise search: cross-source fallback in Cursor chat without slowing headless sweeps",
            ],
        },
    ]

    return {
        "checked_at": checked,
        "verified_clean_at": verified,
        "gate_watermark": gate_base,
        "auto_sweep_mins": settings.auto_sweep_mins(),
        "morning_sweep": settings.morning_sweep() or "08:15",
        "scout_calls": len(queries),
        "last_scout": {
            "took_secs": last_stats.get("took_secs"),
            "calls": last_stats.get("calls"),
            "output_tokens": last_stats.get("output_tokens"),
        } if last_stats else None,
        "sources": sources,
    }


def main() -> int:
    snap = snapshot()
    if "--json" in sys.argv:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0
    print(f"DANDORI Refresh & Delta Targets (watermark: {snap['gate_watermark'] or 'never'})")
    print(f"Stage 1 Haiku scout: {snap['scout_calls']} parallel read-only calls every {snap['auto_sweep_mins'] or 'off'}m\n")
    for s in snap["sources"]:
        print(f"[{s['status'].upper():<4}] {s['name']} ({s['cadence']})")
        print(f"       {s['summary']}")
        for it in s["items"][:5]:
            print(f"         - {it}")
        if len(s["items"]) > 5:
            print(f"         - ... +{len(s['items']) - 5} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
