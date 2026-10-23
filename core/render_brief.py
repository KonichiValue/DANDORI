#!/usr/bin/env python3
"""The morning brief: the five things worth knowing before the day starts.

What a secretary would hand the owner before the morning starts. Not a summary
of the board, which is what the rest of the page already is: only the part that
is true *this morning* and would cost them something to miss.

Five lines, hard cap. The cap is the feature. Everything on this board is already
on a card, so a sixth line is not a busier day, it is one of the five not having
earned its place. `board.py` documents what each `kind` means and `prompts/refresh.md`
tells the sweep how to choose them.

It sits at the top of the work view rather than behind a tab of its own. A
briefing you have to click is a briefing read on the days you remember to, and
this one is five lines: it costs less room than the fold it would need.
"""

from __future__ import annotations

from datetime import date, datetime

import settings
from render import day_words, esc, link_btn, script_meta, sessions, short_when

# The order they read in, whatever order the sweep wrote them. A room today comes
# before anything else because it is the only line with a time on it the owner
# cannot move; the one thing to start on comes before the things they only
# have to watch.
ORDER = {"alert": -1, "room": 0, "big": 1, "moved": 2, "do": 3, "watch": 4}

# What each kind is called on the page. Said the way the owner would say it, not named
# after the field: "Moved" and "Room" were jargon, and a label you have to
# decode is worse than no label. These are answers to "what is this line?".
WORD = {
    "alert": "Needs you now",
    "room": "In the room",
    "big": "Big news",
    "moved": "Changed",
    "do": "Start here",
    "watch": "Keep an eye",
}

CAP = 5
MINE = ' class="mine"'


def freshness(built: str, for_date: str) -> tuple[str, str]:
    """How old this brief is, and whether it is still worth drawing.

    A briefing written yesterday and read as this morning's is worse than none at
    all, so a brief for a day that has passed is not drawn. Within today it says
    its own age, because "written at 08:15" and "written four hours ago" are
    different amounts of trust.
    """
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


def room_words(board: dict, live: dict) -> str:
    """The next room, whether the prep is for it, and the room after it.

    This used to be a fold of its own under the brief, saying the same room a
    second time. The two things only it said were whether the prep was written
    for this room and what the room after it needs, so they live on this line.
    """
    when = short_when(live["date"])
    room = live.get("tab") or live.get("name") or "Session"
    focus = (live.get("focus") or "").strip()
    words = f"{room} {when}." + (f" {focus}" if focus else "")
    meta = script_meta(board)
    has_script = any((t.get("prep") or {}).get("script") for t in board.get("tickets", []))
    if not has_script:
        words += " No prep written yet."
    elif meta.get("for_date") and meta["for_date"] != live["date"]:
        words += f" The prep is for {day_words(meta['for_date'])}, so it needs updating."
    after = [
        s for s in sessions(board)
        if not s.get("skipped") and s.get("date")
        and (s.get("date"), s.get("at")) != (live.get("date"), live.get("at"))
    ][:1]
    for s in after:
        name = s.get("tab") or s.get("name") or "Session"
        then = f" Then {name} {short_when(s['date'], s.get('at', ''))}"
        bring = s.get("bring") or []
        extra = (s.get("focus") or "").strip()
        if bring:
            extra = (extra + " Bring: " + "; ".join(bring) + ".").strip()
        words += then + (f": {extra}" if extra else ".")
    return words


def from_board(board: dict) -> list[dict]:
    """The lines the page can write for itself, so the sweep need not repeat them.

    The next room and the newest client news were their own blocks under this one,
    which meant the five lines sat above two more panels saying the same kind of
    thing. They belong *in* the five. Both are read straight off the board rather
    than written by an agent, so neither can go stale the way a sentence can.

    A line the sweep wrote itself always wins: if it already has a `room` line it
    knows something about today that this cannot, and this one is dropped.
    """
    out = []
    alert = board.get("alert") or {}
    if isinstance(alert, str):
        alert = {"what": alert}
    if alert.get("what"):
        out.append({"kind": "alert", "what": alert["what"],
                    "source_url": alert.get("source_url", ""), "from_board": True})
    live = next(
        (s for s in sessions(board) if not s.get("skipped")),
        {},
    )
    if live.get("date"):
        out.append(
            {
                "kind": "room",
                "at": live.get("at", ""),
                "what": room_words(board, live),
                "plan": live.get("timetable") or [],
                "from_board": True,
            }
        )
    news = board.get("news") or board.get("watch") or []
    if news:
        latest = max(news, key=lambda r: r.get("on") or "")
        out.append(
            {
                "kind": "big",
                "what": f'{latest.get("topic", "")}. {latest.get("why", "")}'.strip(),
                "source_url": latest.get("source_url", ""),
                "from_board": True,
            }
        )
    return out


def chosen(board: dict) -> list[dict]:
    """The lines worth drawing, in the order they read, capped at five.

    Empty when the brief is missing or was written for a day that has passed: a
    briefing read as this morning's when it is yesterday's is worse than none.
    """
    brief = board.get("brief") or {}
    _, state = freshness(brief.get("built_at", ""), brief.get("for_date", ""))
    # Yesterday's written lines go, but what is read straight off the board
    # (the room, the news, an alert) is true whenever the page is drawn.
    written = [] if state == "stale" else [
        row for row in (brief.get("lines") or []) if row.get("what")
    ]
    kinds = {row.get("kind") for row in written}
    lines = written + [r for r in from_board(board) if r["kind"] not in kinds]
    return sorted(lines, key=lambda r: ORDER.get(r.get("kind", "watch"), 9))[:CAP]


def gist(row: dict) -> str:
    """The first sentence of a line, for the summary when the card is shut.

    Cut on the sentence, so what shows is a whole thought rather than a clipped
    one. A first sentence long enough to wrap twice is trimmed on a word.
    """
    text = (row.get("what") or "").strip()
    for stop in (". ", "。"):
        if stop in text:
            text = text.split(stop)[0] + ("." if stop == ". " else "。")
            break
    if len(text) > 92:
        text = text[:91].rsplit(" ", 1)[0] + "…"
    return text


def render(board: dict, refs: dict[str, str]) -> str:
    """The brief as its own card, dark, at the top of the work view.

    Dark on purpose, and it is the only block on the page drawn that way. It is
    the first thing meant to be read and a white card among white cards does not
    say so. It folds, and shut it still carries its leading line, because the
    point of folding it is to get it out of the way once read, not to hide it.
    """
    lines = chosen(board)
    if not lines:
        return ""
    brief = board.get("brief") or {}
    age, state = freshness(brief.get("built_at", ""), brief.get("for_date", ""))
    if state == "stale":
        age = ""

    out = []
    for n, row in enumerate(lines, 1):
        kind = row.get("kind", "watch")
        # The item numbers this line is about, as links, because the next thing the owner
        # does after reading "start on 43" is open 43.
        jobs = "".join(
            f'<a class="br-job" href="#{esc(refs.get(row.get("ref", ""), ""))}">'
            f"{esc(num)}</a>"
            for num in (row.get("items") or [])
        )
        # The ticket it lands on, when the line names one. On a `big` line this is
        # the whole point: "top priority across the client" matters because of
        # where it reaches the owner.
        tag = (
            f'<a class="br-ref" href="#{esc(refs[row["ref"]])}">{esc(row["ref"])}</a>'
            if row.get("ref") and row["ref"] in refs
            else ""
        )
        out.append(f"""
      <li class="br-l {esc(kind)}">
        <span class="br-n">{n}</span>
        <span class="br-k">{esc(WORD.get(kind, kind))}</span>
        <span class="br-w">{esc(row.get("what"))}
          {f'<span class="br-jobs">{jobs}</span>' if jobs else ""}
          {tag}
          {link_btn(row.get("source_url", ""), "Source") if row.get("source_url") else ""}
        </span>
        {f'<span class="br-at">{esc(at_words(row.get("at")))}</span>' if row.get("at") else ""}
        {timetable(row.get("plan") or [])}
      </li>""")

    news = board.get("news") or board.get("watch") or []
    shown = {r.get("what", "") for r in lines}
    more = [
        r for r in sorted(news, key=lambda r: r.get("on") or "", reverse=True)
        if f'{r.get("topic", "")}. {r.get("why", "")}'.strip() not in shown
    ]
    if more:
        out.append(f"""
      <li class="br-l more">
        <details class="br-more">
          <summary>{len(more)} more {esc(settings.client_short())} update{"" if len(more) == 1 else "s"}, not your
          tickets but they reach them<span class="fold-hint"></span></summary>
          <ul>{"".join(news_li(r) for r in more)}</ul>
        </details>
      </li>""")

    lead = lines[0]
    rest = len(lines) - 1
    return f"""
    <details class="brief{" old" if state == "old" else ""}" id="brief"
             data-remember="brief" open>
      <summary>
        <span class="brief-k">This morning</span>
        <span class="brief-n">{len(lines)} to know</span>
        {f'<span class="brief-age">{esc(age)}</span>' if age else ""}
        <span class="fold-hint"></span>
        <span class="brief-sum">
          <b>{esc(WORD.get(lead.get("kind", ""), ""))}</b> {esc(gist(lead))}
          {f'<i>and {rest} more</i>' if rest else ""}</span>
      </summary>
      <ol class="br">{"".join(out)}</ol>
    </details>"""


def news_li(r: dict) -> str:
    """One news row: what happened, how it reaches the owner, and the rest folded."""
    said = (r.get("what") or "").strip()
    return f"""
            <li><b>{esc(r.get("topic"))}</b>
              <span class="br-why">{esc(r.get("why"))}</span>
              {f'<details class="br-rest"><summary>The rest of it</summary>{esc(said)}</details>' if said else ""}
              <span class="br-on">{esc(day_words(r.get("on", "")))}</span>
              {link_btn(r.get("source_url", ""), "Source") if r.get("source_url") else ""}
            </li>"""


def timetable(rows: list[dict]) -> str:
    """The running order of a day, when the session is a day rather than a slot.

    A standup is a time. An onsite is a building at 09:45, two standups at 11:00
    and the discussion the owner came for at 13:00, and getting that wrong is the
    one mistake the page can save them from.
    """
    out = [
        f'<li{MINE if r.get("mine") else ""}><b>{esc(r.get("at"))}</b>'
        f'{esc(r["what"])}</li>'
        for r in rows if r.get("what")
    ]
    return f'<ul class="br-plan">{"".join(out)}</ul>' if out else ""


def at_words(at: str) -> str:
    """A line's time as the owner would read it: "11:00", or "Mon 15:15" if not today.

    A sweep writes a full timestamp; a room is just a time. Both read the same.
    """
    try:
        when = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return at or ""
    if when.date() == date.today():
        return when.strftime("%H:%M")
    return when.strftime("%a %H:%M")
