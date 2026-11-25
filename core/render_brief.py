#!/usr/bin/env python3
"""The morning radar (`This morning`): live, action-oriented pulse at the top of
the work view.

Built around how the desk is actually used rather than a prose summary of cards
below it:
1. External replies and state shifts since the last working day (never echoing
   the owner's own actions back to them).
2. What is actionable right now: unblocked jobs with you, unsent drafts ready to
   review, due chases, Asana status drift, and today's upcoming room readiness
   (with a one-click jump to `What I say`, hidden once the call has ended).
3. Approaching traps and real clocks: people away, milestone gates within a few
   days, tickets approaching the 7-day client-update window, prioritization
   cutoffs, and fresh programme news (older news stays folded out of the way).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import board as B
import prioritize
import settings
from render import (
    blocked_on,
    day_words,
    esc,
    esc_url,
    index_items,
    item_state,
    link_btn,
    next_live,
    script_meta,
    sessions,
    short_when,
    tracked,
    when_tag,
)

DATE_IN = re.compile(r"\d{4}-\d{2}-\d{2}")
CAP = 7
MINE = ' class="mine"'

WORD = {
    "alert": "Needs you now",
    "reply": "Replied",
    "moved": "Changed",
    "do": "Do next",
    "chase": "Chase due",
    "drift": "Asana status",
    "room": "Next room",
    "watch": "Heads up",
    "big": "Programme",
}


def freshness(built: str, for_date: str) -> tuple[str, str]:
    """How old `board['brief']` is, and whether its written lines still hold."""
    today = date.today().isoformat()
    if for_date and for_date < today:
        return "", "stale"
    if not built:
        return "", "ok"
    try:
        when = datetime.fromisoformat(built)
    except ValueError:
        return "", "ok"
    mins = (datetime.now(when.tzinfo) - when).total_seconds() / 60
    if mins < 90:
        return when.strftime("%H:%M"), "ok"
    if mins < 60 * 6:
        return f'{when.strftime("%H:%M")}, {int(mins // 60)}h ago', "ok"
    return f'{when.strftime("%H:%M")}, {int(mins // 60)}h ago', "old"


def _cutoff_iso(today: date) -> str:
    """Window for recent incoming replies: covers the weekend on Mondays (back to
    Thursday afternoon / Friday) and yesterday + today on Tue-Fri."""
    if today.weekday() == 0:
        return (today - timedelta(days=4)).isoformat()
    return (today - timedelta(days=1)).isoformat()


def _fresh_news_iso(today: date) -> str:
    """Only news from the last 1-2 working days earns a headline row; older rows
    stay tucked inside the collapsed Programme context fold."""
    days = 3 if today.weekday() == 0 else 2
    return (today - timedelta(days=days)).isoformat()


def _one_sentence(text: str, limit: int = 132) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    for stop in (". ", "。"):
        if stop in clean:
            clean = clean.split(stop)[0] + ("." if stop == ". " else "。")
            break
    if len(clean) > limit:
        clean = clean[: limit - 1].rsplit(" ", 1)[0] + "…"
    return clean


def _is_owner(who: str) -> bool:
    w = str(who or "").strip().lower()
    if not w:
        return False
    if w == "you":
        return True
    return any(alias in w for alias in settings.owner_aliases() if len(alias) >= 3)


def _as_date(val: object) -> date | None:
    m = DATE_IN.search(str(val or ""))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(0))
    except ValueError:
        return None


def _hhmm_mins(text: str) -> int | None:
    try:
        h, m = (int(x) for x in str(text or "").strip().split(":")[:2])
        return h * 60 + m
    except ValueError:
        return None


def _external_moves(board: dict, today: date) -> list[dict]:
    """Recent external replies or moves on the owner's active tickets."""
    cutoff = _cutoff_iso(today)
    owners = settings.owner_aliases()
    per_ticket: list[dict] = []
    for t in board.get("tickets") or []:
        asana = t.get("asana") or {}
        if asana.get("completed") or t.get("closed"):
            continue
        # Skip shelved unassigned tickets (e.g. won't-implement handovers) unless an item is with the owner.
        assignee = str(asana.get("assignee") or "").strip().lower()
        has_mine = any(i.get("state") == "todo" for i in (t.get("items") or []))
        if not t.get("no_ticket_yet") and not has_mine and assignee and owners:
            if not any(o in assignee for o in owners):
                continue
        elif not t.get("no_ticket_yet") and not has_mine and not assignee:
            continue
        ref = str(t.get("ref") or "")
        evs = sorted(
            t.get("events") or [],
            key=lambda e: (str(e.get("on") or ""), str(e.get("at") or "")),
        )
        ext = []
        last_owner_idx = -1
        filtered = []
        for e in evs:
            on = str(e.get("on") or "")
            if on < cutoff:
                continue
            who = str(e.get("who") or "").strip()
            what = str(e.get("what") or "").strip()
            if not what:
                continue
            low = what.lower()
            if low.startswith((
                "reminder:",
                "status set from",
                "liked your comment",
                "rag status comment transferred",
                "updated the rag status comment",
            )):
                continue
            filtered.append(e)
            if _is_owner(who):
                last_owner_idx = len(filtered) - 1
        # Only surface external moves that landed AFTER the owner's last action on this ticket,
        # so messages the owner already answered never show up as unread morning replies.
        ext = [
            e for idx, e in enumerate(filtered)
            if idx > last_owner_idx and not _is_owner(str(e.get("who") or ""))
        ]
        if not ext:
            continue
        # Prefer the newest external move that changed what the owner does (`so_what`), else the newest move.
        impactful = [e for e in ext if str(e.get("so_what") or "").strip()]
        chosen_ev = impactful[-1] if impactful else ext[-1]
        who_short = str(chosen_ev.get("who") or "").split(",")[0].strip()
        summary = _one_sentence(str(chosen_ev.get("so_what") or chosen_ev.get("what") or ""), 132)
        when_str = str(chosen_ev.get("on") or "")
        at_str = str(chosen_ev.get("at") or "")
        stamp = f"{when_str}T{at_str}" if when_str and at_str else when_str
        has_so_what = bool(str(chosen_ev.get("so_what") or "").strip())
        per_ticket.append(
            {
                "kind": "reply",
                "impactful": has_so_what,
                "sort_stamp": stamp,
                "ref": ref,
                "who": who_short,
                "what": f"{who_short}: {summary}" if who_short else summary,
                "source_url": str(chosen_ev.get("source_url") or ""),
                "at": f"{when_str}T{at_str}:00" if len(at_str) == 5 and when_str else at_str,
            }
        )
    per_ticket.sort(key=lambda r: (r["impactful"], r["sort_stamp"]), reverse=True)
    return per_ticket[:3]


def _upcoming_rollup_row(board: dict, today: date, now_mins: int) -> tuple[dict | None, bool]:
    """Return `(room_row, ready)` when today has an upcoming rollup (`xws` / `huddle`) ahead."""
    today_iso = today.isoformat()
    day_abbr = settings.DAYS[today.weekday()]
    for also in settings.meeting().get("also") or []:
        if not isinstance(also, dict) or day_abbr not in (also.get("days") or []):
            continue
        r_kind = str(also.get("kind") or "")
        b_key = "xws" if r_kind == "rollup" else ("huddle" if r_kind == "huddle" else "")
        if not b_key:
            continue
        at = str(also.get("time_local") or "")
        mins = _hhmm_mins(at)
        if mins is not None and now_mins > mins + 30:
            continue
        block = board.get(b_key) if isinstance(board.get(b_key), dict) else {}
        ready = str(block.get("for_date") or "") == today_iso
        room_name = str(also.get("name") or ("X-Workstream" if b_key == "xws" else "Team Huddle"))
        if ready:
            if b_key == "xws":
                raised = block.get("raised") or []
                detail = f"{len(raised)} ticket{'s' if len(raised) != 1 else ''} to raise" if raised else "nothing to raise"
                words = f"{room_name}: ready ({detail})."
            else:
                ms = block.get("milestones") or []
                words = f"{room_name}: ready ({len(ms)} milestone{'s' if len(ms) != 1 else ''})."
        else:
            words = f"{room_name}: update prep before {at or 'the call'}."
        return (
            {
                "kind": "room",
                "what": words,
                "at": at,
                "goto_standup": True,
                "warn": not ready,
                "plan": [],
            },
            ready,
        )
    return None, True


def _upcoming_room_row(board: dict, today: date) -> tuple[dict | None, bool]:
    """Return `(room_row, ready)` when a speaking room is still ahead today."""
    now_dt = settings.now()
    now_mins = now_dt.hour * 60 + now_dt.minute
    live = next_live(board)
    today_iso = today.isoformat()
    if not live.get("date") or live["date"] != today_iso:
        return _upcoming_rollup_row(board, today, now_mins)
    at = str(live.get("at") or settings.meeting().get("time_local") or "")
    mins = _hhmm_mins(at)
    dur = int(settings.meeting().get("minutes") or 30)
    if mins is not None and now_mins > mins + dur:
        # Today's session has already ended: check whether a later rollup is still ahead.
        return _upcoming_rollup_row(board, today, now_mins)

    tickets = board.get("tickets") or []
    spoken = [t for t in tickets if (t.get("prep") or {}).get("script")]
    meta = script_meta(board)
    ready = bool(spoken) and (not meta.get("for_date") or meta["for_date"] == today_iso)
    room_name = str(live.get("title") or settings.meeting().get("name") or live.get("name") or "Standup")

    if ready:
        asks = [
            str(t.get("ref") or "")
            for t in spoken
            if (t.get("prep") or {}).get("client_ask_needed") is not False
            or (t.get("prep") or {}).get("decisions")
        ]
        ask_part = (
            f"{len(asks)} ask{'s' if len(asks) != 1 else ''} ({', '.join(asks[:2])})"
            if asks
            else "status-only"
        )
        words = f"{room_name}: {len(spoken)} ticket{'s' if len(spoken) != 1 else ''} ready ({ask_part})."
        if live.get("bring"):
            words += " Bring: " + "; ".join(str(b) for b in live["bring"]) + "."
    else:
        words = f"{room_name}: script needs updating before {at or 'the call'}."

    return (
        {
            "kind": "room",
            "what": words,
            "at": at,
            "goto_standup": True,
            "warn": not ready,
            "plan": live.get("timetable") or [],
        },
        ready,
    )


def _desk_action_rows(board: dict, written_do: dict | None) -> tuple[list[dict], int]:
    """Concrete actions sitting on the owner's desk right now."""
    tickets = board.get("tickets") or []
    index = index_items(tickets)
    soon = next_live(board)
    todos: list[dict] = []
    chases: list[dict] = []

    for ref, item, st in tracked(tickets):
        if st["closed"]:
            continue
        due_txt, due_kind = when_tag(item, st, soon)
        if st["state"] == "todo" and not blocked_on(item, index):
            has_draft = any(B.unsent(d) for d in B.drafts(item))
            item_id = str(item.get("id") or "")
            extra_why = ""
            if (
                written_do
                and item_id in [str(x) for x in (written_do.get("items") or [])]
                and written_do.get("what")
            ):
                extra_why = _one_sentence(str(written_do["what"]), 120)
            bits = []
            if has_draft:
                bits.append("draft ready to send")
            if due_txt and due_txt != "Do now":
                bits.append(due_txt.lower())
            if item.get("est_minutes"):
                bits.append(f"{item['est_minutes']}m")
            tail = f" ({' · '.join(bits)})" if bits else ""
            text = extra_why or f"{item.get('title', '')}{tail}."
            todos.append(
                {
                    "kind": "do",
                    "what": text,
                    "items": [item_id],
                    "ref": ref,
                    "has_draft": has_draft,
                }
            )
        elif st["state"] == "waiting" and due_kind == "now":
            item_id = str(item.get("id") or "")
            who = st.get("who") or "them"
            chases.append(
                {
                    "kind": "chase",
                    "what": f"{item.get('title', '')}: waiting on {who} ({due_txt.lower()}).",
                    "items": [item_id],
                    "ref": ref,
                }
            )

    drifts: list[dict] = []
    for t in tickets:
        should = B.status_behind(t)
        if should:
            now_st = str((t.get("asana") or {}).get("status") or "unset")
            drifts.append(
                {
                    "kind": "drift",
                    "what": f"Asana Status says {now_st}, should be {should}.",
                    "ref": str(t.get("ref") or ""),
                }
            )

    rows = todos[:2] + chases[:2] + drifts[:1]
    return rows, len(todos)


def _silence_radar(board: dict, today: date) -> list[dict]:
    """Open client tickets assigned to the owner with 5+ days since the last visible update."""
    owners = settings.owner_aliases()
    out: list[dict] = []
    for t in board.get("tickets") or []:
        asana = t.get("asana") or {}
        if asana.get("completed") or t.get("closed") or t.get("no_ticket_yet"):
            continue
        assignee = str(asana.get("assignee") or "").strip().lower()
        if not assignee or (owners and not any(o in assignee for o in owners)):
            continue
        has_unsent_client = any(
            B.unsent(d) and d.get("language") == settings.language().get("client")
            for i in (t.get("items") or [])
            if i.get("state") in ("todo", "hold", "waiting")
            for d in B.drafts(i)
        )
        if has_unsent_client:
            continue
        last_day: date | None = None
        for ev in t.get("events") or []:
            who = str(ev.get("who") or "")
            where = str(ev.get("where") or "")
            what = str(ev.get("what") or "")
            if "KT-" in where:
                continue
            by_us = _is_owner(who) or "kraken" in who.lower()
            on_ticket = "asana" in where.lower() or "ticket" in what.lower() or "commented" in what.lower()
            status_only = what.lower().startswith(("changed the ticket's status", "status set from"))
            if by_us and on_ticket and not status_only:
                d = _as_date(ev.get("on"))
                if d and (last_day is None or d > last_day):
                    last_day = d
        if last_day is not None:
            age = (today - last_day).days
            if age >= 5:
                out.append(
                    {
                        "kind": "watch",
                        "ref": str(t.get("ref") or ""),
                        "what": f"{age}d since Kraken's last comment on this ticket: post a short status update before day 7.",
                    }
                )
    return out[:2]


def _milestone_and_prio_radar(board: dict, today: date) -> list[dict]:
    """Upcoming milestone phases (<=3 days) and prioritization cutoffs (<=2 days)."""
    out: list[dict] = []
    open_ids = {
        str(i.get("id"))
        for t in (board.get("tickets") or [])
        for i in (t.get("items") or [])
        if not item_state(i)["closed"]
    }
    for ms in board.get("milestones") or []:
        if not isinstance(ms, dict) or ms.get("status") == "done":
            continue
        for p in ms.get("phases") or []:
            if not isinstance(p, dict) or p.get("state") not in ("now", "blocked"):
                continue
            due_d = _as_date(p.get("due"))
            if due_d is None:
                continue
            left = (due_d - today).days
            if left <= 3:
                when = "today" if left == 0 else ("overdue" if left < 0 else f"in {left}d ({short_when(due_d.isoformat())})")
                p_items = [str(x) for x in (p.get("items") or []) if str(x) in open_ids]
                out.append(
                    {
                        "kind": "watch",
                        "what": f"{ms.get('name', 'Milestone')}: {_one_sentence(str(p.get('what') or ''), 78)} due {when}.",
                        "items": p_items[:2],
                    }
                )

    ps = prioritize.summary(board, today)
    nxt = ps.get("next") or {}
    if nxt and ps.get("raise"):
        ask_by = _as_date(nxt.get("ask_by"))
        if ask_by is not None and 0 <= (ask_by - today).days <= 2:
            to_raise = [r.get("kt", "") for r in ps["raise"] if r.get("kt")]
            when_w = "today" if ask_by == today else short_when(ask_by.isoformat())
            out.append(
                {
                    "kind": "watch",
                    "what": f"Prioritization cutoff {when_w} {nxt.get('cutoff_at', '')}: {len(to_raise)} build(s) to raise ({', '.join(to_raise[:3])}).",
                }
            )
    return out[:2]


def _valid_written_lines(board: dict, state: str) -> tuple[dict | None, list[dict]]:
    """Filter `board['brief']['lines']` so lines pointing only at closed items never linger."""
    if state == "stale":
        return None, []
    open_ids = {
        str(i.get("id"))
        for t in (board.get("tickets") or [])
        for i in (t.get("items") or [])
        if not item_state(i)["closed"]
    }
    raw = [r for r in ((board.get("brief") or {}).get("lines") or []) if isinstance(r, dict) and r.get("what")]
    written_do: dict | None = None
    kept_watch: list[dict] = []
    for row in raw:
        nums = [str(x) for x in (row.get("items") or [])]
        # Drop any written brief line whose named items have all closed or gone to waiting when it says `do`.
        if nums and not any(n in open_ids for n in nums):
            continue
        k = row.get("kind", "watch")
        if k == "do":
            if written_do is None:
                written_do = row
        elif k in ("watch", "big", "alert"):
            # Keep non-stale human/context watch lines (e.g. OOO dates or cross-client shifts),
            # trimming any item numbers that already closed.
            clean = dict(row)
            if nums:
                clean["items"] = [n for n in nums if n in open_ids]
            kept_watch.append(clean)
    return written_do, kept_watch


def build_rows(board: dict) -> tuple[list[dict], dict]:
    """Assemble the prioritized Morning Radar rows and live header counts."""
    today = date.today()
    brief = board.get("brief") or {}
    age, state = freshness(brief.get("built_at", ""), brief.get("for_date", ""))
    if state == "stale":
        age = ""

    rows: list[dict] = []

    # 0. Urgent sub-hour alert, if any.
    alert = board.get("alert") or {}
    if isinstance(alert, str):
        alert = {"what": alert}
    if alert.get("what"):
        rows.append(
            {
                "kind": "alert",
                "what": str(alert["what"]),
                "source_url": str(alert.get("source_url") or ""),
            }
        )

    written_do, written_watch = _valid_written_lines(board, state)

    # 1. External replies & moves since the last working day.
    ext_moves = _external_moves(board, today)
    rows.extend(ext_moves)

    # 2. Today's upcoming speaking room (only while still ahead) + live desk actions.
    room_row, room_ready = _upcoming_room_row(board, today)
    desk_rows, n_todo = _desk_action_rows(board, written_do)
    if room_row and not room_ready:
        rows.append(room_row)
    rows.extend(desk_rows)
    if room_row and room_ready:
        rows.append(room_row)

    # 3. Radar & watchpoints: approaching deadlines, silence traps, OOO notes, fresh news.
    radar = _milestone_and_prio_radar(board, today) + _silence_radar(board, today)
    seen_refs_what = {(r.get("ref", ""), r.get("what", "")) for r in rows + radar}
    for w in written_watch:
        key = (w.get("ref", ""), w.get("what", ""))
        if key not in seen_refs_what:
            radar.append(w)
            seen_refs_what.add(key)

    fresh_cut = _fresh_news_iso(today)
    news = board.get("news") or board.get("watch") or []
    fresh_news = [
        r for r in sorted(news, key=lambda x: str(x.get("on") or ""), reverse=True)
        if str(r.get("on") or "") >= fresh_cut
    ]
    for n_row in fresh_news[:1]:
        rows_text = f"{n_row.get('topic', '')}: {_one_sentence(str(n_row.get('why') or n_row.get('what') or ''), 110)}".strip(": ")
        radar.append(
            {
                "kind": "big",
                "what": rows_text,
                "source_url": str(n_row.get("source_url") or ""),
                "news_topic": str(n_row.get("topic") or ""),
            }
        )

    rows.extend(radar)
    rows = rows[:CAP]

    stats = {
        "age": age,
        "state": state,
        "n_replies": len(ext_moves),
        "n_todo": n_todo,
        "room_row": room_row,
        "room_ready": room_ready,
    }
    return rows, stats


def gist(row: dict) -> str:
    """First clean thought of the leading row, shown when the card is collapsed."""
    return _one_sentence(str(row.get("what") or ""), 96)


def render(board: dict, refs: dict[str, str]) -> str:
    """Render the `This morning` radar card at the top of the work view."""
    tickets = board.get("tickets") or []
    news = board.get("news") or board.get("watch") or []
    rows, stats = build_rows(board)
    if not rows and not tickets and not news:
        return ""

    out = []
    for row in rows:
        kind = row.get("kind", "watch")
        ref = str(row.get("ref") or "")
        anchor_id = refs.get(ref, "")
        jobs = "".join(
            f'<a class="br-job" href="#{esc(anchor_id)}" data-job-link="{esc(num)}">#{esc(num)}</a>'
            for num in (row.get("items") or [])
        )
        tag = (
            f'<a class="br-ref" href="#{esc(anchor_id)}">{esc(ref)}</a>'
            if ref and anchor_id
            else ""
        )
        goto_btn = (
            '<a class="btn br-goto" href="#prep-need" data-goto="standup">'
            f'{"Open prep" if not row.get("warn") else "Check prep"} &rarr;</a>'
            if row.get("goto_standup")
            else ""
        )
        out.append(
            f"""
      <li class="br-l {esc(kind)}{" is-warn" if row.get("warn") else ""}">
        <span class="br-k">{esc(WORD.get(kind, kind))}</span>
        <span class="br-w">{tag}{esc(row.get("what"))}
          {f'<span class="br-jobs">{jobs}</span>' if jobs else ""}
          {goto_btn}
          {link_btn(row.get("source_url", ""), "Source") if row.get("source_url") else ""}
        </span>
        {f'<span class="br-at">{esc(at_words(row.get("at")))}</span>' if row.get("at") else ""}
        {timetable(row.get("plan") or [])}
      </li>"""
        )

    if not out:
        out.append(
            '<li class="br-l quiet"><span class="br-k">All clear</span>'
            '<span class="br-w">No overnight replies, no urgent chases, and nothing waiting on your hand right now.</span></li>'
        )

    shown_topics = {r.get("news_topic") for r in rows if r.get("news_topic")}
    more = [
        r for r in sorted(news, key=lambda r: str(r.get("on") or ""), reverse=True)
        if r.get("topic") not in shown_topics
    ]
    if more:
        out.append(
            f"""
      <li class="br-l more">
        <details class="br-more">
          <summary>{len(more)} programme note{"" if len(more) == 1 else "s"} around your tickets<span class="fold-hint"></span></summary>
          <ul>{"".join(news_li(r) for r in more)}</ul>
        </details>
      </li>"""
        )

    # Header status pills: glanceable in 2 seconds even without reading the rows.
    n_rep = stats["n_replies"]
    n_todo = stats["n_todo"]
    room_row = stats["room_row"]
    pills = [
        f'<span class="brief-pill {"moves" if n_rep else "quiet"}">'
        f'{f"{n_rep} recent repl" + ("y" if n_rep == 1 else "ies") if n_rep else "Quiet overnight"}</span>',
        f'<span class="brief-pill {"mine" if n_todo else "quiet"}">{n_todo} with you</span>',
    ]
    if room_row:
        at_lbl = esc(room_row.get("at") or "")
        ready = stats["room_ready"]
        pills.append(
            f'<a class="brief-pill room{" warn" if not ready else ""}" href="#prep-need" data-goto="standup">'
            f'{at_lbl + " " if at_lbl else ""}{"prep ready" if ready else "needs prep"} &rarr;</a>'
        )

    lead = rows[0] if rows else {"kind": "watch", "what": "Nothing urgent on your desk."}
    rest = max(0, len(rows) - 1)
    note_url = str((board.get("meeting_note") or {}).get("url") or "").strip()
    note_chip = (
        f'<a class="brief-note" href="{esc_url(note_url)}" target="_blank" rel="noopener">Last meeting note</a>'
        if note_url.startswith(("http://", "https://"))
        else ""
    )
    age = stats["age"]
    state = stats["state"]

    return f"""
    <details class="brief{" old" if state == "old" else ""}" id="brief"
             data-remember="brief" open>
      <summary>
        <span class="brief-k">This morning</span>
        <span class="brief-pills">{"".join(pills)}</span>
        {f'<span class="brief-age">{esc(age)}</span>' if age else ""}
        {note_chip}
        <span class="fold-hint"></span>
        <span class="brief-sum">
          <b>{esc(WORD.get(lead.get("kind", ""), ""))}</b> {esc(gist(lead))}
          {f'<i>+{rest} more</i>' if rest else ""}</span>
      </summary>
      <ul class="br">{"".join(out)}</ul>
    </details>"""


def news_li(r: dict) -> str:
    """One background programme note, folded out of the main morning view."""
    said = (r.get("what") or "").strip()
    return f"""
            <li><b>{esc(r.get("topic"))}</b>
              <span class="br-why">{esc(r.get("why"))}</span>
              {f'<details class="br-rest"><summary>Details</summary>{esc(said)}</details>' if said else ""}
              <span class="br-on">{esc(day_words(r.get("on", "")))}</span>
              {link_btn(r.get("source_url", ""), "Source") if r.get("source_url") else ""}
            </li>"""


def timetable(rows: list[dict]) -> str:
    """The running order of an onsite day, when present."""
    out = [
        f'<li{MINE if r.get("mine") else ""}><b>{esc(r.get("at"))}</b>'
        f'{esc(r["what"])}</li>'
        for r in rows if r.get("what")
    ]
    return f'<ul class="br-plan">{"".join(out)}</ul>' if out else ""


def at_words(at: str) -> str:
    """A line's time as the owner reads it: '11:00', or 'Fri 16:23' if not today."""
    try:
        when = datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return str(at or "")
    if when.date() == date.today():
        return when.strftime("%H:%M")
    return when.strftime("%a %H:%M")
