#!/usr/bin/env python3
"""Shared parts of the desk page: styling, furigana, and the common blocks.

Not a page on its own. `render_desk.py` builds the page from the board and
`render_standup.py` builds the standup view inside it, and both take their
styling, their furigana handling and their item lifecycle from here so the two
views cannot drift apart.
"""

from __future__ import annotations

import html
import json
import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

import agent
import board as B
import people
import settings

RUBY = re.compile(r"\{([^|{}]+)\|([^|{}]+)\}")
KANJI = re.compile(r"[\u4e00-\u9fff]")

# Same four meanings everywhere on the page: yours, blocked, somebody else's,
# closed. Kept in step with the CSS variables of the same names.
TONES = {
    "red": ("#b42318", "#fef4f2", "#fbd2cd"),
    "amber": ("#b25309", "#fff9ed", "#f9dda3"),
    "green": ("#046c46", "#effaf4", "#a8e7c5"),
    "grey": ("#4e5666", "#f4f6fa", "#e5e8ee"),
    "blue": ("#1257c9", "#f0f6ff", "#bedaff"),
}

# An action is with the owner, with somebody else, or finished. Sending a message
# moves it to the middle state rather than closing it, because the reply comes
# back on the same number. Third value is the sort order of the group.
LIFECYCLE = {
    "todo": ("With you", "red", 0),
    "hold": ("Not yet", "amber", 1),
    "waiting": ("Waiting", "blue", 2),
    "done": ("Done", "green", 3),
    "sent": ("Sent", "green", 3),
    "dropped": ("Dropped", "grey", 3),
}
CLOSED_STATES = {"done", "sent", "dropped"}

# Inside a group, the order is what has to happen soonest, not the number. The
# numbers are for typing, and reading the list top to bottom should be the same
# as working down it.
#
# Three values and no more: today, this-week, monitor. A sweep writing anything
# else used to sort as "unknown" and land in the middle of the list silently,
# which is how `this week` (a space, not a hyphen) sat on item 51 unnoticed.
# `urgency_of` normalises instead of trusting, and check.py fails the board if a
# value cannot be normalised, so the failure is loud and at the point of writing.
URGENCY = {"today": 0, "this-week": 1, "monitor": 2}

# What a sweep has actually written and what it meant. Anything not here and not
# in URGENCY is a real mistake rather than a spelling of one.
URGENCY_ALIASES = {
    "this week": "this-week",
    "thisweek": "this-week",
    "week": "this-week",
    "now": "today",
    "urgent": "today",
    "todo": "this-week",
    "waiting": "monitor",
    "watch": "monitor",
}


def urgency_of(item: dict) -> str:
    """The item's urgency as one of the three words, whatever was written."""
    raw = (item.get("urgency") or "").strip().lower()
    if raw in URGENCY:
        return raw
    return URGENCY_ALIASES.get(raw, "this-week")


def pressing(item: dict) -> int:
    return URGENCY[urgency_of(item)]


def index_items(tickets: list[dict]) -> dict[str, dict]:
    """Every item by its number, so `after` can be resolved to the real item."""
    return {
        str(i.get("id")): i for t in tickets for i in t.get("items", [])
    }


def ticket_tag(t: dict) -> str:
    """The ticket's short tag, prefixed with its open item numbers (`2: DD-RETRY`)."""
    ref = str(t.get("ref") or "")
    nums = [
        str(i.get("id"))
        for i in t.get("items", [])
        if not item_state(i)["closed"] and i.get("id") is not None
    ]
    return f"{', '.join(nums)}: {ref}" if nums else ref


def ticket_tags(tickets: list[dict]) -> dict[str, str]:
    """Every ticket's numbered tag by its short ref."""
    return {str(t.get("ref") or ""): ticket_tag(t) for t in tickets}



def blocked_on(item: dict, index: dict[str, dict]) -> str:
    """The number this item is queued behind, when that one is still open.

    Most work on a ticket is a queue rather than a menu: the scope cannot be
    confirmed before the sheet exists, and the sheet cannot go out before the
    questions are settled. Listing all three as things to do invents two
    decisions the owner does not have, so an item names the one it follows in `after`
    and only the front of the queue is a job.

    An `after` pointing at something already closed is spent, and returns "".
    """
    after = item.get("after")
    if not after:
        return ""
    lead = index.get(str(after))
    if lead is None:
        return ""
    if lead.get("state", "todo") in CLOSED_STATES:
        return ""
    return str(after)


def chain_depth(item: dict, index: dict[str, dict], _seen: set[str] | None = None) -> int:
    """How many open items stand in front of this one.

    Depth 0 is workable now. Anything deeper sorts under the item it waits on,
    so reading a ticket top to bottom is the order the work actually happens.
    A board written by hand can point two items at each other, so the walk stops
    on a number it has already passed rather than recursing forever.
    """
    seen = _seen or {str(item.get("id"))}
    lead_id = blocked_on(item, index)
    if not lead_id or lead_id in seen:
        return 0
    seen.add(lead_id)
    return 1 + chain_depth(index[lead_id], index, seen)


def item_order(item: dict, index: dict[str, dict]) -> tuple:
    """The order items read within one ticket.

    Finished work sits at the top, folded and struck through: it is the record
    of what has already happened on the ticket, and it answers "where are we"
    before the list answers "what now". Then the open work in the order it has
    to happen, front of the queue first.
    """
    st = item_state(item)
    ident = int(item.get("id", 99)) if str(item.get("id", "")).isdigit() else 99
    if st["closed"]:
        return (0, item.get("closed_at", ""), ident)
    return (1, chain_depth(item, index), pressing(item), ident)



def furi(raw: str) -> str:
    """Escape text, then turn {漢字|かんじ} into real ruby annotations.

    Only kanji get a reading. Models reliably over-apply the markup and wrap
    katakana in it too, and a katakana word with its own sounds printed above it
    is noise on a line the owner is reading out loud at speed. Dropping the annotation
    here rather than in the prompt means it cannot come back with the next model.
    """
    def one(match: re.Match) -> str:
        base, reading = match.group(1), match.group(2)
        if not KANJI.search(base):
            return base
        return f"<ruby>{base}<rt>{reading}</rt></ruby>"

    return RUBY.sub(one, html.escape(raw or ""))


def esc(raw: Any) -> str:
    return html.escape(str(raw or ""))


SAFE_SCHEME = re.compile(r"^(https?://|mailto:|#|/(?![/\\]))", re.I)


def esc_url(raw: Any) -> str:
    """A link from the board or the calendar, fit for an href.

    `esc()` stops a value leaving the attribute but not `javascript:` running
    inside it, and these URLs are written by agents reading other people's text.
    """
    url = str(raw or "").strip()
    return esc(url) if SAFE_SCHEME.match(url) else "#"


def script_json(obj: Any) -> str:
    """JSON for the body of a `<script type="application/json">` block.

    `json.dumps` leaves `<` alone, so a title holding `</script>` would end the
    block and the rest would be parsed as markup. `<`, `>` and `&` go out as
    unicode escapes, which JSON.parse reads back as the same characters, and
    U+2028 and U+2029 too, so the text stays valid if a block is ever read as script.
    """
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")
# Where a Japanese word can turn up inside a sentence written for the owner. Drafts,
# the terms table and thread labels are left alone: a draft is text that goes to
# somebody, and the terms table is the long version of this gloss already.
GLOSS_FIELDS = {
    "en",
    "title_en",
    "what",
    "why",
    "note",
    "title",
    "topic",
    "hint",
    "text",
    "where_it_stands",
    "progress_note",
    "done_when",
    "so_what",
    "issue",
    "why_it_matters",
    "fix_covers",
    "falls_outside",
    "accumulates",
    "verdict",
    "route",
    "steps",
    "body",
    "findings",
    "conclusion",
    "unanswered",
    "focus",
    "bring",
    "headline",
    "need",
    "fallback",
    "done_means",
    "still_open",
    "who_owns_it",
    "they_say",
    "say_en",
    "unknowns",
    "waiting_on",
    "label",
    "gist",
}
GLOSS_SKIP = {"draft", "terms", "threads", "asks"}
# Each of these holds things the owner reads one at a time: a block of the
# script, a line of the timeline, one job. Each starts the gloss again, because
# the term they need explaining is the one in front of them, and the explanation
# being four blocks up the card is the same as it not being there.
GLOSS_BLOCKS = {
    "items",
    "events",
    "script",
    "news",
    "closes_when",
    "decisions",
    "pushback",
    "open_questions",
    "sessions",
    "other_notes",
    "agenda",
    "unanswered",
    "raised",
}
# The order the page reads a card, so the gloss lands on the first mention they
# actually see rather than on one inside a shut fold.
GLOSS_ORDER = (
    "title_en",
    "title",
    "where_it_stands",
    "issue",
    "why_it_matters",
    "script",
    "items",
    "prepared",
    "decisions",
    "pushback",
    "consequences",
    "closes_when",
    "events",
)


def jp_heavy(text: str) -> bool:
    """Is this a Japanese sentence rather than an English one with a word in it?

    A gloss belongs in the English line. Dropping one into the client-language
    line the owner reads out loud would put an English clause in the middle of a
    sentence they are speaking to the client.
    """
    solid = [c for c in text if not c.isspace()]
    if not solid:
        return False
    return sum(1 for c in solid if CJK.match(c)) / len(solid) > 0.4


def gloss_text(text: str, glossary: dict[str, str], seen: set[str]) -> str:
    """Put the English meaning after the first Japanese term in a line.

    The owner reads the English to know what the client-language line says, and
    a sentence that still has the client's term in the middle of it has not
    translated the part they needed. Once per term per card: the second mention
    is a word they now know,
    and glossing every one of them turns the line back into noise.
    """
    if not text or not CJK.search(text) or jp_heavy(text):
        return text
    # Every occurrence of every term, so a term inside a longer one is left
    # alone: a short term that sits inside a longer compound would, glossed
    # there, break the longer word in half.
    covers = [
        (m.start(), m.end(), len(term))
        for term in glossary
        for m in re.finditer(re.escape(term), text)
    ]
    found = []
    for term in sorted(glossary, key=len, reverse=True):
        if term in seen:
            continue
        size, at = len(term), 0
        while True:
            i = text.find(term, at)
            if i < 0:
                break
            end = i + size
            inside = any(
                a <= i and end <= b and length > size for a, b, length in covers
            )
            if inside:
                at = end
                continue
            # A term followed straight away by Latin letters ("<term>IF") can be
            # the name of a sheet in the client's workbook rather than the term
            # followed by the letters, and a gloss dropped between the two
            # renames their document. A Latin letter or digit straight after the
            # term means the match is part of a longer identifier.
            tail = text[end : end + 1]
            if tail.isascii() and (tail.isalnum() or tail == "_"):
                at = end
                continue
            gloss = glossary[term]
            # The sentence often explains the word itself, as in "<term>
            # reading_date". Saying it twice is worse than not saying it. The
            # test is the one word that carries the gloss: "period" matching
            # "period boundary" would drop the gloss on a line that never says
            # which period it means.
            # The window is at least as long as the gloss, so a long one cannot
            # be re-added past the end of its own words.
            after = text[end : end + max(44, len(gloss) + 8)].lower()
            words = sorted(re.split(r"\W+", gloss.lower()), key=len)
            if words and words[-1] in after:
                seen.add(term)
                break
            found.append((i, end, gloss))
            seen.add(term)
            break
    for _, end, gloss in sorted(found, reverse=True):
        text = f"{text[:end]} ({gloss}){text[end:]}"
    return text


def gloss_tree(node: Any, glossary: dict[str, str], seen: set[str]) -> None:
    """Walk a card and gloss its English, in the order the page reads it.

    `seen` covers one block. A list named in GLOSS_BLOCKS hands each of its
    entries a fresh one, so every script block, every line of the timeline and
    every job explains its own Japanese. Anything nested inside one of those
    entries shares it, which is why two lines of the same block do not both carry
    the same bracket.
    """
    def rank(key: str) -> tuple[int, str]:
        place = GLOSS_ORDER.index(key) if key in GLOSS_ORDER else len(GLOSS_ORDER)
        return place, key

    if isinstance(node, dict):
        for key in sorted(node, key=rank):
            if key in GLOSS_SKIP:
                continue
            value = node[key]
            fresh = key in GLOSS_BLOCKS
            if isinstance(value, str):
                if key in GLOSS_FIELDS:
                    node[key] = gloss_text(value, glossary, seen)
            elif isinstance(value, list):
                if key in GLOSS_FIELDS:
                    node[key] = [
                        gloss_text(v, glossary, seen) if isinstance(v, str) else v
                        for v in value
                    ]
                for entry in node[key]:
                    if not isinstance(entry, str):
                        gloss_tree(entry, glossary, set() if fresh else seen)
            else:
                gloss_tree(value, glossary, set() if fresh else seen)
    elif isinstance(node, list):
        for value in node:
            gloss_tree(value, glossary, seen)


def apply_glossary(board: dict) -> dict:
    """Gloss the whole board once, before either view renders it.

    One pass, and the blocks inside it decide where a gloss starts over. The two
    views are two cards off the same ticket and the script is a third thing
    again, being all that survives Japanese-only mode, so nothing may depend on
    the owner having read the block above.
    """
    glossary = board.get("glossary") or {}
    if not glossary:
        return board
    for ticket in board.get("tickets", []):
        gloss_tree(ticket, glossary, set())
    for key in ("news", "sessions", "other_notes", "script", "meeting_note", "xws", "huddle"):
        if board.get(key):
            gloss_tree(board[key], glossary, set())
    return board


def plain(raw: str) -> str:
    """Strip furigana markup, leaving just the kanji. For copy buttons."""
    return RUBY.sub(r"\1", raw or "")


def code(raw: Any) -> str:
    """Escape, then let `backticks` become code. For instructions to the owner.

    A step that names a dotted setting such as `module.some-setting-name` is
    naming a thing they have to type or search for exactly, and a dotted string in running prose
    is where a typo hides.
    """
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(raw))


def opening(word: str) -> str:
    """First letter up, the rest left alone.

    `capitalize` lowercases everything after the first letter, which turned
    "Mon 24 Aug" into "Mon 24 aug" on every day heading on the page.
    """
    return word[:1].upper() + word[1:]


def answered(raw: Any) -> str:
    """What came back from an ask, as prose rather than as its own source.

    An answer is written the way an answer is written: a bold claim, a couple of
    paragraphs, sometimes a short list. Escaped and dropped in whole it arrived
    on the card wearing its asterisks, which made the one thing on the page the
    owner had asked for the hardest thing on it to read. Only the four marks an answer
    actually uses are honoured, and everything else stays literal.
    """
    text = code(raw)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    # An answer asked in front of the script comes back as the line to read out,
    # furigana and all. Printed with its braces showing it is not readable, which
    # defeats the point of asking for it there.
    text = RUBY.sub(
        lambda m: (
            f"<ruby>{m.group(1)}<rt>{m.group(2)}</rt></ruby>"
            if KANJI.search(m.group(1))
            else m.group(1)
        ),
        text,
    )
    out, items = [], []

    def flush() -> None:
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        for ln in lines:
            bullet = re.match(r"^(?:[-*\u2022]|\d+\.)\s+(.*)$", ln)
            if bullet:
                items.append(bullet.group(1))
            else:
                flush()
                out.append(f"<p>{ln}</p>")
        flush()
    return "".join(out)


def item_state(item: dict) -> dict:
    """Where one item sits: its state, label, tone, and who is holding it.

    The item carries its own state, because the board outlives the day it was
    written on. A hold is inferred when nothing has been recorded yet.
    """
    state = item.get("state") or ("hold" if item.get("hold") else "todo")
    waits = item.get("waits_on") or {}
    who = waits.get("who", "")
    at = item.get("state_at") or waits.get("since", "")
    label, tone, order = LIFECYCLE.get(state, LIFECYCLE["todo"])
    # The way the owner says them, "Sato-san" rather than "Taro Sato, <client>",
    # once the render has worked out who the board's names are. As written otherwise.
    who = people.said(who) if who else who
    if state == "waiting" and who:
        label = f"Waiting on {who}"
    return {
        "state": state,
        "label": label,
        "tone": tone,
        "order": order,
        "closed": state in CLOSED_STATES,
        "who": who,
        "at": at,
        "note": item.get("state_note", ""),
        "sent": bool(item.get("sent_by_you")),
    }


def tracked(tickets: list[dict]) -> list[tuple[str, dict, dict]]:
    """Every item as (ticket ref, item, state), yours first, finished last.

    Within a group, an item queued behind another sorts under it, so the top of
    the list is always something the owner can pick up without reading further.
    """
    index = index_items(tickets)
    rows = [
        (t.get("ref", ""), i, item_state(i))
        for t in tickets
        for i in t.get("items", [])
    ]
    return sorted(
        rows,
        key=lambda r: (
            r[2]["order"],
            chain_depth(r[1], index),
            pressing(r[1]),
            r[1].get("id", 99),
        ),
    )


# What the owner is preparing for. Usually the main standup in `config.json`
# `meeting`, sometimes an onsite, which is the same tickets with a great deal
# more riding on each one. Built at call time, because the server lives across
# an edit to config.json and the rooms are the owner's, not the code's.
def kinds() -> dict[str, dict]:
    """Every kind of session the board can name, from the owner's rooms.

    `standup` is the main standup (`meeting`), `onsite` and `workshop` are
    generic, and each programme-wide or review meeting in `meeting.also` adds
    its own kind (its `kind`, or its name made into one). An entry there may
    carry `tab` and `what` to say how the page names it; otherwise the name is
    used, less any bracketed aside, and "the weekly review" for a weekly.
    """
    m = settings.meeting()
    at = str(m.get("time_local") or "")
    c = settings.section("client")
    client = str(c.get("short") or c.get("name") or "the client")
    out = {
        "standup": {
            "name": "Standup",
            "tab": "Standup",
            "at": at,
            "what": f'the {m.get("minutes") or 30} minutes at {at}' if at else "the standup",
        },
        "onsite": {
            "name": "Onsite",
            "tab": "Onsite",
            "at": "",
            "what": f"a day in the room with {client}",
        },
        "workshop": {
            "name": "Workshop",
            "tab": "Workshop",
            "at": "",
            "what": "the session",
        },
    }
    for room in m.get("also") or []:
        if not isinstance(room, dict) or not room.get("name"):
            continue
        name = re.sub(r"\s*\([^)]*\)\s*$", "", str(room["name"])).strip() or str(room["name"])
        kind = str(room.get("kind") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
        if not kind or kind in ("standup", "onsite", "workshop"):
            continue
        out[kind] = {
            "name": name,
            "tab": str(room.get("tab") or ("Weekly" if kind == "weekly" else name)),
            "at": str(room.get("time_local") or ""),
            "what": str(room.get("what") or ("the weekly review" if kind == "weekly" else name)),
        }
    # A board that names a weekly with no weekly configured still renders it.
    out.setdefault("weekly", {"name": "Weekly", "tab": "Weekly", "at": "",
                              "what": "the weekly review"})
    return out


def a_session(raw: dict) -> dict:
    """Fill in what the kind of session implies, leaving what was written."""
    out = dict(raw)
    kind = out.get("kind") or "standup"
    every = kinds()
    spec = every.get(kind, every["standup"])
    out["kind"] = kind
    out["name"] = out.get("label") or spec["name"]
    out["tab"] = spec["tab"]
    out["at"] = out.get("at") or spec["at"]
    out["what"] = spec["what"]
    return out


def sessions(board: dict) -> list[dict]:
    """Everything the owner still has to speak at, soonest first.

    A list rather than one field, because an onsite on Wednesday and a standup
    on Monday are two different rooms with two different scripts, and a board
    that can only hold one of them forgets whichever is further away.

    Boards written before this change said `next_standup` and `standup`, so
    those are read too and nothing has to be regenerated to render.
    """
    raw = board.get("sessions")
    if raw is None:
        raw = []
        old = board.get("next_standup") or {}
        if old.get("date"):
            raw.append({"kind": "standup", **old})
        built = board.get("standup") or {}
        if built.get("date") and built.get("date") != old.get("date"):
            raw.append({"kind": "standup", "date": built["date"], "at": built.get("at")})
    today = date.today().isoformat()
    live = [a_session(s) for s in raw if (s.get("date") or "") >= today]
    return sorted(live, key=lambda s: (s.get("date", ""), s.get("at", "")))


def next_live(board: dict) -> dict:
    """The next session that is actually happening."""
    return next((s for s in sessions(board) if not s.get("skipped")), {})


def script_meta(board: dict) -> dict:
    """When the script was written, and the line it opens on."""
    meta = dict(board.get("script") or board.get("standup") or {})
    meta.setdefault("for_date", meta.get("date", ""))
    return meta


def script_session(board: dict) -> dict:
    """The session the current script was written for.

    If the script names a date, that is the room it describes, even when a
    different session is now sooner. Otherwise, the next live one. Two rooms
    can share a day (the morning standup and an afternoon weekly review),
    so the script's `at` picks between them when it names one.
    """
    meta = script_meta(board)
    wanted = meta.get("for_date")
    if wanted:
        same_day = [s for s in sessions(board) if s.get("date") == wanted]
        match = next((s for s in same_day if s.get("at") == meta.get("at")), None)
        match = match or next(iter(same_day), None)
        if match:
            return match
        return a_session({"kind": "standup", "date": wanted})
    return next_live(board)


def when_words(iso: str, at: str = "") -> str:
    """2026-08-26 becomes "Wed 26 Aug", with the time when there is one."""
    if not iso:
        return ""
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return iso
    words = day.strftime("%a %-d %b")
    today = date.today()
    if day == today:
        words = "today"
    elif (day - today).days == 1:
        words = "tomorrow"
    return f"{words}, {at}" if at else words


def short_when(iso: str, at: str = "") -> str:
    """The same day in as few characters as a tab can spare: "Wed 14:00".

    A bare weekday only means one thing inside the current week. Said on a Friday
    about the Wednesday after, "Chase Wed" reads as the Wednesday just gone and
    the owner treats it as overdue, or as this week and leaves it. Six days is inside the
    old rolling window and still the wrong week, so the week is what decides:
    within it the weekday is enough, past it the date comes too, "Wed (9/30)".
    """
    if not iso:
        return ""
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return iso
    today = date.today()
    left = (day - today).days
    words = {0: "today", 1: "tomorrow"}.get(left, day.strftime("%a"))
    if left > 6 or left < 0:
        words = day.strftime("%-d %b")
    elif left > 1 and day.isocalendar()[:2] != today.isocalendar()[:2]:
        words = f"{words} ({day.month}/{day.day})"
    return f"{words} {at}".strip()


def due_words(iso: str) -> str:
    """A chase or revisit date as a phrase, aged so one that has passed says so.

    The chip at the top of a card is only three words, so the sentence underneath
    is where "and you have not done it" fits. Both come from the stored date, and
    neither rewrites it: the board records when the owner meant to chase, and the page is
    responsible for saying how that went.
    """
    if not iso:
        return ""
    try:
        day = date.fromisoformat(DATE_IN.search(iso).group(0))
    except (AttributeError, ValueError):
        # Half of these are a condition rather than a date, "only if they raise
        # it again", and "Chase on Only if they raise it again" is not a sentence.
        words = iso.strip().rstrip(".")
        return f"Chase {words[:1].lower()}{words[1:]}."
    late = (date.today() - day).days
    when = day.strftime("%-d %b")
    if late > 0:
        return f"Chase was due {when}, {late} day{'s' if late > 1 else ''} ago."
    if late == 0:
        return "Chase today."
    # The weekday is what the owner plans around, and the date is what stops it being
    # read as this week. Both, because this line has the room the chip does not.
    return f"Chase on {day.strftime('%a')} {when}."


def day_words(iso: str) -> str:
    """A date as the owner would say it: today, yesterday, or "Fri 21 Aug"."""
    if not iso:
        return ""
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return iso
    back = (date.today() - day).days
    if back == 0:
        return "today"
    if back == 1:
        return "yesterday"
    return day.strftime("%a %-d %b")


ROOT = settings.REPO
STATIC = settings.STATIC

# Everything the page is drawn from. Not the rendered files in `output/`, which
# are written fresh on every load and would change the answer every time.
# `calendar.json` only changes when a read found the meetings different, so an
# hourly look that found nothing new leaves the page where it is.
SOURCES = ("board.json", "asks.json", "calendar.json")


def stamp(conversation: bool = True) -> str:
    """What this page was rendered from, in one short token.

    The page carries it in `data-stamp` and asks the server for it every couple of
    seconds, so `./core/tick.py 26` in a terminal reloads the browser without the
    owner going to look for it. Size and modification time rather than a hash of the
    contents, because it is checked often and never has to be right about *what*
    changed, only that something did. `board.save()` renames the file into place,
    so there is no moment where this reads half a write.
    """
    marks = []
    # Without the conversation, it is the board alone. An answer arriving moves
    # the whole stamp, and the page needs to know whether that was all that
    # moved: if so it swaps one card's thread in place, and only a board that
    # moved under it is worth a reload.
    names = SOURCES if conversation else tuple(n for n in SOURCES if "asks" not in n)
    paths = [settings.STATE_DIR / n for n in names] + sorted(STATIC.glob("*"))
    for path in paths:
        try:
            info = path.stat()
            marks.append(f"{info.st_mtime_ns}.{info.st_size}")
        except OSError:
            marks.append("-")
    return hashlib.blake2s("-".join(marks).encode(), digest_size=6).hexdigest()


PREFS = settings.STATE_DIR / "prefs.json"


def prefs() -> dict:
    """How the owner likes the page, from the server rather than the browser.

    The theme and the folds used to live in localStorage only, which belongs to
    one origin in one browser profile. Opened on 127.0.0.1 rather than
    localhost, on the preview port, or in another Chrome profile, the page came
    up plain with every fold at its default, and that read as the app having
    gone backwards. `state/prefs.json` is the same for every one of them, and
    localStorage is only the cache for a page opened from disk.

    Deliberately not in SOURCES: the page that writes a pref is already showing
    it, and a reload every time they shut a fold would be absurd.
    """
    try:
        data = json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def asset(name: str) -> str:
    """One stylesheet or script, from a file rather than a Python string literal.

    A third of the renderers used to be CSS and JavaScript held in triple-quoted
    strings. Nothing could see it: no syntax check, no linter, no formatter, and
    the desk script shared its string with `%` formatting, so every modulo in it
    was written `%%`. The polling bug that left a card spinning for six minutes
    with its answer already on disk was a promise chain with no `.catch()`, which
    any linter would have flagged the moment it was typed, and none was looking.

    Read on every render rather than cached, because the render costs 15ms and an
    edit to the CSS should show up on reload without restarting the server.
    """
    return (STATIC / name).read_text(encoding="utf-8")


DATE_IN = re.compile(r"\d{4}-\d{2}-\d{2}")


def when_tag(item: dict, st: dict, sess: dict | None = None) -> tuple[str, str]:
    """When this one is due to move, in two or three words.

    The colour of an item already says whose it is. This says when, which is the
    other half of deciding what to open next, and it is the half that used to
    live buried in a hold note or a chase date three lines down.
    """
    if st["closed"]:
        return "", ""
    hold = item.get("hold") or {}
    waits = item.get("waits_on") or {}

    def day_in(*values: str) -> str:
        for value in values:
            found = DATE_IN.search(value or "")
            if found:
                return short_when(found.group(0))
        return ""

    def days_late(*values: str) -> int | None:
        """Days past the first date in these strings. 0 is today, negative is ahead.

        None means no date was written, which is a different thing from a date
        that has passed and has to read differently on the page.
        """
        for value in values:
            found = DATE_IN.search(value or "")
            if not found:
                continue
            try:
                return (date.today() - date.fromisoformat(found.group(0))).days
            except ValueError:
                return None
        return None

    def owed(word: str, *values: str) -> tuple[str, str] | None:
        """The chip for a date that was set so it would come back round.

        A date in the past is the whole point of writing one down, so it has to
        read as work. Left to `short_when` it comes out "28 Aug" in the same grey
        as a date next week, sorts as though nothing is due, and four days overdue
        looks like a plan the owner already has. The sweep cannot be relied on to
        roll these forward either, and rolling them forward silently would hide
        that they never chased, so the page ages them instead.
        """
        late = days_late(*values)
        if late is None:
            return None
        if late > 0:
            return f"{word}, {late}d late", "now"
        if late == 0:
            return f"{word} today", "now"
        return f"{word} {day_in(*values)}", "next"

    if st["state"] == "todo":
        if item.get("at_standup") and sess:
            return f'Say it {short_when(sess.get("date", ""))}'.strip(), "next"
        return "Do now", "now"
    if st["state"] == "hold":
        # `until` is often prose rather than a date, so it only ever supplies the
        # fallback wording, never the clock.
        due = owed("Held to", hold.get("revisit", ""))
        if due:
            return due
        day = day_in(hold.get("until", ""))
        return (f"Held to {day}" if day else "Held"), "next"
    if st["state"] == "waiting":
        due = owed("Chase", waits.get("chase_on", ""))
        if due:
            return due
        if item.get("at_standup") and sess:
            return f'Ask {short_when(sess.get("date", ""))}'.strip(), "next"
        return "No chase date", "none"
    return "", ""


ROLE_HINT = {
    "now": "what you do",
    "log": "what happened",
    "say": "what you say",
    "ref": "background",
    "warn": "careful",
}


def section(
    title: str,
    body: str,
    role: str = "ref",
    count: str = "",
    fold: bool = False,
    hint: str = "",
    open_: bool = False,
    remember: str = "",
) -> str:
    """One section of a ticket card, the same shape wherever it is used.

    Every section used to open with the same small grey capitals, which made a
    card one undifferentiated column. The marker colour and the weight now say
    what kind of section it is before the words do.

    `count` is words, never a bare number: "3 open" is a fact, "3" is a riddle.
    A folded section carries a Show or Hide word on the right, because a small
    triangle is not enough to tell you there is anything behind it.

    `open_` is for a section worth reading but worth getting out of the way once
    read, and `remember` keeps that choice across page loads.
    """
    n = f'<span class="n">{esc(count)}</span>' if count else ""
    # A hint is normally two or three words of aside, and small capitals suit
    # that. On a shut fold it is doing different work: it is the fact the owner
    # came for, so they do not have to open the fold at all. A sentence in capitals reads as
    # shouting, so a long hint on a fold is set as the sentence it is.
    plain_hint = fold and len(hint) > 28
    aside = (
        f'<span class="hint{" long" if plain_hint else ""}">{esc(hint)}</span>'
        if hint
        else ""
    )
    head = f"<h3>{esc(title)}{n}{aside}</h3>"
    if fold:
        keep = f' data-remember="{esc(remember)}"' if remember else ""
        return (
            f'<details class="sub {role}"{" open" if open_ else ""}{keep}>'
            f'<summary>{head}'
            f'<span class="fold-hint"></span></summary>{body}</details>'
        )
    return f'<section class="sub {role}">{head}{body}</section>'


def addressee(draft: dict) -> str:
    """Who a draft is addressed to, read off its `target`.

    The target is a sentence ("Slack reply to Alex Example in the Ask IE
    thread"), so this takes the words after "to" and stops at the place.
    """
    m = re.search(r"\bto ([A-Z].+?)(?: in | on | \u2014 | \(|,|$)", str(draft.get("target", "")))
    return m.group(1).strip()[:60] if m else ""


def replier(draft: dict) -> str:
    """Who answers this message once it has gone, or "" when nothing comes back.

    Usually the person it is addressed to. `reply_from` says otherwise when the
    writer knows better, and `expects_reply: false` says nobody does, which is
    what lets the Sent bar close a job rather than leave it waiting on a reply
    that is never coming.
    """
    if draft.get("expects_reply") is False:
        return ""
    return str(draft.get("reply_from") or "").strip()[:80] or addressee(draft)


def draft_to(item: dict) -> str:
    """Who the first unsent draft waits on, so "Sent, waiting on" comes filled.

    A guess the owner can overwrite in the box, never written to the board.
    """
    live = [d for d in B.drafts(item) if B.unsent(d)]
    return replier(live[0]) if live else ""


def after_posting(item: dict, k: int) -> tuple[str, str]:
    """What pressing Sent on draft `k` (1-based) does to the job, as (label, who).

    The label is the half sentence on the button, so the owner knows before pressing
    whether it closes the job, hands it to someone or leaves another message to
    send. `who` is the holder the job will wait on, empty when it closes.
    """
    all_drafts = B.drafts(item)
    rest = [d for i, d in enumerate(all_drafts, 1) if i != k and B.unsent(d)]
    if rest:
        return f"{len(rest)} more to send", ""
    owed = []
    for d in all_drafts:
        name = replier(d)
        if name and name not in owed:
            owed.append(name)
    if owed:
        who = " and ".join(owed)
        return f"waiting on {people.said(who)}", who
    if any(d.get("expects_reply") is not False for d in all_drafts):
        # A reply is owed and nobody can be read off the target: the button
        # opens the picker instead of guessing.
        return "say who replies", ""
    return "closes the job", ""


def status_button(item: dict, st: dict, label: str = "", tone: str = "") -> str:
    """Where a job stands, as the thing you press to move it. Jira's lozenge.

    A job here has more than two states, and sending is not finishing, so a
    checkbox that can only say done was the wrong shape: it closed jobs that
    were owed a reply. This is the status itself, and pressing it lists the moves
    open from where the job stands, each one a ./tick.py.

    `label` and `tone` override what the job's state says, for a job queued
    behind another, which is "With you" on the board and not startable.
    """
    n = esc(item.get("id", ""))
    waits = item.get("waits_on") or {}
    tone = tone if tone in TONES else st["tone"]
    holders = people.find(waits.get("who", "")) if st["state"] == "waiting" else []
    attrs = "".join(
        f' data-{k}="{esc(v)}"'
        for k, v in (
            ("who", waits.get("who", "")),
            ("whoids", ",".join(p["id"] for p in holders)),
            ("chase", waits.get("chase_on", "")),
            ("to", draft_to(item)),
            ("toids", ",".join(p["id"] for p in people.find(draft_to(item)))),
        )
        if v
    )
    text = label or st["label"]
    return (
        f'<button type="button" class="stat {esc(tone)}" data-tick="{n}" '
        f'data-state="{esc(st["state"])}"{attrs} '
        f'data-title="{esc(item.get("title", ""))}" aria-haspopup="menu" '
        f'aria-label="Job {n}: {esc(text)}. Change status" '
        f'title="{esc(text)}. Press to change it (x)">'
        f"{avatars(holders)}"
        f'<span class="stat-t">{esc(text)}</span>'
        f'<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 4l2.5 2.5L7.5 4"/></svg>'
        f"</button>"
    )


def avatar(p: dict, size: str = "") -> str:
    """One person as a coloured circle with their initials, the way Jira and
    Slack show who has something, so a holder is recognised before it is read."""
    team = " team" if p.get("team") else ""
    return (
        f'<span class="av{team}{" " + size if size else ""}" '
        f'style="--h:{people.hue(p["id"])}" aria-hidden="true">'
        f"{esc(people.initials(p))}</span>"
    )


def avatars(holders: list[dict]) -> str:
    """The holders of a job as overlapping circles, two at most and a count."""
    if not holders:
        return ""
    more = f'<span class="av more">+{len(holders) - 2}</span>' if len(holders) > 2 else ""
    return f'<span class="avs">{"".join(avatar(p) for p in holders[:2])}{more}</span>'


def people_json(board: dict) -> str:
    """The directory as the status menu's picker reads it, embedded in the page."""
    blob = script_json(
        {"people": [people.public(p) for p in people.DIRECTORY], "near": people.near(board)}
    )
    return f'<script type="application/json" id="desk-people">{blob}</script>'


def pill(label: str, tone: str) -> str:
    fg, bg, border = TONES.get(tone, TONES["grey"])
    return (
        f'<span class="pill" style="color:{fg};background:{bg};'
        f'border-color:{border}">{esc(label)}</span>'
    )


def link_btn(url: str, label: str) -> str:
    if not url:
        return ""
    return f'<a class="btn" href="{esc_url(url)}" target="_blank" rel="noopener">{esc(label)}</a>'


def render_script(blocks: list[dict]) -> str:
    if not blocks:
        return '<p class="empty">Nothing to say on this one today.</p>'
    out = []
    for block in blocks:
        lines = []
        for line in block.get("lines", []):
            lines.append(
                f"""
          <div class="jp-line">
            <p class="jp">{furi(line.get("ja_ruby", ""))}</p>
            <p class="en">{esc(line.get("en"))}</p>
          </div>"""
            )
        raw = "\n".join(plain(l.get("ja_ruby", "")) for l in block.get("lines", []))
        out.append(
            f"""
        <div class="jp-block">
          <div class="jp-heading">
            <span class="jp-h-ja">{furi(block.get("heading", ""))}</span>
            <span class="jp-h-en">{esc(block.get("heading_en"))}</span>
            <button class="copy" data-copy="{esc(raw)}">copy</button>
          </div>
          {"".join(lines)}
        </div>"""
        )
    return "".join(out)


def render_questions(questions: list[dict]) -> str:
    if not questions:
        return ""
    out = []
    for q in questions:
        client = settings.client_short()
        who = q.get("who", client)
        out.append(
            f"""
        <li class="q">
          <div class="q-en">{esc(q.get("en"))} {pill(who, "amber" if who == client else "grey")}</div>
          <div class="q-ja">{furi(q.get("ja_ruby", ""))}</div>
        </li>"""
        )
    return f"""
      <section class="sub">
        <h3>Questions I need answered</h3>
        <ul class="qlist">{"".join(out)}</ul>
      </section>"""


def render_drafts(drafts: list[dict]) -> str:
    if not drafts:
        return ""
    out = []
    for d in drafts:
        body = d.get("body_ruby", "")
        is_ja = d.get("language") == "ja"
        rendered = furi(body) if is_ja else esc(body)
        trans = (
            f'<p class="draft-en">{esc(d.get("body_en"))}</p>'
            if is_ja and d.get("body_en")
            else ""
        )
        out.append(
            f"""
        <div class="draft">
          <div class="draft-head">
            <span>{esc(d.get("target"))}</span>
            {link_btn(d.get("link", ""), "Go to thread")}
            <button class="copy" data-copy="{esc(plain(body))}">copy</button>
          </div>
          <div class="draft-body {"ja" if is_ja else ""}">{rendered}</div>
          {trans}
        </div>"""
        )
    return f"""
      <details class="sub drafts">
        <summary><h3>Draft replies ({len(drafts)})</h3></summary>
        {"".join(out)}
      </details>"""


CONSEQUENCE_ROWS = [
    ("fix_covers", "The fix covers"),
    ("falls_outside", "It does not cover"),
    ("accumulates", "So this piles up"),
    ("who_owns_it", "Owned by"),
    ("done_means", "Cleanup means"),
    ("still_open", "Still undecided"),
]


# Questions asked from the cards, by what they were asked about. A module-level
# store, because the alternative is threading a second file through every render
# function between here and an item, to add one block at the bottom of a card.
# Written once, at load, and read-only after that.
ASKED: dict[str, list[dict]] = {}


def load_asks(path: Path) -> None:
    """Read the questions file, and shrug if it is not there yet."""
    ASKED.clear()
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("asks", [])
    except (OSError, ValueError):
        return
    for row in rows:
        if row.get("ref"):
            ASKED.setdefault(row["ref"], []).append(row)


# The mark on every ask button. Four-pointed sparkles are what this means now,
# so it is recognised before the words are read, and the words then say what it
# does to this card rather than which model is behind it.
SPARK = (
    '<svg class="spark" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M12 2.6l1.9 5.1a3 3 0 0 0 1.8 1.8l5.1 1.9-5.1 1.9a3 3 0 0 0-1.8 '
    '1.8L12 20.2l-1.9-5.1a3 3 0 0 0-1.8-1.8L3.2 11.4l5.1-1.9a3 3 0 0 0 1.8-1.8z"/>'
    '<path d="M18.6 2.2l.7 1.9.9.3-.9.4-.7 1.9-.7-1.9-.9-.4.9-.3z"/></svg>'
)

# The arrow on both send buttons, the box at the foot of a card and the reply
# line under an answer, so the two read as the same action.
SEND = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h12M12 6l6 6-6 6"/></svg>'

# A move an answer may propose but never make. The agent writes one of these on
# its last line when the conversation plainly ends in the work moving, and the
# page turns it into a button: the owner presses it or does not. Reading a
# thread and concluding a job is finished has never been the same thing as the
# owner saying so, and this is that distinction made into a mechanism rather than a rule in a
# prompt. `/api/act` is the other half, and it accepts only these four shapes.
ACTION = re.compile(
    r"^ACTION:\s*tick\s+(\d+)\s+(done|mine|dropped|waiting)\s*(?:\|\s*(.{1,90}?))?\s*$",
    re.M,
)

ACTION_WORDS = {
    "done": ("Mark {n} done", "finished, nothing comes back"),
    "mine": ("Back to you", "they replied, {n} is yours again"),
    "dropped": ("Drop {n}", "not happening"),
    "waiting": ("{n} is with {who}", "sent, waiting on them"),
}


def proposed(raw: str) -> tuple[str, list[dict]]:
    """An answer split from the moves it suggests at the end of it.

    The line never reaches the card as text: either it becomes a button or it
    is dropped, because `ACTION: tick 30 waiting | Sam` printed as prose is
    worse than nothing.
    """
    moves = []
    for m in ACTION.finditer(raw or ""):
        n, verb, who = m.group(1), m.group(2), (m.group(3) or "").strip()
        if verb == "waiting" and not who:
            continue
        label, sub = ACTION_WORDS[verb]
        moves.append(
            {
                "n": n,
                "verb": verb,
                "who": who,
                "label": label.format(n=n, who=who),
                "sub": sub.format(n=n, who=who),
            }
        )
    return (ACTION.sub("", raw or "").strip(), moves)


def action_html(ident: str, moves: list[dict], done: str = "") -> str:
    """The moves an answer proposed, as buttons that have not happened yet.

    Said in words so there is no doubt which way round it is: it is offering,
    and nothing moves until the owner presses. Once they have, the row says what moved
    rather than offering it again.
    """
    if done:
        return f'<p class="qa-did">{esc(done)}</p>'
    if not moves:
        return ""
    btns = "".join(
        f'<button type="button" class="qa-act" data-act="{esc(ident)}"'
        f' data-act-n="{esc(m["n"])}" data-act-verb="{esc(m["verb"])}"'
        f' data-act-who="{esc(m["who"])}">{esc(m["label"])}'
        f'<span>{esc(m["sub"])}</span></button>'
        for m in moves
    )
    return f"""
                  <div class="qa-acts">
                    <span class="qa-acts-k">It suggests, nothing has moved yet</span>
                    {btns}
                  </div>"""


def qa_one(
    a: dict, open_: bool, child: bool = False, kids: str = "", foot: bool = True
) -> str:
    """One exchange: what the owner asked, and what came back under it.

    Shut, it is the question and when they asked it, which is how they find the
    one they half remember. Open, it is the answer, whatever they asked next about
    it, and the box to ask the next thing.

    A question still being answered is not shut and not foldable: it is the
    bubble with the thinking line under it, which is where the owner is looking.
    """
    state = a.get("state", "answered")
    running = state in ("running", "queued")
    failed = state == "failed"
    raw = a.get("answer") or ""
    body, moves = proposed(raw) if state == "answered" else (raw, [])
    ident = esc(a.get("id", ""))
    if running:
        # No <details> while it is working, so nothing can fold the answer away
        # as it arrives, and the live line is part of the bubble rather than a
        # strip at the bottom of the card.
        return f"""
              <li class="qa running" data-qa="{ident}" data-live="1">
                <div class="qa-me"><span class="qa-k">You asked</span>
                  <span class="qa-q">{esc(a.get("question"))}</span>
                  <span class="qa-when">{esc(a.get("asked_at", ""))}</span></div>
                <div class="qa-wait">
                  <span class="ask-dots" aria-hidden="true"><i></i><i></i><i></i></span>
                  <span class="ask-wait-t">{"Queued, behind the one in front"
                      if state == "queued" else "Thinking"}</span>
                  {f'<span class="qa-model">{esc(agent.model_label(a.get("model")))}</span>'
                   if a.get("model") else ""}
                  <button type="button" class="qa-stop" data-stop="{ident}">Stop</button>
                  <span class="ask-wait-tail" hidden></span>
                </div>
              </li>"""
    if state == "cancelled":
        # Called off, so there is nothing to read. One quiet line that says so,
        # rather than a bubble with "You called this one off" folded inside it.
        return f"""
              <li class="qa cancelled" data-qa="{ident}">
                <div class="qa-me"><span class="qa-k">Stopped</span>
                  <span class="qa-q">{esc(a.get("question"))}</span>
                  <span class="qa-when">{esc(a.get("asked_at", ""))}</span></div>
                <button type="button" class="qa-del" data-forget="{ident}"
                        title="Forget this question" aria-label="Forget this question">&times;</button>
              </li>"""
    if failed:
        # The failure belongs on the bubble that failed, with the question still
        # in it and one button that sends it again. It used to be a red line at
        # the top of the card, which is where the owner was not looking.
        return f"""
              <li class="qa failed" data-qa="{ident}" data-parent="{esc(a.get("parent", ""))}">
                <div class="qa-me"><span class="qa-k">You asked</span>
                  <span class="qa-q">{esc(a.get("question"))}</span>
                  <span class="qa-when">{esc(a.get("asked_at", ""))}</span></div>
                <div class="qa-bad">
                  <b>That did not come back</b>{esc(body or "No answer came back.")}
                  <button type="button" class="qa-retry" data-retry="{ident}">Try again</button>
                </div>
                <button type="button" class="qa-del" data-forget="{ident}"
                        title="Forget this question" aria-label="Forget this question">&times;</button>
              </li>"""
    return f"""
              <li class="qa {esc(state)}{" kid" if child else ""}" data-qa="{ident}"
                  data-at="{esc(a.get("asked_at", ""))}">
                <details {"open" if open_ else ""}>
                  <summary>
                    <span class="qa-k">{"Then" if child else "You asked"}</span>
                    <span class="qa-q">{esc(a.get("question"))}</span>
                    <span class="qa-when">{esc(a.get("asked_at", ""))}</span>
                    <span class="fold-hint"></span>
                  </summary>
                  <div class="qa-a">{answered(body)}</div>
                  {qa_by(a)}
                  {action_html(ident, moves, str(a.get("acted_on") or ""))}
                  {kids}
                  {qa_foot(ident) if foot else ""}
                </details>
                <button type="button" class="qa-del" data-forget="{ident}"
                        title="Forget this question" aria-label="Forget this question">&times;</button>
              </li>"""


def qa_by(a: dict) -> str:
    """Which model wrote the answer and how long it took, under the answer.

    The owner asked to be told, and it is the first thing to look at when an answer is
    thin: a Haiku answer to a drafting question is the model, not the desk.
    """
    label = agent.model_label(a.get("model"))
    took = a.get("took_secs")
    bits = [b for b in (label, f"{took}s" if took else "") if b]
    if not bits:
        return ""
    return f'<div class="qa-by">{SPARK}{" &middot; ".join(esc(b) for b in bits)}</div>'


def model_picker() -> str:
    """The model a question goes to, chosen in the composer, Claude Desktop's way.

    The choice is a pref, so every composer on the page and every browser shows
    the same one; desk.js keeps them in step and sends it with the question. The
    default is whatever config.json gives the ask job.
    """
    default = agent.model_for("ask")
    chosen = str(prefs().get("desk-ask-model") or default)
    models = agent.ask_models()
    ids = [m for m, _, _ in models]
    if chosen not in ids:
        chosen = default if default in ids else ids[0]
    opts = "".join(
        f'<option value="{esc(m)}"{" selected" if m == chosen else ""}'
        f' title="{esc(why)}">{esc(name)}{" (default)" if m == default else ""}</option>'
        for m, name, why in models
    )
    return (
        f'<label class="ask-model" title="Which model answers">{SPARK}'
        f'<select aria-label="Model">{opts}</select></label>'
    )


def qa_foot(ident: str) -> str:
    """The reply line, inside the answer it replies to.

    Replying to the answer is the common case and it used to take two clicks
    through a button that said "Ask about this answer". Now the line is already
    there: one press on it is the cursor in it, and the exchange above travels
    with whatever the owner types, so "why?" is a whole question.
    """
    return f"""
                  <div class="fu" data-parent="{ident}">
                    <textarea rows="1" aria-label="Reply to this answer"
                      placeholder="Reply&hellip; why, or what to change"></textarea>
                    <button type="button" class="ask-send" data-ask-send hidden
                            aria-label="Send reply" title="Send (Enter)">{SEND}</button>
                    <div class="ask-go">
                      <span class="ask-keys">Enter sends &middot; Shift+Enter for a new line &middot; Esc lets go</span>
                      <button type="button" class="ask-copy">Copy for a chat</button>
                      <span class="ask-hint"></span>
                    </div>
                  </div>"""


def chat_one(a: dict) -> str:
    """One exchange in the side panel: the owner's words on the right, the answer under.

    Nothing folds here, because the panel is where the owner reads a conversation
    rather than where they keep one. Running, failed and stopped questions look
    the same as on a card, so the live line and Try again work unchanged.
    """
    if a.get("state", "answered") != "answered":
        return qa_one(a, True, foot=False)
    body, moves = proposed(a.get("answer") or "")
    ident = esc(a.get("id", ""))
    return f"""
              <li class="qa answered chat-qa" data-qa="{ident}"
                  data-at="{esc(a.get("asked_at", ""))}">
                <div class="qa-me"><span class="qa-q">{esc(a.get("question"))}</span>
                  <span class="qa-when">{esc(a.get("asked_at", ""))}</span></div>
                <div class="chat-a">
                  <div class="qa-a">{answered(body)}</div>
                  {qa_by(a)}
                  {action_html(ident, moves, str(a.get("acted_on") or ""))}
                </div>
                <button type="button" class="qa-del" data-forget="{ident}"
                        title="Forget this question" aria-label="Forget this question">&times;</button>
              </li>"""


def chat_thread(asks: list[dict]) -> str:
    """The same conversation for the side panel: every exchange, oldest first.

    On a card the record is folded so the card stays a card. In the panel the
    conversation is the point, so nothing folds and nothing nests: it reads top to
    bottom like every chat the owner uses, and the composer under it carries on from the
    newest answer.
    """
    if not asks:
        return (
            '<p class="chat-empty">Nothing asked here yet. Ask anything about the '
            "work, or tell it what to change. The answer stays here, and on the "
            "card.</p>"
        )
    rows = sorted(enumerate(asks), key=lambda p: (str(p[1].get("asked_at", "")), p[0]))
    return '<ul class="qa-list">' + "".join(chat_one(a) for _, a in rows) + "</ul>"


def qa_thread(asks: list[dict]) -> str:
    """Everything asked about one card, newest last, follow-ups under their own.

    Only the newest exchange is open. The rest are one line each, and once there
    are more than three the older ones go behind a single line of their own,
    because the record of what the owner asked in June should not be what they
    scroll through to reach today's answer.
    """
    if not asks:
        return ""
    kids: dict[str, list[dict]] = {}
    roots = []
    known = {a.get("id") for a in asks}
    for a in asks:
        parent = a.get("parent")
        if parent and parent in known:
            kids.setdefault(parent, []).append(a)
        else:
            roots.append(a)

    def under(ident: str) -> list[dict]:
        """Everything asked off the back of one question, in the order asked.

        Following up on a follow-up is the normal shape of digging into
        something, and a grandchild hung off a bubble it was never drawn under
        would simply not appear on the page.
        """
        out = []
        for k in kids.get(ident, []):
            out.append(k)
            out += under(k.get("id", ""))
        return out

    def bubble(a: dict, newest: bool) -> str:
        mine = under(a.get("id", ""))
        rows = "".join(
            qa_one(k, newest and i == len(mine) - 1, child=True)
            for i, k in enumerate(mine)
        )
        # The newest thread stands open, and inside it only the newest exchange:
        # what the owner asked last is what they came back for. One way in per
        # thread, at the foot of that last exchange, because the chain travels
        # with it either way and two identical buttons a line apart is a choice
        # they should not have to make.
        return qa_one(
            a,
            newest,
            kids=f'<ul class="qa-kids">{rows}</ul>' if mine else "",
            foot=not mine,
        )

    # The newest stands open only on the day it was asked. A week-old answer
    # held open was the tallest thing on the card, 2,000px on 7 Oct.
    today = date.today().isoformat()
    live = [
        bubble(a, i == len(roots) - 1 and str(a.get("asked_at", ""))[:10] == today)
        for i, a in enumerate(roots)
    ]
    if len(live) > 3:
        older, recent = live[:-2], live[-2:]
        n = len(older)
        head = (
            f"""
            <details class="qa-earlier">
              <summary>{n} earlier question{"" if n == 1 else "s"} on this card
                <span class="fold-hint"></span></summary>
              <ul class="qa-list">{"".join(older)}</ul>
            </details>"""
        )
        return head + f'<ul class="qa-list">{"".join(recent)}</ul>'
    return f'<ul class="qa-list">{"".join(live)}</ul>'


def ask_block(
    ref: str,
    label: str,
    asks: list[dict],
    lead: str = "",
    has_draft: bool = False,
    kind: str = "work",
) -> str:
    """The chat for one job, on the job.

    Same box does both halves of the same conversation: "what do you mean by
    this term" and "rewrite this with the latest from the refinement thread, and
    tell Sato-san we are still checking". Asked in a fresh chat, either one
    starts with the owner explaining which draft they mean, and that explaining is why
    the question does not get asked.

    The answers stay on the card. A chat window is gone by tomorrow, and the
    reason a draft says what it says is worth having next to the draft.

    `kind` is which view is asking. The box is the same box and the record is the
    same record, so a question asked in front of the script is on the card on the
    work view too. Only the example changes, because in front of the script what
    the owner wants changed is the words.

    There used to be a row of starter chips under the box. The owner did not use
    them and said so, and five canned sentences under every composer were the most
    repeated thing on the page. The placeholder carries the one example instead.

    `.ask-thread` is the part `/api/thread` redraws when an answer lands, so the
    page swaps one card's conversation in place rather than reloading and
    putting the owner somewhere else on the board.
    """
    say = kind == "say"
    eg = (
        "Rewrite the second line more plainly\u2026"
        if say
        else "Ask, or tell it what to change\u2026"
        if not has_draft
        else "Rewrite this with the latest from the thread\u2026"
    )
    n = len(asks)
    return f"""
            <div class="ask" data-ask="{esc(ref)}" data-ask-lead="{esc(lead)}">
              <div class="ask-thread">{qa_thread(asks)}</div>
              <div class="ask-bar">
                <button type="button" class="ask-open">{SPARK}Ask or change on
                  <span class="ask-of">{esc(label)}</span></button>
                {f'<span class="ask-n">{n} asked</span>' if asks else ""}
              </div>
              <div class="ask-box" hidden>
                <div class="ask-field">
                  {SPARK}
                  <textarea rows="1" aria-label="Ask or change on {esc(label)}"
                    placeholder="{esc(eg)}"></textarea>
                  <button type="button" class="ask-send" data-ask-send hidden
                          aria-label="Send" title="Send (Enter)">
                    {SEND}</button>
                </div>
                <div class="ask-go">
                  {model_picker()}
                  <span class="ask-keys">Enter sends &middot; Shift+Enter for a new line &middot; Esc closes</span>
                  <button type="button" class="ask-chat" data-chat-open="{esc(ref)}" hidden
                          title="Carry on in the side panel (a)">Open in side panel</button>
                  <button type="button" class="ask-copy">Copy for a chat</button>
                  <button type="button" class="ask-cancel">Close</button>
                  <span class="ask-hint"></span>
                </div>
              </div>
            </div>"""


def thread_html(ref: str, chat: bool = False) -> str:
    """One card's conversation, for the page to swap in without a reload."""
    return (chat_thread if chat else qa_thread)(ASKED.get(ref, []))


def render_consequences(c: dict, ref: str = "") -> str:
    """The scope of a fix, and what happens to everything outside it.

    This is where the client's questions come from, so it renders even when half
    the fields are blank. Folded, because it is what the owner reaches for when
    the client pushes, not what they read before speaking, and six rows of it on
    every card was two thousand pixels between them and the next thing they say.
    """
    if not isinstance(c, dict):
        return ""
    rows = [(label, c.get(key)) for key, label in CONSEQUENCE_ROWS if c.get(key)]
    if not rows:
        return ""
    items = "".join(
        f'<div class="cons-row"><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>'
        for label, value in rows
    )
    return section(
        "Scope, and what falls outside it",
        f'<dl class="cons-list">{items}</dl>',
        role="cons",
        count=f"{len(rows)} rows",
        hint="for when they push on scope",
        fold=True,
        remember=f"cons-{ref}",
    )


def sub_line(st: dict, item: dict | None = None) -> str:
    """The one line under a title saying what has already happened to it.

    A held item says what it is held for right here. That used to be a separate
    amber box above the list, which meant reading two lists to find out that one
    of the four things in front of the owner was not theirs to send yet.
    """
    bits = []
    if st["state"] == "waiting":
        who = st.get("who", "them")
        since = st.get("at", "")
        bits.append(
            f"sent {since}, with {who}" if st.get("sent") else f"with {who} since {since}"
        )
    elif st["state"] == "hold" and item:
        hold = item.get("hold") or {}
        if hold.get("until"):
            bits.append(f"do not send yet, waiting for {hold['until']}")
    elif st["closed"]:
        bits.append(f"{st['label'].lower()} {st.get('at', '')}")
    if st.get("note"):
        bits.append(st["note"])
    return " &middot; ".join(esc(b) for b in bits)
