#!/usr/bin/env python3
"""Render the board into markdown.

The HTML page is for reading. This markdown file is for working: it is what a
chat in this repo reads when the owner says "draft the reply to Sato-san" or
"check the codebase for X". Keep it dense, keep every link, and keep the ticket
grouping so a single block is enough context to act on.

Usage:
    python3 render_desk_md.py state/board.json output/desk.md
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import board as B
import calendar_sync
import people
import render_milestone
import render_prioritize
import render_standup
import settings

from render import item_state as state_of
from render import proposed
from render import (
    apply_glossary,
    blocked_on,
    due_words,
    index_items,
    item_order,
    next_live,
    plain,
    sessions,
    tracked,
    when_words,
)


# Questions asked from the page, by what they were asked about. Here so that a
# chat picking this file up knows what has already been asked and answered on a
# job, and does not contradict an answer the owner is looking at.
ASKED: dict[str, list[dict]] = {}


def load_asks(path: Path) -> None:
    ASKED.clear()
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("asks", [])
    except (OSError, ValueError):
        return
    for row in rows:
        if row.get("ref"):
            ASKED.setdefault(row["ref"], []).append(row)


def render_asks(ref: str) -> list[str]:
    """What the owner asked from the page, so a chat does not answer it differently.

    A follow-up says so, because "why" as a standalone question in this file
    would read as a question about the job.
    """
    out = []
    for a in ASKED.get(ref, []):
        if not a.get("answer") or a.get("state") == "cancelled":
            continue
        lead = "Followed up" if a.get("parent") else "Asked"
        # The ACTION line is a button on the page, not prose; what the owner did with
        # it is the part a chat needs.
        body, _ = proposed(a["answer"])
        out += [
            f"**{lead} {a.get('asked_at', '')}:** {a.get('question', '')}",
            "",
            f"> {body.replace(chr(10), chr(10) + '> ')}",
            *([f"> *{a['acted_on']}*"] if a.get("acted_on") else []),
            "",
        ]
    return out


def link(label: str, url: str) -> str:
    return f"[{label}]({url})" if url else label


def block(title: str, rows: list[str]) -> list[str]:
    return [f"### {title}", "", *rows, ""] if rows else []


def first_sentence(text: str) -> str:
    """One sentence, for table cells. The rest of the why sits under the action."""
    head = text.split(". ")[0].strip()
    return head if head.endswith(".") else f"{head}."


def client_language_name() -> str:
    """The client's language as a word for a draft heading: "Japanese", "German"."""
    code = str(settings.language().get("client") or "").lower()
    return LANGUAGE_NAMES.get(code, code.upper() if code else "Client language")


LANGUAGE_NAMES = {
    "ja": "Japanese", "en": "English", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "nl": "Dutch", "pt": "Portuguese",
    "pl": "Polish", "ko": "Korean", "zh": "Chinese",
}


def render_draft(d: dict, st: dict | None = None) -> list[str]:
    if not d:
        return []
    # "ja" is the historical value for a draft in the client's language.
    lang = client_language_name() if d.get("language") == "ja" else "English"
    gone = (st and st["state"] not in {"todo", "hold"}) or d.get("sent") or d.get("posted")
    head = (
        f"**Sent {str(d.get('sent_at') or '')[11:16] or (st or {}).get('at', '')}** to {d.get('target', '')}"
        if gone
        else f"**Draft ({lang})** for {d.get('target', '')}"
    )
    # A client-language draft's text is in body_ruby (body_en is its translation,
    # folded below); an English draft's text is in body_en with no body_ruby. Show the right
    # one as the main body, or an English draft prints an empty code block.
    is_ja = d.get("language") == "ja"
    if is_ja:
        body, translation = plain(d.get("body_ruby", "")), d.get("body_en")
    else:
        body, translation = plain(d.get("body_en") or d.get("body_ruby") or d.get("body", "")), None
    out = [
        head + (f" ({link('open thread', d['link'])})" if d.get("link") else ""),
        "",
        "```",
        body,
        "```",
        "",
    ]
    if translation:
        out += ["<details><summary>English</summary>", "", "```", translation, "```", "", "</details>", ""]
    return out


STAGES = ("refining", "queued", "building", "review", "released", "verified")
STAGE_WORDS = {
    "refining": "Refining",
    "queued": "Queued",
    "building": "Building",
    "review": "In review",
    "released": "Released",
    "verified": "Verified",
}


def build_lines(internal: dict) -> list[str]:
    """The Kraken build under the status, in the same order as the page.

    A chat asked "where is HOLD-RELEASE" has to be able to answer "queued, nobody
    on it" without opening Asana, and has to see the line it may repeat to the
    client without inventing one.
    """
    b = internal.get("build") or {}
    if not b:
        if internal.get("none_yet"):
            return [f"**Kraken build.** {internal['none_yet']}", ""]
        return []
    at = STAGES.index(b["stage"]) if b.get("stage") in STAGES else -1
    rail = " > ".join(
        f"**{STAGE_WORDS[s]}**" if n == at else STAGE_WORDS[s]
        for n, s in enumerate(STAGES)
    )
    facts = [f"Engineer {b.get('engineer') or 'nobody yet'}"]
    if b.get("size"):
        facts.append(f"size {b['size']}")
    if b.get("refined_by"):
        facts.append(f"refined by {b['refined_by']}")
    if b.get("feature_flag") == "Yes":
        facts.append("behind a feature flag")
    if b.get("moved_on"):
        facts.append(f"last moved {b['moved_on']}")
    out = [
        f"**Kraken build {b.get('kt', '')}, {b.get('asana_status', '')}.** "
        f"{b.get('waiting_on', '')}",
        "",
        f"- Stage: {rail}",
        f"- {', '.join(facts)}",
    ]
    if b.get("safe_to_say"):
        out.append(f"- What {settings.client_short()} can be told: {b['safe_to_say']}")
    return out + [""]


def closes_lines(rows: list[dict]) -> list[str]:
    """What still has to fall before the ticket closes, gates nobody owns included."""
    if not rows:
        return []
    mark = {"done": "x", "now": " ", "blocked": " ", "next": " "}
    lines = []
    for r in rows:
        st = r.get("state", "next")
        who = f" ({r.get('who')})" if r.get("who") else ""
        tail = f" {r['note']}" if r.get("note") else ""
        flag = "" if st in {"done", "next"} else f" **{st}**"
        lines.append(f"- [{mark.get(st, ' ')}] {r.get('what', '')}{who}{flag}{tail}")
    done = sum(1 for r in rows if r.get("state") == "done")
    return block(f"What is left before this closes ({done} of {len(rows)} done)", lines)


TIMELINE = 8


def render_ticket(t: dict, index: dict[str, dict] | None = None) -> list[str]:
    index = index if index is not None else {
        str(i.get("id")): i for i in t.get("items", [])
    }
    out: list[str] = [
        f"## {t.get('ref', '')}: {t.get('title_en', '')}",
        "",
    ]
    if t.get("title_ja"):
        out += [f"**{t['title_ja']}**", ""]
    if t.get("no_ticket_yet"):
        out.append(f"- No Asana ticket yet: {t['no_ticket_yet']}")
    else:
        out += [
            f"- Asana: {link(t.get('title_ja') or t.get('title_en') or 'ticket', t.get('asana_url', ''))}",
            f"- Asana status: {(t.get('asana') or {}).get('status', 'unknown')}, "
            f"{(t.get('asana') or {}).get('section', 'no section')}, "
            f"priority {(t.get('asana') or {}).get('priority', 'unset')}",
        ]

    internal = t.get("internal_ticket") or {}
    if internal.get("name"):
        out.append(
            f"- Internal build ticket, never mentioned to {settings.client_short()}: "
            f"{link(internal['name'], internal.get('url', ''))}"
        )
    # The plain-English "what this is about" from prep.issue, above Where it stands.
    issue = (t.get("prep") or {}).get("issue")
    if isinstance(issue, list) and issue:
        out += ["", f"_{' '.join(issue)}_", ""]
    out += ["", t.get("where_it_stands", ""), ""]
    out += build_lines(internal)
    out += closes_lines(t.get("closes_when", []))

    terms = [
        f"- **{tm.get('term', '')}**"
        + (f" ({plain(tm['say'])})" if tm.get("say") else "")
        + f": {tm.get('means', '')} {link('source', tm.get('source_url', ''))}"
        for tm in t.get("terms", [])
    ]
    out += block("Words and shorthand on this ticket", terms)

    changed = []
    # The newest moves only. The whole history can run to fifty moves on one ticket, and a
    # chat reading this file wants where it is, not how it got here.
    events = sorted(t.get("events", []), key=lambda x: (x.get("on", ""), x.get("at", "")))
    if len(events) > TIMELINE:
        changed.append(
            f"_{len(events) - TIMELINE} earlier moves are on the board: "
            "`./digest.py --everything`._"
        )
        changed.append("")
        events = events[-TIMELINE:]
    for c in events:
        when = f"{c.get('on', '')} {c.get('at', '')}".strip()
        changed.append(f"- **{when} {c.get('who', '')}** {c.get('what', '')}")
        if c.get("so_what"):
            changed.append(f"  So: {c['so_what']}")
        changed += [
            f"  Source: {c.get('where', '')} {link('link', c.get('source_url', ''))}",
            "",
        ]
    out += block("Timeline", changed)

    actions: list[str] = []
    for a in sorted(t.get("items", []), key=lambda x: item_order(x, index)):
        mins = f", {a['est_minutes']} min" if a.get("est_minutes") else ""
        hold = a.get("hold") or {}
        st = state_of(a)
        behind = blocked_on(a, index) if st["state"] in {"todo", "hold"} else ""
        active = st["state"] in {"todo", "hold"} and not behind
        label = f"AFTER {behind}" if behind else st["label"].upper()
        head = f"{a.get('id', '-')}. {a.get('title', '')} [{label}{mins}]"
        if st["closed"]:
            # Finished is finished: the number and how it ended, nothing more.
            # Printed whole these were most of a 600KB file. --item N has the rest.
            note = f" {st['note']}" if st.get("note") else ""
            actions.append(f"- {head} {st.get('at', '')}.{note}")
            continue
        if active:
            actions += [f"#### {head}", ""]
        else:
            actions += [f"<details><summary>{head}</summary>", ""]
        note = f" {st['note']}" if st.get("note") else ""
        if behind:
            lead = index.get(behind) or {}
            actions += [
                f"> **Queued behind job {behind}.** Nothing to do here until "
                f"\u201c{lead.get('title', 'that one')}\u201d closes.",
                "",
            ]
        if st["state"] == "waiting":
            waits = a.get("waits_on") or {}
            owed = f" They owe: {waits['what']}." if waits.get("what") else ""
            chase = f" {due_words(waits['chase_on'])}" if waits.get("chase_on") else ""
            tail = f" {st['note']}." if st.get("note") else ""
            when = f"Sent {st['at']}. " if st.get("sent") else ""
            actions += [
                f"> {when}Nothing further from you until "
                f"{st.get('who', 'they')} answers.{owed}{tail}{chase}",
                "",
            ]
        if hold:
            revisit = f" Chase on {hold['revisit']}." if hold.get("revisit") else ""
            actions += [
                f"> **Do not send this yet.** {hold.get('why', '')} "
                f"Wait for: {hold.get('until', '')}.{revisit}",
                "",
            ]
        prep = a.get("prepared") or {}
        if prep:
            built = f" ({prep['built_at']})" if prep.get("built_at") else ""
            actions += [f"**Prepared for you**{built}", ""]
            if prep.get("what"):
                actions += [prep["what"], ""]
            if prep.get("conclusion"):
                actions += [f"> **Bottom line:** {prep['conclusion']}", ""]
            tbl = prep.get("table") or {}
            if tbl.get("rows"):
                cols = tbl.get("columns", [])
                actions.append("| " + " | ".join(cols) + " |")
                actions.append("|" + "---|" * len(cols))
                for row in tbl["rows"]:
                    cells = [
                        str(c.get("text", "") if isinstance(c, dict) else c)
                        .replace("|", "\\|")
                        .replace("\n", " ")
                        for c in row
                    ]
                    actions.append("| " + " | ".join(cells) + " |")
                actions.append("")
            for f in prep.get("findings", []):
                actions.append(f"- {f}")
            if prep.get("findings"):
                actions.append("")
            for note in prep.get("notes", []):
                if note.get("heading") and note.get("body"):
                    actions += [f"**{note['heading']}:** {note['body']}", ""]
            if prep.get("unanswered"):
                actions.append("**Still unanswered**")
                actions.append("")
                for q in prep["unanswered"]:
                    actions.append(f"- {q}")
                actions.append("")
            earlier = [e for e in prep.get("earlier") or [] if isinstance(e, dict)]
            if earlier:
                actions += [f"_{len(earlier)} earlier details, taken out by later rewrites: "
                            "under Earlier detail on the card._", ""]
            files = [f for f in prep.get("files", []) if f.get("path") or f.get("url")]
            if files:
                actions += [
                    "Files it made: "
                    + ", ".join(
                        link(
                            f.get("label") or f.get("path") or f["url"],
                            f.get("path") or f["url"],
                        )
                        for f in files
                    ),
                    "",
                ]
            meeting = prep.get("meeting_use") or {}
            if meeting:
                actions += [
                    f"**Meeting version:** {meeting.get('summary', 'Use the What I say in the room section below.')}",
                    "",
                ]
            srcs = [s for s in prep.get("sources", []) if s.get("url")]
            if srcs:
                actions += [
                    "Built from: "
                    + ", ".join(link(s.get("label", "source"), s["url"]) for s in srcs),
                    "",
                ]
        if a.get("where_it_stands") and not st["closed"]:
            actions += [f"**Where it stands:** {a['where_it_stands']}", ""]
        steps = a.get("steps", [])
        if steps:
            actions.append("**Do this**")
            actions.append("")
            for n, b in enumerate(steps, 1):
                # Ticked on the page: struck through, so a chat reading this
                # file knows which step the owner is on without asking.
                when = B.step_done(a, b)
                actions.append(f"{n}. ~~{b}~~ (done {when[:10]})" if when else f"{n}. {b}")
            actions.append("")
        actions.append(
            f"Do it in: {a.get('where', '')} {link('open', a.get('link', ''))}"
        )
        actions.append("")
        if a.get("done_when") and not st["closed"]:
            actions += [f"**Finished when:** {a['done_when']}", ""]
        if a.get("why"):
            actions += [f"**Why it matters:** {a['why']}", ""]
        if a.get("progress_note"):
            actions += [f"> Already happened: {a['progress_note']}", ""]
        if a.get("committed_to"):
            actions += [f"**You committed this to {a['committed_to']}.**", ""]
        if a.get("blocked_by"):
            actions += [f"Blocked by: {a['blocked_by']}", ""]
        if a.get("source_quote"):
            actions += [
                "",
                f"> {a['source_quote']}",
                f"> {link('source', a.get('source_url', ''))}",
            ]
        actions.append("")
        for d in [a.get("draft") or {}] + list(a.get("drafts") or []):
            actions += render_draft(d, st)
        actions += render_asks(f"item:{a.get('id')}")
        if not active:
            actions += ["</details>", ""]
    out += block("To do on this ticket", actions)

    decisions = []
    for d in t.get("open_decisions", []):
        decisions.append(f"- **{d.get('question', '')}**")
        for o in d.get("options", []):
            decisions.append(f"  - {o}")
        decisions.append(
            f"  - Decided by: {d.get('owner', '')} {link('source', d.get('source_url', ''))}"
        )
    out += block("Still undecided", decisions)

    threads = [
        f"- {link(th.get('label', 'thread'), th.get('url', ''))}: "
        f"{th.get('where', '')}. Last: {th.get('last_from', '')}, "
        f"{th.get('last_at', '')}. {th.get('gist', '')}"
        for th in t.get("threads", [])
    ]
    out += block("Every conversation this lives in", threads)
    out += block("Asked about this ticket", render_asks(f"ticket:{t.get('ref')}"))
    out += render_prep(t.get("prep") or {})

    return out


def render_prep(prep: dict) -> list[str]:
    """The speaking half, in plain text: what the owner says and still asks.

    `{word|reading}` markup is stripped, because this file is read by an agent
    and by the owner in a chat, and the reading markup helps neither of them.
    """
    if not prep.get("script") and not prep.get("open_questions"):
        return []
    rows: list[str] = []
    if prep.get("issue"):
        rows += [f"- {b}" for b in prep["issue"]]
        if prep.get("why_it_matters"):
            rows.append(f"- Why it matters: {prep['why_it_matters']}")
        rows.append("")
    cons = prep.get("consequences") or {}
    labels = (
        ("fix_covers", "The fix covers"),
        ("falls_outside", "It does not cover"),
        ("accumulates", "So this piles up"),
        ("who_owns_it", "Owned by"),
        ("done_means", "Cleanup means"),
        ("still_open", "Still undecided"),
    )
    rows += [f"- **{label}:** {cons[key]}" for key, label in labels if cons.get(key)]
    if cons:
        rows.append("")
    for d in prep.get("decisions", []):
        rows.append(f"- **Settle in the room:** {d.get('need', '')}")
        if d.get("why"):
            rows.append(f"  Why now: {d['why']}")
        if d.get("fallback"):
            rows.append(f"  If they will not: {d['fallback']}")
    if prep.get("decisions"):
        rows.append("")
    for b in prep.get("script", []):
        rows += [
            f"**{b.get('heading', '')} ({b.get('heading_en', '')})**",
            "",
            "```",
            *[plain(line.get("ja_ruby", "")) for line in b.get("lines", [])],
            "```",
            "",
        ]
        rows += [f"- {line.get('en', '')}" for line in b.get("lines", [])]
        rows.append("")
    for p in prep.get("pushback", []):
        rows.append(f"- **If they say:** {p.get('they_say', '')}")
        rows.append(f"  Answer: {plain(p.get('say_ja', ''))}")
        if p.get("say_en"):
            rows.append(f"  ({p['say_en']})")
    if prep.get("pushback"):
        rows.append("")
    for q in prep.get("open_questions", []):
        rows.append(f"- **Ask {q.get('who', settings.client_short())}:** {q.get('en', '')}")
        rows.append(f"  {plain(q.get('ja_ruby', ''))}")
    if prep.get("unknowns"):
        rows.append("")
        rows += [f"- Check first: {u}" for u in prep["unknowns"]]
    return block("What I say in the room", rows)


def render_xws(data: dict) -> list[str]:
    """The programme-wide rollup (board field `xws`), markup stripped. Only the
    biggest items from the owner's workstream surface here, so it is a short
    list, and an empty one is a fact worth stating rather than a section to
    drop."""
    xws = data.get("xws") or {}
    if not xws.get("for_date") and not xws.get("headline") and not xws.get("raised"):
        return []
    when = when_words(xws.get("for_date", ""), xws.get("at", ""))
    rows: list[str] = []
    if xws.get("headline"):
        rows += [f"**{xws['headline']}**", ""]
    raised = xws.get("raised", [])
    if not raised:
        ws = settings.workstream() or "your work"
        rows.append(f"Nothing from {ws} is big enough to raise this week.")
    for r in raised:
        rows.append(f"- **{r.get('ref', '')}**"
                    + (f": {r['why']}" if r.get("why") else ""))
        for line in r.get("say", []):
            ja = plain(line.get("ja_ruby", ""))
            if ja:
                rows.append(f"  > {ja}")
            if line.get("en"):
                rows.append(f"  {line['en']}")
    title = render_standup.rollup_name() + (f", {when}" if when else "")
    return block(title, rows)


def render_huddle(data: dict) -> list[str]:
    """The team huddle: a short internal update, on-my-plate plus real
    blockers only. English, since it is a Kraken-side room, not the client's."""
    h = data.get("huddle") or {}
    if not h.get("for_date") and not h.get("plate") and not h.get("blockers"):
        return []
    when = when_words(h.get("for_date", ""), h.get("at", ""))
    rows: list[str] = ["**On my plate**"]
    rows += [f"- {x}" for x in h.get("plate", [])]
    rows += ["", "**Blockers**"]
    blockers = h.get("blockers", [])
    rows += [f"- {x}" for x in blockers] if blockers else ["No blockers."]
    title = render_standup.huddle_name() + (f", {when}" if when else "")
    return block(title, rows)


def render_meetings(data: dict) -> list[str]:
    """Today's and the next working day's meetings, from `state/calendar.json`.

    So a chat asked "what's my afternoon" or "where's the weekly review link" can
    answer from this file like the page does, and knows which notes are unread.
    """
    stored = calendar_sync.load_raw()
    events = stored.get("events") or []
    if not events:
        return []
    today = date.today()
    ahead = today + timedelta(days=3 if today.weekday() == 4 else 1)
    out = ["## Meetings", "",
           f"From Google Calendar, read {calendar_sync.seen_at() or stored.get('fetched_at', '?')}.", ""]
    for day in (today, ahead):
        rows = [e for e in events if e.get("date") == day.isoformat()]
        out.append(f"**{'Today' if day == today else day.strftime('%a %-d %b')}**"
                   + ("" if rows else ": nothing"))
        for e in rows:
            span = e["at"] + (f"-{e['until']}" if e.get("until") else "")
            tags = ", ".join(filter(None, [
                f"{settings.client_short()} in the room" if e.get("client") else "",
                {"tentative": "maybe", "needsAction": "not answered"}.get(e.get("answer", ""), ""),
            ]))
            links = " ".join(
                ([link("Join", e["join"])] if e.get("join") else [])
                + [link("Notes", n["url"]) for n in (e.get("notes") or [])[:1] if n.get("url")]
            )
            out.append(f"- {span} {'**' + e['title'] + '**' if e.get('work') else e['title']}"
                       + (f" ({tags})" if tags else "") + (f" {links}" if links else ""))
        out.append("")
    unread = calendar_sync.unread_notes(data)
    if unread:
        out += ["**Notes not folded in yet:** " + "; ".join(
            f"{e['date']} {e['title'].strip()}" for e in unread[:6]) + ". The next refresh reads them.", ""]
    return out


def render(data: dict) -> str:
    pretty = date.today().strftime("%A %-d %B %Y")
    # Holders said the way the page says them, so the two files agree.
    people.load(data)
    tickets = data.get("tickets", [])
    _index = index_items(tickets)
    total = sum(
        i.get("est_minutes") or 0
        for t in tickets
        for i in t.get("items", [])
        if state_of(i)["state"] == "todo" and not blocked_on(i, _index)
    )

    out = [
        f"# {settings.desk_name()}, {pretty}",
        "",
        f"Board last checked {data.get('checked_at', 'never')}. "
        f"Numbers stay with an item until it is closed, so 'do 4' means the same "
        f"thing tomorrow.",
        "",
    ]

    alert = data.get("alert") or {}
    if isinstance(alert, str):
        alert = {"what": alert}
    if alert.get("what"):
        out += [
            f"> **Needs you now.** {alert['what']} "
            f"{link('source', alert.get('source_url', ''))}",
            "",
        ]

    live = next_live(data)
    if live.get("date"):
        when = when_words(live.get("date", ""), live.get("at", ""))
        line = f"> **Next up: {live.get('title') or live.get('name')}, {when}.**"
        if live.get("place"):
            line += f" {live['place']}."
        if live.get("focus"):
            line += f" {live['focus']}"
        out += [line, ""]
        for row in live.get("timetable", []):
            if row.get("what"):
                mark = " **<-**" if row.get("mine") else ""
                out.append(f"> - {row.get('at', '')} {row['what']}{mark}")
        if live.get("timetable"):
            out.append("")
    for sess in sessions(data):
        if sess.get("skipped"):
            out += [
                f"> **No {sess.get('name', 'standup').lower()} on "
                f"{sess.get('date', '')}.** {sess.get('reason', '')} "
                "Anything that was waiting for it has to move into Asana.",
                "",
            ]
            continue
        if sess.get("date") == live.get("date") and sess.get("at") == live.get("at"):
            continue
        when = when_words(sess.get("date", ""), sess.get("at", ""))
        line = f"> **After that: {sess.get('title') or sess.get('name')}, {when}.**"
        if sess.get("place"):
            line += f" {sess['place']}."
        if sess.get("focus"):
            line += f" {sess['focus']}"
        out += [line, ""]
        for thing in sess.get("bring", []):
            out.append(f"> - Bring: {thing}")
        if sess.get("bring"):
            out.append("")

    out += render_huddle(data)
    out += render_xws(data)

    index = index_items(tickets)
    rows = tracked(tickets)
    if rows:
        counts: dict[str, int] = {}
        for _, a, st in rows:
            # Only work that would otherwise read "with you" diverts. An item
            # waiting on someone is already described by who holds it.
            if st["state"] == "todo" and blocked_on(a, index):
                counts["queued"] = counts.get("queued", 0) + 1
                continue
            key = "finished" if st["closed"] else st["state"]
            counts[key] = counts.get(key, 0) + 1
        summary = ", ".join(
            f"{counts[k]} {word}"
            for k, word in (
                ("todo", "with you"),
                ("queued", "queued behind another job"),
                ("hold", "not yet"),
                ("waiting", "with someone else"),
                ("finished", "finished"),
            )
            if counts.get(k)
        )
        out += [
            "## To do",
            "",
            f"**{summary}.**"
            + (f" About {total} min of work still with you." if total else ""),
            "",
        ]
        for ref, a, st in rows:
            if st["closed"]:
                continue
            bits = []
            if st["state"] == "waiting":
                who, since = st.get("who", "them"), st.get("at", "")
                bits.append(
                    f"sent {since}, with {who}"
                    if st.get("sent")
                    else f"with {who} since {since}"
                )
            elif st["closed"]:
                bits.append(f"{st['label'].lower()} {st.get('at', '')}")
            elif st["state"] == "hold":
                bits.append(f"not yet, until {(a.get('hold') or {}).get('until', '')}")
            else:
                behind = blocked_on(a, index)
                bits.append(f"queued behind {behind}" if behind else "with you")
                if a.get("est_minutes"):
                    bits.append(f"{a['est_minutes']} min")
            if st.get("note"):
                bits.append(st["note"])
            tail = "; ".join(b for b in bits if b)
            out.append(
                f"- **{a.get('id', '')}. {ref}: {a.get('title', '')}** ({tail})"
            )
        out.append("")
        mine = [
            (r, a)
            for r, a, s in rows
            if s["state"] == "todo" and not blocked_on(a, index)
        ]
        if not mine:
            out += ["Nothing is with you right now.", ""]
        else:
            out += ["**With you, in order:**", ""]
            for ref, a in mine:
                out.append(
                    f"{a.get('id', '')}. **{ref}: {a.get('title', '')}** "
                    f"{first_sentence(a.get('why', ''))}"
                )
            out.append("")

    milestones = render_milestone.markdown(data)
    if milestones:
        out += milestones.splitlines() + [""]

    rounds = render_prioritize.markdown(data)
    if rounds:
        out += rounds.splitlines() + [""]

    out += render_meetings(data)

    asked = render_asks("board")
    if asked:
        out += ["## Asked about the desk", "",
                "Questions about the day rather than one job, and what came back.",
                ""] + asked + [""]

    done = [t for t in tickets if (t.get("asana") or {}).get("completed") or t.get("closed")]
    for t in tickets:
        if t not in done:
            out += render_ticket(t, index)
    if done:
        out += ["## Closed on both sides", "",
                "Finished in Asana. `./digest.py --everything` has the record.", ""]
        out += [f"- {t.get('ref', '')}: {t.get('title_en', '')} {link('Asana', t.get('asana_url', ''))}"
                for t in done]
        out.append("")

    news = data.get("news") or data.get("watch") or []
    if news:
        out += [f"## {settings.client_short()} news", "",
                "Not your tickets. Things that move them.", ""]
        for w in news:
            line = f"- **{w.get('topic', '')}**"
            if w.get("what"):
                line += f" {w['what']}"
            if w.get("why"):
                line += f" _{w['why']}_"
            out.append(f"{line} {link('source', w.get('source_url', ''))}")
        out.append("")

    gaps = data.get("gaps") or []
    if gaps:
        out += ["## Open gaps", ""]
        if len(gaps) > 5:
            out += [f"_{len(gaps) - 5} older gaps are on the board._", ""]
        out += [f"- {g}" for g in gaps[-5:]]
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    args = sys.argv[1:]
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
    load_asks(src.parent / "asks.json")
    # Write then rename, so a reader never catches the file half written. The
    # pid keeps two overlapping renders off each other's temp file.
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    tmp.write_text(render(apply_glossary(data)), encoding="utf-8")
    os.replace(tmp, dest)
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
