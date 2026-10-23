#!/usr/bin/env python3
"""Render the board into one self-contained HTML file with two views.

The desk view answers "what do I do", grouped by ticket because that is how the
work gets done. The standup view answers "what do I say at the standup", in the order
the meeting walks the board. Same tickets, same file, one source of truth, and
switching between them costs a keystroke.

Usage:
    python3 render_desk.py state/board.json output/desk.html
    python3 render_desk.py --error "message" output/desk.html
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import render_standup
import render_brief
import calendar_sync
import render_chat
import people
import render_milestone
import render_prioritize
import render_dialogs
import render_path
import render_week
import settings
from render import (
    stamp,
    asset,
    prefs,
    status_button,
    ticket_tag,
    urgency_of,
    sub_line,
    ASKED,
    SPARK,
    answered,
    apply_glossary,
    ask_block,
    blocked_on,
    code,
    day_words,
    due_words,
    esc,
    esc_url,
    furi,
    index_items,
    item_order,
    item_state as state_of,
    pressing,
    link_btn,
    load_asks,
    next_live,
    opening,
    people_json,
    pill,
    plain,
    script_json,
    script_meta,
    script_session,
    section,
    sessions,
    tracked,
    short_when,
    when_tag,
    when_words,
)



def anchor(ref: str) -> str:
    """A stable URL-safe id for a mostly-Japanese tag."""
    ascii_part = re.sub(r"[^0-9A-Za-z]+", "", ref)
    digest = hashlib.md5(ref.encode("utf-8")).hexdigest()[:6]
    return f"t-{ascii_part}{digest}" if ascii_part else f"t-{digest}"


GROUPS = {"todo": "mine", "hold": "blocked", "waiting": "theirs"}


def track_row(
    ref: str,
    item: dict,
    st: dict,
    refs: dict[str, str],
    sess: dict | None,
    queued_behind: str = "",
) -> str:
    mins = item.get("est_minutes")
    sub = sub_line(st, item)
    if queued_behind:
        sub = f"after {esc(queued_behind)}" + (f" &middot; {sub}" if sub else "")
    group = "shut" if st["closed"] else GROUPS.get(st["state"], "")
    due, due_kind = when_tag(item, st, sess)
    # A clock is running on this one: it is the owner's to start now, or its chase date is
    # today. Either reads red, so the time-critical row stands out of the list.
    hot = not st["closed"] and (due_kind == "now" or "today" in (due or "").lower())
    classes = " ".join(c for c in ("tr", group, "tr-hot" if hot else "") if c)
    return f"""
      <li class="{classes}" data-job="{esc(item.get("id", ""))}">
        <span class="tr-rank" title="Job {esc(item.get("id", "-"))}. It keeps this
        number until it closes, so &ldquo;done {esc(item.get("id", "-"))}&rdquo; in
        a chat is enough.">{esc(item.get("id", "-"))}</span>
        <span class="tr-tag">{esc(ref)}</span>
        <a class="tr-title" href="#{esc(refs.get(ref, anchor(ref)))}">{esc(item.get("title"))}
          {f'<span class="tr-note">{sub}</span>' if sub else ""}</a>
        <span class="when {due_kind}">{esc(due)}</span>
        {status_button(item, st, tone="grey" if queued_behind and not st["closed"] else "")}
        <span class="tr-min">{f"{esc(mins)} min" if mins and not st["closed"] else ""}</span>
      </li>"""


def render_track(
    tickets: list[dict], refs: dict[str, str], sess: dict | None = None
) -> str:
    """The one list the owner works from: what is theirs, what is not, what is finished."""
    rows = tracked(tickets)
    if not rows:
        return ('<div class="panel" style="padding:18px 22px"><p class="empty">'
                "Nothing open across any ticket. Enjoy it.</p></div>")

    counts = {"todo": 0, "hold": 0, "waiting": 0, "closed": 0}
    index = index_items(tickets)
    waiting_cold, queued, shut = [], [], []
    today_rows, week_rows, later_rows = [], [], []
    done_today = 0
    for ref, item, st in rows:
        # Only work that would otherwise read "with you" diverts into the queue.
        # An item waiting on someone is already described by who holds it.
        behind = blocked_on(item, index) if st["state"] == "todo" else ""
        row = track_row(ref, item, st, refs, sess, behind)
        if st["closed"]:
            counts["closed"] += 1
            shut.append(row)
            done_today += str(item.get("closed_at", ""))[:10] == date.today().isoformat()
            continue
        if behind:
            queued.append(row)
            continue
        counts[st["state"]] = counts.get(st["state"], 0) + 1
        # A job with someone else and no chase date is nothing the owner can move
        # today, so it folds under the live list rather than padding it. Anything they own,
        # or that carries a chase date or a room to raise it in, stays up top.
        _, due_kind = when_tag(item, st, sess)
        if st["state"] == "waiting" and due_kind == "none":
            waiting_cold.append(row)
        elif st["state"] == "todo":
            # The owner's own work goes by the urgency they gave it, the same
            # reading the week strip makes; every job of theirs reads "Do now",
            # which says it is theirs rather than when.
            {"today": today_rows, "this-week": week_rows}.get(
                urgency_of(item), later_rows).append(row)
        elif due_kind == "now":
            today_rows.append(row)     # a chase that is due is today's work
        else:
            later_rows.append(row)

    summary = ", ".join(
        f"{n} {word}"
        for n, word in (
            (counts["todo"], "with you"),
            (len(queued), "queued behind"),
            (counts["hold"], "not yet"),
            (counts["waiting"], "with someone else"),
        )
        if n > 0
    )
    # Jobs sitting with someone else, with no date pulling them back, fold under
    # the live list. They are still on the page, one click away, but they are not
    # the thing the owner opens the board to do.
    waiting_block = (
        f"""
    <details class="track-closed" data-remember="waiting">
      <summary><b>{len(waiting_cold)} with others</b>, no chase date, nothing to
      do on them yet <span class="fold-hint"></span></summary>
      <ul style="list-style:none;margin:0;padding:0">{"".join(waiting_cold)}</ul>
    </details>"""
        if waiting_cold
        else ""
    )
    # Work that cannot start until something else closes is not a choice, so it
    # folds too. It stays on the page because "what comes after this" is worth
    # one click, and it is wrong to make the owner rediscover the queue each morning.
    queued_block = (
        f"""
    <details class="track-closed" data-remember="queued">
      <summary><b>{len(queued)} queued</b> behind the jobs above, nothing to do
      on them yet <span class="fold-hint"></span></summary>
      <ul style="list-style:none;margin:0;padding:0">{"".join(queued)}</ul>
    </details>"""
        if queued
        else ""
    )
    # Finished work folds away by default. It is a record, not a list.
    closed_block = (
        f"""
    <details class="track-closed" data-remember="done">
      <summary><b>{len(shut)} finished</b> on these tickets, kept for the record
      <span class="fold-hint"></span></summary>
      <ul style="list-style:none;margin:0;padding:0">{"".join(shut)}</ul>
    </details>"""
        if shut
        else ""
    )
    # Today, then the rest of the week, then what is moving without the owner: the
    # order Todoist draws a day in, so the top of the list is always the next
    # thing to press. Every row is still the same row, with its number.
    def group(title: str, rows: list[str], hint: str = "", empty: str = "") -> str:
        if not rows:
            if not empty:
                return ""
            return (
                f'<li class="tr-sec empty"><b>{esc(title)}</b><span>0</span></li>'
                f'<li class="tr-empty">{esc(empty)}</li>'
            )
        return (
            f'<li class="tr-sec"><b>{esc(title)}</b><span>{len(rows)}</span>'
            f'{f"<i>{esc(hint)}</i>" if hint else ""}</li>' + "".join(rows)
        )

    open_now = len(today_rows)
    total_day = open_now + done_today
    pct = round(100 * done_today / total_day) if total_day else 0
    progress = (
        f'<div class="track-day" title="Closed today, out of what was due today">'
        f'<span class="track-bar"><i style="width:{pct}%"></i></span>'
        f'<span><b>{done_today}</b> of {total_day} done today</span></div>'
        if total_day
        else ""
    )
    return f"""
  <div class="track" id="todo">
    <div class="track-head"><h2>To do</h2>
    <span>{esc(summary)}</span></div>
    {progress}
    <ul class="track-list" style="list-style:none;margin:0;padding:0">
      {group("Today", today_rows, "due now, or chase due", "Nothing due today. Pick from this week, or chase.")}
      {group("This week", week_rows)}
      {group("With others, or held", later_rows, "each has a chase date or a room to raise it in")}
    </ul>
    {waiting_block}
    {queued_block}
    {closed_block}
  </div>"""


def render_terms(rows: list[dict]) -> str:
    """The words on this ticket, ours and theirs.

    `say` is what the client calls it, and it earns its own line because hearing
    the client's word in the room and reading the English here are the same lookup.
    """
    if not rows:
        return ""
    items = ""
    for r in rows:
        say = (
            f'<span class="term-ja">{furi(r.get("say", ""))}</span>'
            if r.get("say")
            else ""
        )
        items += (
            f'<div class="term"><dt>{esc(r.get("term"))}{say}</dt>'
            f'<dd>{esc(r.get("means"))} '
            f'{link_btn(r.get("source_url", ""), "Source") if r.get("source_url") else ""}'
            "</dd></div>"
        )
    ja = sum(1 for r in rows if r.get("say"))
    count = f"{len(rows)} terms" + (f", {ja} in Japanese" if ja else "")
    return section(
        "Words and shorthand on this ticket",
        f'<dl class="terms">{items}</dl>',
        role="ref",
        count=count,
        fold=True,
        hint="what they say, what it means",
    )


def render_threads(rows: list[dict]) -> str:
    if not rows:
        return ""
    out = []
    for r in rows:
        out.append(
            f"""
        <li>
          <span class="thr-where">{esc(r.get("where"))}</span>
          <span class="thr-body">
            <span class="thr-label">{esc(r.get("label"))}</span>
            <span class="thr-gist">{esc(r.get("gist"))}</span>
          </span>
          <span class="thr-when"><b>{esc(r.get("last_from"))}</b>{esc(r.get("last_at"))}</span>
          {link_btn(r.get("url", ""), "Open")}
        </li>"""
        )
    return section(
        "Where this is discussed",
        f'<ul class="thr">{"".join(out)}</ul>',
        role="ref",
        count=f"{len(rows)} threads",
        fold=True,
    )


def event_li(r: dict, show_date: bool) -> str:
    src = r.get("source_url", "")
    day = day_words(r.get("on", "")) if show_date else ""
    when = (
        f'<b>{esc(day)}</b><i>{esc(r.get("at", ""))}</i>'
        if day
        else f'<i>{esc(r.get("at", ""))}</i>'
    )
    return f"""
        <li class="ev">
          <span class="ev-at">{when}</span>
          <span class="ev-body">
            <span class="ev-who">{esc(r.get("who", ""))}</span>
            <span class="ev-what">{esc(r.get("what"))}</span>
            {f'<span class="ev-so">{esc(r.get("so_what"))}</span>' if r.get("so_what") else ""}
            <span class="ev-src">{esc(r.get("where", ""))} {link_btn(src, "Source") if src else ""}</span>
          </span>
        </li>"""


# How many days of timeline a card carries in the page itself. Everything older
# is fetched from /api/events when the owner opens the fold.
#
# 323 events across ten tickets was 247KB of the markup, a quarter of the whole
# page, every one of them inside a fold that is shut on load. That is the cost
# of drawing history nobody asked to see. Six days covers "what moved since the
# last standup", which is the only part of a timeline that gets read daily.
TIMELINE_DAYS = 6


def day_folds(days: dict[str, list[dict]], keys: list[str]) -> str:
    """One fold per day, newest open, oldest at the top so it reads forward."""
    out = []
    for day in keys:
        moves = days[day]
        n = len(moves)
        out.append(
            f"""
        <details class="ev-fold" {"open" if day == keys[-1] else ""}>
          <summary><span class="ev-d">{opening(esc(day_words(day)))}</span>
          <span class="ev-c">{n} {"move" if n == 1 else "moves"}</span>
          <span class="fold-hint"></span></summary>
          <ol class="evs">{"".join(event_li(r, False) for r in moves)}</ol>
        </details>"""
        )
    return "".join(out)


def week_html(data: dict, offset: int) -> str:
    """The week strip for another week, for the page to swap in when the owner flips.

    The card anchors are worked out the way `render()` works them, so a job on
    a flipped-to week still links to its card.
    """
    people.load(data)
    tickets = data.get("tickets", [])
    refs = {t.get("ref", ""): anchor(t.get("ref", "")) for t in tickets}
    return render_week.render(data, tickets, refs, offset)


def earlier_events(rows: list[dict]) -> str:
    """Everything older than the days a card carries, for /api/events.

    The same day folds the card draws, so what arrives looks like what was
    already there rather than a different kind of list.
    """
    if not rows:
        return ""
    ordered = sorted(rows, key=lambda x: (x.get("on", ""), x.get("at", "")))
    days: dict[str, list[dict]] = {}
    for r in ordered:
        days.setdefault(r.get("on", ""), []).append(r)
    keys = list(days)
    older = keys[: -TIMELINE_DAYS] if len(keys) > TIMELINE_DAYS else []
    return day_folds(days, older) if older else ""


def render_events(rows: list[dict], ident: str = "", ref: str = "") -> str:
    """The timeline, one fold per day, oldest at the top so it reads forward.

    The day is the handle: press Yesterday and yesterday closes. The most recent
    day is open because that is the one being asked about, and every other day
    is a line saying how much is behind it.

    The whole thing is shut to begin with. It answers a question the owner asks
    once a week, how did this get here, and its summary line carries the only
    part they need daily, which is whether anything moved today.

    Only the last `TIMELINE_DAYS` days are in the page. Anything older is a
    button that fetches the rest, because history nobody opened was a quarter of
    a one-megabyte page. A card with no server behind it (a page opened from
    disk) keeps everything inline instead, since there would be nothing to
    fetch from.
    """
    if not rows:
        return ""
    ordered = sorted(rows, key=lambda x: (x.get("on", ""), x.get("at", "")))
    days: dict[str, list[dict]] = {}
    for r in ordered:
        days.setdefault(r.get("on", ""), []).append(r)
    keys = list(days)
    shown = keys[-TIMELINE_DAYS:]
    older = keys[: -len(shown)] if len(keys) > len(shown) else []

    more = ""
    if older:
        n = sum(len(days[d]) for d in older)
        more = (
            f'<button type="button" class="ev-more" data-events="{esc(ref)}" '
            f'data-ident="{esc(ident)}">Load the earlier '
            f'{n} {"move" if n == 1 else "moves"}, '
            f'{len(older)} {"day" if len(older) == 1 else "days"}</button>'
            '<noscript><p class="empty">The earlier moves need JavaScript, or '
            "read them in output/desk.md.</p></noscript>"
        )
    latest = len(days[keys[-1]])
    return section(
        "Timeline",
        f'<div class="ev-in">{more}{day_folds(days, shown)}</div>',
        role="log",
        count=f"{latest} {'move' if latest == 1 else 'moves'} {day_words(keys[-1])}",
        hint=f"{len(keys)} {'day' if len(keys) == 1 else 'days'} in all",
        fold=True,
        remember=f"tl-{ident}",
    )


def render_decisions(rows: list[dict]) -> str:
    if not rows:
        return ""
    out = []
    for r in rows:
        opts = "".join(f"<li>{esc(o)}</li>" for o in r.get("options", []))
        out.append(
            f"""
        <div class="dec">
          <div class="dec-q">{esc(r.get("question"))}</div>
          {f"<ul>{opts}</ul>" if opts else ""}
          <p class="dec-owner">Decided by: {esc(r.get("owner"))}
          {link_btn(r.get("source_url", ""), "Source") if r.get("source_url") else ""}</p>
        </div>"""
        )
    return section(
        "Still undecided",
        "".join(out),
        role="warn",
        count=f"{len(rows)} open",
        hint="nobody owns this yet",
    )


def asana_chips(t: dict) -> str:
    """What Asana itself says about this ticket, not what the agent thinks.

    Every value carries its field name, and the field names are Asana's own, so
    what is on the screen matches what the owner sees when they open the ticket.
    """
    a = t.get("asana") or {}
    bits = []
    labelled = (
        ("status", "Status"),
        ("category", "Category"),
        ("priority", "Priority"),
        ("severity", "Severity"),
        ("assignee", "Assignee"),
        ("section", "Section"),
    )
    # An assignee who is not the owner is worth flagging. With no name set up
    # yet there is nothing to compare against, so nothing is flagged.
    first = settings.first_name()
    for key, label in labelled:
        if not a.get(key):
            continue
        odd = key == "assignee" and bool(first) and first not in a[key]
        bits.append(
            f'<span class="tk-chip{" warn" if odd else ""}">'
            f"<b>{label}</b>{esc(a[key])}</span>"
        )
    return "".join(bits)


STAGES = (
    ("refining", "Refining"),
    ("queued", "Queued"),
    ("building", "Building"),
    ("review", "In review"),
    ("released", "Released"),
    ("verified", "Verified"),
)


def build_rail(stage: str) -> str:
    """Six stops from refinement to the client's own verification, current one lit.

    A client ticket does not close when the code lands, it closes when the
    client has checked the bills that came out after it, so the rail runs one stop past
    the release rather than stopping at Done.
    """
    keys = [k for k, _ in STAGES]
    at = keys.index(stage) if stage in keys else -1
    out = []
    for n, (key, label) in enumerate(STAGES):
        cls = "done" if at > -1 and n < at else "at" if n == at else ""
        out.append(f'<li class="{cls}">{esc(label)}</li>')
    return f'<ol class="rail">{"".join(out)}</ol>'


def build_strip(internal: dict) -> str:
    """The Kraken-side build, inside the ticket's status rather than beside it.

    The client cannot see any of this and must never be quoted any of it, but half of
    "where is this ticket" is which stop the build is at and what it is waiting
    on, so it belongs under the short answer rather than in a chip row of its
    own. `none_yet` is the same answer for a ticket with no build raised: an
    empty queue is a fact about the ticket too.
    """
    b = internal.get("build") or {}
    if not b:
        if internal.get("none_yet"):
            return (
                '<p class="bld none"><span class="bld-k">Kraken build</span>'
                f'{esc(internal["none_yet"])}</p>'
            )
        return ""

    # (text, worth flagging). Amber only where the fact is the reason the build
    # is not moving, so the colour keeps meaning something.
    facts = [
        (f"Engineer <b>{esc(b['engineer'])}</b>", False)
        if b.get("engineer")
        else ("Engineer <b>nobody yet</b>", True)
    ]
    if b.get("size"):
        facts.append((f"Size <b>{esc(b['size'])}</b>", False))
    if b.get("refined_by"):
        facts.append((f"Refined by <b>{esc(b['refined_by'])}</b>", False))
    if b.get("feature_flag") == "Yes":
        facts.append(("Ships <b>behind a feature flag</b>", True))
    if b.get("moved_on"):
        facts.append((f"Last moved <b>{esc(day_words(b['moved_on']))}</b>", False))

    client = settings.client_short()
    # The client-safe line.
    safe = (
        f'<p class="bld-safe"><b>What {esc(client)} can be told</b>{esc(b.get("safe_to_say"))}</p>'
        if b.get("safe_to_say")
        else ""
    )
    return f"""
      <div class="bld">
        <div class="bld-head">
          <span class="bld-k">Kraken build</span>
          <span class="bld-kt">{esc(b.get("kt", ""))}</span>
          <span class="bld-state">{esc(b.get("asana_status", ""))}</span>
          <span class="keep">never quote this one to {esc(client)}</span>
          {link_btn(internal.get("url", ""), "Open build ticket")}
        </div>
        <div class="bld-body">
          {build_rail(b.get("stage", ""))}
          <p class="bld-now">{esc(b.get("waiting_on"))}</p>
          <p class="bld-facts">{"".join(
              f'<span class="{"odd" if odd else ""}">{f}</span>' for f, odd in facts
          )}</p>
          {safe}
        </div>
      </div>"""


def render_closes(rows: list[dict], ident: str = "") -> str:
    """Everything that still has to fall before the ticket can be closed.

    The item list says what the owner does next. This says what closing looks
    like, including the gates that are nobody's item: an engineer being
    assigned, a release landing, the client checking the bills that came out
    after it. Without it a
    ticket with no open items reads as finished when it is only quiet.

    Folded, with the next gate and whose it is on the summary line, because that
    one line is the daily question and the other five gates are the monthly one.
    """
    if not rows:
        return ""
    out = []
    for r in rows:
        state = r.get("state", "next")
        note = f'<span class="g-n">{esc(r.get("note"))}</span>' if r.get("note") else ""
        out.append(
            f"""
        <li class="gate {esc(state)}">
          <span class="g-m"></span>
          <span class="g-w">{esc(r.get("what"))}{note}</span>
          <span class="g-who">{esc(r.get("who", ""))}</span>
        </li>"""
        )
    done = sum(1 for r in rows if r.get("state") == "done")
    nxt = next((r for r in rows if r.get("state") != "done"), None)
    waiting = ""
    if nxt:
        who = nxt.get("who", "")
        whose = (
            "yours"
            if who.strip().lower() in settings.owner_aliases()
            else f"with {who}"
            if who
            else ""
        )
        waiting = f"Next: {nxt.get('what', '')}" + (f", {whose}" if whose else "")
        if len(waiting) > 78:
            waiting = waiting[:77].rsplit(" ", 1)[0] + "\u2026"
    return section(
        "What is left before this closes",
        f'<ul class="gates">{"".join(out)}</ul>',
        role="key",
        count=f"{done} of {len(rows)} done",
        hint=waiting,
        fold=True,
        remember=f"gates-{ident}",
    )




def background_drawer(t: dict, ident: str) -> str:
    """How it got here, what the words mean, where it is discussed: one drawer.

    Three folds that were three sections, and all three answer questions the
    owner asks about once a week: how did this get here, what does that client
    term mean, which thread was that in. Shut, they were still three rows of chrome on every card.
    One drawer with a count is one row, and what is inside keeps its own shape.
    """
    inner = "".join(
        [
            render_events(t.get("events", []), ident, t.get("ref", "")),
            render_terms(t.get("terms", [])),
            render_threads(t.get("threads", [])),
        ]
    )
    if not inner.strip():
        return ""
    bits = []
    if t.get("events"):
        n = len(t["events"])
        bits.append(f"{n} move{'s' if n != 1 else ''}")
    if t.get("terms"):
        bits.append(f"{len(t['terms'])} terms")
    if t.get("threads"):
        n = len(t["threads"])
        bits.append(f"{n} thread{'s' if n != 1 else ''}")
    return f"""
      <details class="sub ref bg-drawer" data-remember="bg-{esc(ident)}">
        <summary><h3>Background{f'<span class="n">{esc(", ".join(bits))}</span>' if bits else ""}
          <span class="hint">how it got here, the words, the threads</span></h3>
          <span class="fold-hint"></span></summary>
        <div class="bg-in">{inner}</div>
      </details>"""


def internal_btn(internal: dict) -> str:
    """The Kraken-side ticket, named on hover rather than in the row.

    The name is long, and it is the one string on this page that must never be
    copied into anything addressed to the client, so it does not get to sprawl. A build
    with a strip under Where it stands already carries its own link, so this
    only fires for a linked ticket that has no build detail on it.
    """
    if not internal.get("url") or internal.get("build"):
        return ""
    return (
        f'<a class="btn" href="{esc_url(internal["url"])}" target="_blank" rel="noopener" '
        f'title="{esc(internal.get("name", ""))}">Internal ticket</a>'
    )


def render_ticket(
    t: dict,
    ident: str,
    raise_label: str = "Raise at standup",
    sess: dict | None = None,
    index: dict[str, dict] | None = None,
) -> str:
    internal = t.get("internal_ticket") or {}
    # The warning belongs beside the link it is about. With a build strip that
    # link has moved into Where it stands, and the warning goes with it.
    keep_in = (
        f'<span class="keep">never quote this one to {esc(settings.client_short())}</span>'
        if internal.get("name") and not internal.get("build")
        else ""
    )

    states = [state_of(i) for i in t.get("items", [])]
    mine = sum(1 for s in states if s["state"] == "todo")
    open_n = sum(1 for s in states if not s["closed"])
    if mine:
        posture, tone = pill(f"{mine} with you", "red"), "mine"
    elif open_n:
        posture, tone = pill("Nothing on you", "blue"), "theirs"
    else:
        posture, tone = pill("Clear", "green"), "clear"

    # When there is a script for this ticket, the desk card says so and jumps
    # straight to it, because "what do I say about this" is the next question.
    say_link = (
        f'<a class="btn" href="#{esc(render_standup.anchor(ident))}" '
        f'data-goto="standup">What I say about this</a>'
        if (t.get("prep") or {}).get("script")
        else ""
    )

    return f"""
    <article class="tk {tone}" id="{esc(ident)}">
      <header class="tk-head">
        <div class="tk-id">
          <span class="tag">{esc(ticket_tag(t))}</span>
          {posture}
        </div>
        <h2>{esc(t.get("title_en"))}</h2>
        {f'<p class="tk-ja">{esc(t["title_ja"])}</p>' if t.get("title_ja") else ""}
        <p class="tk-meta">
          {asana_chips(t)}
        </p>
        {f'<p class="tk-noticket"><b>No Asana ticket yet</b>{code(t.get("no_ticket_yet"))}</p>'
         if t.get("no_ticket_yet") else ""}
        <div class="tk-links">
          <span class="lab">Go to</span>
          {link_btn(t.get("asana_url", ""), "Open in Asana")}
          {say_link}
          {internal_btn(internal)}
          {keep_in}
        </div>
      </header>

      {f'<p class="tk-about">{esc(" ".join(t["prep"]["issue"]))}</p>'
       if isinstance((t.get("prep") or {}).get("issue"), list) and t["prep"]["issue"] else ""}

      {section("Where it stands",
               f'<p class="status">{esc(t.get("where_it_stands"))}</p>'
               + build_strip(internal),
               role="key",
               hint="both sides" if internal.get("build") else "the short answer")}

      {render_path.render_path(
          t,
          ident,
          raise_label,
          sess,
          render_standup.anchor(ident),
          index,
          fold=not mine,
      )}
      {render_decisions(t.get("open_decisions", []))}
      {background_drawer(t, ident)}
      <section class="sub ask">{ask_block(
          f'ticket:{t.get("ref")}',
          t.get("ref", "this ticket"),
          ASKED.get(f'ticket:{t.get("ref")}', []),
          "Read prompts/ask.md, then answer this about the "
          f'{t.get("ref", "")} ticket:',
      )}</section>
    </article>"""


def shell(
    title: str,
    body: str,
    view: str = "desk",
    meeting_iso: str = "",
    meeting_label: str = "",
    closed: str = "",
) -> str:
    # The manifest and icon are what let Chrome install this as its own app, with
    # its own Dock tile. They 404 harmlessly when the page is opened from disk.
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" sizes="512x512" href="/icon-512.png">
<link rel="apple-touch-icon" href="/icon-512.png">
<meta name="theme-color" content="#0d1524">
<title>{esc(title)}</title>
<style>{asset("base.css")}{asset("desk.css")}{asset("standup.css")}{asset("kraken.css")}{asset("ukiyoe.css")}{asset("sakura.css")}</style></head>
<body data-view="{esc(view)}" data-meeting="{esc(meeting_iso)}"
      data-meeting-label="{esc(meeting_label)}" data-stamp="{stamp()}" data-board="{stamp(False)}"
      data-closed="{esc(closed)}">
<script id="prefs" type="application/json">{prefs_json()}</script>
<script>
/* The theme, before anything is drawn, so a page never paints plain and then
   repaints indigo. The server's copy (state/prefs.json, in the tag above) wins,
   because it is the same in every browser, port and profile; localStorage is
   only what a page opened from disk falls back on. base.js keeps both. */
(function(){{var p={{}};try{{p=JSON.parse(document.getElementById('prefs').textContent)||{{}}}}catch(e){{}}
window.deskPrefs=p;
try{{
var t=p['desk-theme']||localStorage.getItem('desk-theme');
if(t&&t!=='plain')document.body.dataset.theme=t;
if(t==='kraken')document.querySelector('meta[name=theme-color]').content='#29162f';
if((p['desk-motion']||localStorage.getItem('desk-motion'))==='off')document.body.dataset.motion='off';
}}catch(e){{}}}})();
</script>
{body}
<script>{asset("base.js")}</script></body></html>"""


def prefs_json() -> str:
    """The prefs as a script body: `</` escaped, so no value can close the tag."""
    return script_json(prefs())


# The buttons only appear when serve.py is behind the page, because a file://
# page has nothing to send the click to. serve.py swaps the key in as it serves.
def controls(prep_label: str) -> str:
    return f"""
  <button id="refresh" class="refresh" hidden data-run="/api/refresh"
          data-busy="Refreshing">Refresh</button>
  <button id="prep" class="refresh" hidden data-run="/api/prep"
          data-busy="Writing">{prep_label}</button>
  <button id="changed" class="refresh moved" hidden>Board changed &middot; Reload</button>
  <button id="login" class="refresh" hidden>Log in</button>
  <a id="login-link" class="refresh" hidden target="_blank" rel="noopener">Open sign-in page</a>
  <span id="refresh-note" class="refresh-note"></span>
  <a id="meet-now" class="meet-now" hidden target="_blank" rel="noopener"></a>
<script type="application/json" id="meets">{meets_today()}</script>
<script>
{asset("desk.js")}</script>"""


def meets_today() -> str:
    """Today's meetings that can be joined, for the header's Join chip.

    Every meeting with a Meet link, not only the work ones: the chip is how the
    owner gets into the next room on time, and the room that is not about the
    client still starts at its hour.
    """
    rows = [
        {"t": e["title"], "s": e["start"], "e": e["end"], "j": e["join"], "w": bool(e.get("work"))}
        for e in calendar_sync.events_on(date.today().isoformat())
        if e.get("join")
    ]
    return script_json(rows)

def checked_line(stamp: str) -> str:
    """How long ago the board was last brought up to date, in plain words.

    Short, because it shares the header with the tabs and the buttons, and a
    wrapped header pushes the page around every time it changes.
    """
    if not stamp:
        return "never checked"
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return esc(stamp)
    mins = int((datetime.now().astimezone() - then).total_seconds() // 60)
    if mins < 2:
        return "checked just now"
    if mins < 60:
        return f"checked {mins}m ago"
    if mins < 24 * 60:
        return f"checked {mins // 60}h ago"
    return f"checked {then:%-d %b}"


def is_stale(stamp: str) -> bool:
    """Four hours is long enough for a reply to have landed unseen."""
    try:
        then = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return True
    return (datetime.now().astimezone() - then).total_seconds() > 4 * 3600


def view_tabs(mine: int, has_script: bool, sess: dict) -> str:
    """Two tabs, both named for the client, because a second client may come.

    "<client> my work" and "<client> what I say", from `client.short`, say whose
    work and which half. The client prefix means a second client's pair can sit
    beside them without either name having to change or a tab having to be read
    twice.

    All on one line: the header sits above everything the owner reads all day, so it
    stays as short as it can while still being the most obvious thing up there.
    """
    badge = (
        f'<span class="badge">{mine}</span>'
        if mine
        else '<span class="badge quiet">0</span>'
    )
    when = short_when(sess.get("date", ""), sess.get("at", ""))
    # The kind, not the board's own label for the room: "Onsite today 09:45"
    # rather than "<workstream> onsite today 09:45". Everything on this page is
    # the one workstream, and the words it saves are what keep the bar one line tall.
    room = f'{sess.get("tab") or sess.get("name", "Standup")} {when}'.strip()
    alert = "" if has_script else '<span class="badge">!</span>'
    client = esc(settings.client_short())
    return f"""
  <div class="views" role="tablist" aria-label="Views">
    <button data-view="desk" role="tab" aria-selected="true">
      <span class="k">1</span><span class="lbl">{client} my work</span>{badge}
    </button>
    <button data-view="standup" role="tab" aria-selected="false">
      <span class="k">2</span><span class="lbl">{client} what I say</span>
      <span class="sub">{esc(room)}</span>{alert}
    </button>
  </div>"""


def opening_view(board: dict, has_script: bool) -> str:
    """What to show on load.

    On the morning of a session, with a script already written, the script is
    what the owner opens the laptop for. Every other moment of the day, the work is.
    """
    if not has_script:
        return "desk"
    sess = script_session(board)
    if sess.get("date") != date.today().isoformat():
        return "desk"
    at = sess.get("at") or str(settings.meeting().get("time_local") or "")
    try:
        hour, minute = (int(x) for x in at.split(":")[:2])
    except ValueError:
        hour, minute = 10, 30
    now = datetime.now()
    return "standup" if (now.hour, now.minute) < (hour, minute) else "desk"


def jump_bar(tickets: list[dict], refs: dict[str, str], has_news: bool) -> str:
    """The sections and every ticket, one click away, wherever you are."""
    chips = []
    if has_news:
        chips.append('<a href="#brief">This morning</a>')
    chips.append('<a href="#todo">To do</a>')
    chips.append('<a href="#tickets">Tickets</a>')
    for t in tickets:
        # A ticket with nothing open is a line in the finder, not a stop here.
        if ticket_group(t) != "live":
            continue
        ref = t.get("ref", "")
        mine = sum(1 for i in t.get("items", []) if state_of(i)["state"] == "todo")
        count = f'<span class="c">{mine}</span>' if mine else ""
        chips.append(f'<a class="tkt" href="#{esc(refs[ref])}">{esc(ticket_tag(t))}{count}</a>')
    return f"""
  <nav class="jump">
    <span class="lab">Jump to</span>
    <div class="jump-track">{"".join(chips)}</div>
    <button class="find" data-find type="button">Find <kbd>/</kbd></button>
  </nav>"""


def finder(tickets: list[dict], refs: dict[str, str]) -> str:
    """The index behind the finder, and the box it draws into.

    Tickets and items both go in, because "3", a client term and a colleague's
    name are all things the owner would type to get to the same three cards.
    """
    rows = []
    for t in tickets:
        ref = t.get("ref", "")
        tag = ticket_tag(t)
        title = t.get("title_en", "")
        rows.append(
            {
                "id": refs[ref],
                "tag": tag,
                "label": title,
                "sub": "ticket",
                "view": "desk",
                "hay": f'{tag} {ref} {title} {t.get("title_ja", "")}'.lower(),
            }
        )
        if (t.get("prep") or {}).get("script"):
            rows.append(
                {
                    "id": render_standup.anchor(refs[ref]),
                    "tag": tag,
                    "label": f"What I say about {title}",
                    "sub": "script",
                    "view": "standup",
                    "hay": f"say script {tag} {ref} {title}".lower(),
                }
            )
        for item in t.get("items", []):
            st = state_of(item)
            rows.append(
                {
                    "id": refs[ref],
                    "tag": f'{item.get("id", "-")}',
                    "label": item.get("title", ""),
                    "sub": st["label"].lower(),
                    "view": "desk",
                    "hay": (
                        f'{item.get("id", "")} {item.get("title", "")} {ref} '
                        f'{st.get("who", "")} {st["label"]}'
                    ).lower(),
                }
            )
    index = script_json(rows)
    return f"""
<script type="application/json" id="desk-index">{index}</script>
<div class="pal" id="pal" hidden>
  <div class="pal-box" role="dialog" aria-label="Find">
    <input type="text" placeholder="Ticket, number, or a word from a title"
           aria-label="Find" autocomplete="off">
    <ul role="listbox"></ul>
    <div class="pal-foot"><span><kbd>&uarr;</kbd><kbd>&darr;</kbd> move</span>
    <span><kbd>enter</kbd> go</span><span><kbd>esc</kbd> close</span></div>
  </div>
</div>"""


def ticket_group(t: dict) -> str:
    """Which of the three piles a ticket belongs in.

    "live" is anything with open work on it. "clear" is a ticket where the owner
    has done their part and Asana still has it open, which is worth keeping in sight
    but not worth reading every morning. "closed" is finished on both sides.
    """
    if (t.get("asana") or {}).get("completed") or t.get("closed"):
        return "closed"
    if any(not state_of(i)["closed"] for i in t.get("items", [])):
        return "live"
    return "clear"


def render(data: dict) -> str:
    pretty = date.today().strftime("%a %-d %b")
    # Who the board's names are, first, so every holder below is said one way.
    people.load(data)
    item_index = index_items(data.get("tickets", []))

    # Tickets you owe something on come first. Everything open stays on the page.
    def ticket_order(t: dict) -> tuple:
        """Cards in the order the owner should work them, not the order they arrived.

        Work they owe a person comes before work they owe a room, and both come
        before work with no date on it at all. Within that the number decides,
        so the sequence is stable from one build to the next.
        """
        states = [state_of(i) for i in t.get("items", [])]
        mine = [
            i
            for i in t.get("items", [])
            if state_of(i)["state"] == "todo" and not blocked_on(i, item_index)
        ]
        return (
            0 if mine else
            1 if any(not s["closed"] for s in states) else 2,
            0 if any(i.get("committed_to") for i in mine) else 1,
            0 if any(i.get("at_standup") for i in mine) else 1,
            min((pressing(i) for i in mine), default=9),
            min((i.get("id", 99) for i in t.get("items", [])), default=99),
        )

    tickets = sorted(data.get("tickets", []), key=ticket_order)
    refs = {t.get("ref", ""): anchor(t.get("ref", "")) for t in tickets}
    # "Raise at the onsite" and "Raise at standup" are different instructions,
    # so the pill says which room it means.
    raise_label = f'Raise at {next_live(data).get("name", "standup").lower()}'
    soon = next_live(data)

    groups: dict[str, list[dict]] = {"live": [], "clear": [], "closed": []}
    for t in tickets:
        groups[ticket_group(t)].append(t)

    def cards_for(rows: list[dict]) -> str:
        return "".join(
            render_ticket(t, refs[t.get("ref", "")], raise_label, soon, item_index)
            for t in rows
        )

    cards = cards_for(groups["live"])
    # Closed on both sides is finished: desk.md and the finder still have it.
    for key, title, why in (
        (
            "clear",
            "Done your part, still open in Asana",
            "Nothing here needs you. It stays because Asana has not closed it.",
        ),
    ):
        if groups[key]:
            cards += f"""
    <details class="tk-fold" data-remember="tk-{key}">
      <summary>{esc(title)} <span class="n">{len(groups[key])}</span>
      <span class="q">{esc(why)}</span><span class="fold-hint"></span></summary>
      {cards_for(groups[key])}
    </details>"""

    mine = sum(
        1
        for t in tickets
        for i in t.get("items", [])
        if state_of(i)["state"] == "todo"
    )

    # What a run could not reach. One shut line: most of it is history, and a
    # lapsed login still needs to be seen, so it stays rather than going.
    gaps = data.get("gaps", [])
    gap_block = (
        f"""
    <details class="gaps" data-remember="gaps">
      <summary>Open gaps <span class="n">{len(gaps)}</span>
      <span class="q">what a run could not reach</span><span class="fold-hint"></span></summary>
      <ul>{"".join(f"<li>{esc(g)}</li>" for g in gaps)}</ul>
    </details>"""
        if gaps
        else ""
    )

    meta = script_meta(data)
    built = meta.get("built_at", "")
    has_script = any((t.get("prep") or {}).get("script") for t in tickets)
    # A script written before the last sweep may not know the newest replies.
    stale = bool(has_script and built and built < (data.get("checked_at") or ""))
    prep_label = "Update prep" if has_script else "Write prep"

    # The countdown only makes sense on the day of a session, and an empty value
    # is what tells the page not to draw one at all.
    today_iso = date.today().isoformat()
    on_today = soon.get("date") == today_iso and soon.get("at")
    meeting = (
        f'{today_iso}T{soon.get("at")}:00{settings.offset(date.today())}' if on_today else ""
    )
    meeting_label = soon.get("name", "standup").lower() if on_today else ""

    body = f"""
<header class="top"><div class="top-in">
  <span class="brand"><img src="/icon-192.png" alt="" onerror="this.remove()">
  <span class="brand-k" aria-hidden="true"><i class="brand-mark"></i><i class="brand-word"></i></span>
  <h1>{esc(settings.desk_name())}</h1></span>
  {view_tabs(mine, has_script, script_session(data))}
  <span class="acts">
    <span class="date {"old" if is_stale(data.get("checked_at", "")) else ""}">
      {esc(pretty)} &middot; {esc(checked_line(data.get("checked_at", "")))}</span>
    <span id="countdown"></span>
    {link_btn((data.get("meeting_note") or {}).get("url", ""), "Meeting note")}
    <button class="toggle" id="scriptonly">{esc(render_standup.script_only_words().capitalize())}</button>
    {controls(prep_label)}
    <button class="toggle chat-go" data-chat-toggle type="button" aria-controls="chat" hidden
            title="Ask in the side panel (a)">{SPARK}Ask</button>
    <button class="toggle" data-settings type="button" aria-label="Settings"
            title="Settings">&#9881;</button>
    <button class="toggle" data-help type="button" aria-label="How to use this"
            title="How to use this">?</button>
  </span>
</div></header>
<div class="wrap" id="top">
  <div id="view-desk" role="tabpanel">
    {jump_bar(tickets, refs, True)}
    {render_brief.render(data, refs)}
    {render_milestone.render(data, refs)}
    {render_prioritize.render(data, refs)}
    {render_week.render(data, tickets, refs)}
    {render_track(tickets, refs, soon)}
    {ask_block("board", "the desk", ASKED.get("board", []),
               "Read prompts/ask.md, then answer this about the desk as a "
               "whole rather than one job:")}
    <span id="tickets"></span>
    {cards}
    {gap_block}
  </div>
  <div id="view-standup" role="tabpanel">
    {render_standup.render(data, refs, built, stale)}
  </div>
</div>
{render_chat.render(data, refs)}
{people_json(data)}
{finder([t for t in tickets if ticket_group(t) != "closed"], refs)}
{render_dialogs.help_dialog()}
{render_dialogs.settings_dialog()}"""
    # Which items are closed right now. The page remembers this set, so when
    # `./tick.py 45` in a terminal reloads it, the page can tell which number
    # just fell rather than only that something did.
    shut = ",".join(
        sorted(
            str(i.get("id"))
            for t in tickets
            for i in t.get("items", [])
            if state_of(i)["closed"]
        )
    )
    return shell(
        settings.desk_name(),
        body,
        opening_view(data, has_script),
        meeting,
        meeting_label,
        shut,
    )


def render_error(message: str) -> str:
    body = f"""
<div class="wrap" style="padding-top:40px">
  <div class="err">
    <h1 style="margin:0 0 8px;font-size:20px">The desk did not build</h1>
    <p style="margin:0">The board is still there. Rebuild with
    <code>{esc(settings.command())} open</code>, or run <code>./run_post.sh --force</code> to start
    again from the meeting note. Details below.</p>
    <pre>{esc(message)}</pre>
  </div>
</div>"""
    return shell(f"{settings.desk_name()}, build failed", body)


def main() -> int:
    args = sys.argv[1:]
    if len(args) >= 3 and args[0] == "--error":
        Path(args[2]).write_text(render_error(args[1]), encoding="utf-8")
        return 0
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    src, dest = Path(args[0]), Path(args[1])
    if not src.exists():
        print(f"missing {src}", file=sys.stderr)
        return 1
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"invalid JSON in {src}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict) or "tickets" not in data:
        print(f"{src} is missing the 'tickets' key", file=sys.stderr)
        return 1

    load_asks(src.parent / "asks.json")
    # Write then rename, so a page load reading this file never catches it half
    # written when two renders overlap. The temp name carries the pid because
    # two renders sharing one temp file was its own race: the first rename took
    # the file and the second found nothing to rename.
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    tmp.write_text(render(apply_glossary(data)), encoding="utf-8")
    os.replace(tmp, dest)
    items = sum(len(t.get("items", [])) for t in data.get("tickets", []))
    print(f"wrote {dest} ({len(data['tickets'])} tickets, {items} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
