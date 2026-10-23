#!/usr/bin/env python3
"""The week ahead: the rooms the owner speaks in, and everything that comes due.

Split out of `render_desk.py`, which had grown to 2300 lines and held eight
unrelated jobs. This one answers a single question, "is the week covered", and
it is the only file that knows how a day is drawn.

The first version of this was a strip of seven boxes with a job number in each,
which looked like a calendar and was useless as one: a 10.5px chip reading `13`
and a two-word client term tells you a number is due without telling you what
it is, whose it is or whether it is late. A calendar you cannot read at a glance is decoration.

So a day here is a column of real rows. Each row carries the job number, what
the job actually is, and who is holding it, coloured the way the rest of the
page colours work: red is the owner's, amber is stuck, blue is with someone
else. Today is wider than the rest, because today is the column they read.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import calendar_sync
import prioritize
import settings
from render import (
    day_words,
    esc,
    esc_url,
    item_state as state_of,
    next_live,
    urgency_of,
)

# An ISO date and nothing else. `chase_on` is sometimes a sentence ("Only if they
# raise it again"), and a sentence is not a day on a calendar.
ISO = re.compile(r"\d{4}-\d{2}-\d{2}")

# How many days to look ahead, and which of them get drawn.
#
# Monday to Friday of the week being looked at. A calendar should say "this
# week", because that is the unit the work is actually organised in: the cycle
# runs in weeks, the standup falls on set weekdays (`meeting.days`), and "is Wednesday covered"
# is a question about a week rather than about the next five days.
#
# A rolling window starting today was the first attempt and it was wrong. It
# meant the columns changed identity every morning, Thursday's strip began at
# Thursday with no way to see that Tuesday had been busy, and on a Friday it
# showed three days of next week with no heading to say so.
#
# Saturday and Sunday get no column. Nothing on this desk happens on them and no
# standup is ever scheduled on one, so they were two sevenths of the width
# carrying nothing. A date that does fall on one moves to the Friday before,
# flagged with its real date, because Friday is the last day the owner can act on it.
WEEK_DAYS = 5

# Rows a day draws before the rest become "and 2 more". The column scrolls, so
# this is about which rows are visible without asking, not which exist.
#
# Work sitting with *the owner* is never folded away, however many there are:
# the strip exists so they can see what they are doing, and "and 2 more" hiding
# two of their own jobs is the failure it was built to prevent. Only other people's
# chases count against the limit.
ROWS_PER_DAY = 4

# A meeting outside these hours is somebody else's working day: the US/EU
# all-hands at 23:00, a dinner. Kept, but folded with the meetings that are not
# about this work, so the column reads as the day the owner is actually at the desk.
DESK_HOURS = ("08:00", "18:00")


def meetings(days: list[date]) -> dict[str, list[dict]]:
    """The calendar, by day, for the columns being drawn."""
    shown = {d.isoformat() for d in days}
    out: dict[str, list[dict]] = {}
    for e in calendar_sync.load_raw().get("events") or []:
        if e.get("date") in shown:
            out.setdefault(e["date"], []).append(e)
    return out


def same_room(s: dict, events: list[dict]) -> dict | None:
    """The calendar event that is this board session, by its day and time."""
    at = s.get("at") or ""
    return next((e for e in events if at and e.get("at") == at and e.get("work")), None)


def meet_links(e: dict, past: bool) -> str:
    """Join while it can still be joined, Notes once there are some."""
    out = ""
    if e.get("join") and not past:
        out += (f'<a class="wm-join" href="{esc_url(e["join"])}" target="_blank" rel="noopener"'
                f' title="Join in Google Meet">Join</a>')
    for n in (e.get("notes") or [])[:1]:
        out += (f'<a class="wm-notes" href="{esc_url(n["url"])}" target="_blank" rel="noopener"'
                f' title="{esc(n["title"])}">Notes</a>')
    return out


def over(e: dict) -> bool:
    """The meeting has ended, so there is nothing left to join."""
    try:
        return datetime.fromisoformat(e["end"]) <= datetime.now(settings.tz())
    except (KeyError, ValueError):
        return False


def meet_row(e: dict, today: date) -> str:
    past = over(e)
    answer = e.get("answer", "")
    klass = " ".join(filter(None, [
        "wm", "work" if e.get("work") else "side", "client" if e.get("client") else "",
        "tent" if answer in {"tentative", "needsAction"} else "",
        "past" if past else "",
    ]))
    span = e["at"] + (f'&ndash;{esc(e["until"])}' if e.get("until") else "")
    said = {"tentative": " · maybe", "needsAction": " · not answered"}.get(answer, "")
    return f"""<div class="{klass}" data-start="{esc(e.get("start", ""))}" data-end="{esc(e.get("end", ""))}">
              <span class="wm-t">{esc(e["at"])}</span>
              <a class="wm-w" href="{esc_url(e.get("link", ""))}" target="_blank" rel="noopener"
                 title="{esc(e["title"])} {span}{esc(said)}">{esc(e["title"])}</a>
              <span class="wm-go">{meet_links(e, past)}</span></div>"""


def whose(item: dict, st: dict) -> tuple[str, str]:
    """Who is holding this, and which of the page's four colours that is.

    The words are the card's words. A job whose card pill reads "With you" and
    whose calendar row read "You" is the same fact told two ways, and the whole
    point of putting the owner's work on the strip is that they recognise it there.
    """
    if st["state"] == "todo":
        return "With you", "mine"
    if st["state"] == "hold":
        return st.get("who") or "Held", "held"
    who = st.get("who") or ""
    return f"With {who}" if who else "With them", "theirs"


def monday_of(day: date) -> date:
    """The Monday of the week `day` falls in.

    On a Saturday or Sunday this is the Monday coming, not the one just gone:
    by the weekend the week on the page is over, and what the owner wants to see
    is the one they are about to walk into.
    """
    if day.weekday() >= 5:
        return day + timedelta(days=7 - day.weekday())
    return day - timedelta(days=day.weekday())


def columns(monday: date) -> list[date]:
    """Monday to Friday of one week."""
    return [monday + timedelta(days=n) for n in range(WEEK_DAYS)]


def dated(item: dict, st: dict, today: date) -> str:
    """The day this job belongs on, or "" if it has no day at all.

    Two different fields, because the board dates two different things. Work
    sitting with someone else carries `waits_on.chase_on`, the day their silence
    becomes the owner's problem again. Work sitting with *the owner* carries no
    date at all, only an urgency, which is why the first version of this
    calendar showed five of other people's chases and none of the owner's own
    jobs: the column they were meant to work from was the one thing missing.

    So urgency is read as a day. `today` means today, `this-week` means the next
    working day the owner has not passed yet (today, on a Friday), and `monitor` is genuinely undated and
    stays off the strip. It is an inference rather than a fact, so the row says
    so and the card stays the place the real state lives.
    """
    chase = (item.get("waits_on") or {}).get("chase_on") or ""
    if ISO.fullmatch(chase or ""):
        return chase
    if st["state"] not in {"todo", "hold"}:
        return ""
    urgency = urgency_of(item)
    if urgency == "today":
        return today.isoformat()
    if urgency == "this-week":
        # Tomorrow, or Monday when tomorrow is the weekend: a job the owner has
        # not got to today is a job for the next day they are at the desk.
        d = today + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        # On a Friday that is next week, off the strip the owner is looking at, and a
        # job meant for this week is then a job for today.
        if d.isocalendar()[:2] != today.isocalendar()[:2]:
            return today.isoformat()
        return d.isoformat()
    return ""


def due_rows(
    tickets: list[dict], days: list[date], today: date, home: bool = True
) -> tuple[dict[str, list[dict]], int, int]:
    """Everything with a day, filed by the column it falls in.

    Three things can happen to a date. Inside the week it lands on its own day,
    except a weekend one which moves back to Friday and says so. Before the
    week's first column it is overdue and lands on the first column the owner
    can still act on, because a chase that slipped is more urgent than one that has not
    come up, not less, and a calendar that hides it is lying by omission. After
    the last column it is counted and named in the header rather than drawn.

    That catching is for the week the owner opens on (`home`). Flipped to another week,
    a date lands on its own day or not at all: the pile of overdue work belongs
    to now, and stacking it onto a Monday three weeks out, or one gone by, would
    put it on a day it has nothing to do with.
    """
    shown = {d.isoformat() for d in days}
    first, last = days[0].isoformat(), days[-1].isoformat()
    # Overdue work stacks on today when today is in this week, and on Monday
    # when the owner is looking at a week they have not started yet.
    catch = today.isoformat() if first <= today.isoformat() <= last else first
    by_day: dict[str, list[dict]] = {}
    late = ahead = 0
    for t in tickets:
        for i in t.get("items", []):
            st = state_of(i)
            if st["closed"]:
                continue
            when = dated(i, st, today)
            if not when:
                continue
            if when > last:
                ahead += 1
                continue
            if not home and when < first:
                continue
            who, tone = whose(i, st)
            overdue = when < first or when < today.isoformat()
            if overdue and (home or when not in shown):
                late += 1
                lands = catch
            elif when in shown:
                lands = when
            else:
                # A Saturday or Sunday, which has no column: back to the Friday.
                d = date.fromisoformat(when)
                while d.weekday() >= 5:
                    d -= timedelta(days=1)
                lands = d.isoformat()
                if lands not in shown:
                    continue
            row = {
                "id": str(i.get("id", "")),
                "title": i.get("title", ""),
                "ref": t.get("ref", ""),
                "who": who,
                "tone": "late" if overdue else tone,
                "late": overdue,
                "on": when,
                # A job drawn on a day that is not its own says which day is,
                # or the strip quietly misreports the date.
                "moved": lands != when,
                # Whether the day came from the board or from the urgency. An
                # inferred day is drawn dashed, because "this-week means
                # tomorrow" is this renderer's opinion and not a fact the owner set.
                "guess": not ISO.fullmatch(
                    (i.get("waits_on") or {}).get("chase_on") or ""
                ),
            }
            by_day.setdefault(lands, []).append(row)
    return by_day, late, ahead


def render(
    board: dict, tickets: list[dict], refs: dict[str, str], offset: int = 0
) -> str:
    """One week, Monday to Friday, with every room and every job due in it.

    `offset` is how many weeks from this one, for the arrows in the header. The
    page always opens on this week; another week is fetched when the owner flips
    to it and swapped in, so a reload brings them back to now. Looking back, the
    overdue pile is not restacked onto that week: overdue is about today, and a
    week behind them shows only what was dated inside it.
    """
    today = date.today()
    offset = max(-52, min(52, offset))
    days = columns(monday_of(today) + timedelta(weeks=offset))
    by_day, late, ahead = due_rows(tickets, days, today, home=offset == 0)

    rooms: dict[str, list[dict]] = {}
    for s in board.get("sessions") or []:
        if s.get("date"):
            rooms.setdefault(s["date"], []).append(s)
    cal = meetings(days)

    # The prioritization lead's thread (`prioritization.who`), and the last day
    # to raise a build before it. Neither is a meeting the owner sits in, so they
    # come from the cadence in config.json rather than the calendar, and both
    # link to the card. With no `prioritization` block there are no rounds and
    # neither is drawn.
    flags: dict[str, list[tuple[str, str]]] = {}
    for r in prioritize.rounds(start=days[0], count=2):
        flags.setdefault(r["date"], []).append((r["at"], "Prioritization"))
        flags.setdefault(r["ask_by"], []).append(("by", "Raise builds"))

    cells = []
    for n, d in enumerate(days):
        iso = d.isoformat()
        sess = sorted(rooms.get(iso, []), key=lambda s: s.get("at") or "")
        # Late first, then the owner's own work, then everyone else's, then by number.
        jobs = sorted(
            by_day.get(iso, []),
            key=lambda r: (not r["late"], r["tone"] != "mine", int(r["id"] or 0)),
        )
        events = sorted(cal.get(iso, []), key=lambda e: e.get("start", ""))
        # A room the owner speaks in is drawn as a room, with the calendar's Join and
        # Notes on it, and not a second time in the list of meetings under it.
        # Everything on the day's schedule is sorted by time so 10:30 sits above 11:00
        # and 11:00 sits above 13:00 regardless of whether an entry is a speaking room
        # or a regular calendar meeting.
        used: set[str] = set()
        timed: list[tuple[str, int, str]] = []
        for idx, (at, what) in enumerate(flags.get(iso, [])):
            sort_at = "08:00" if at == "by" else (at or "08:00")
            timed.append((
                sort_at,
                idx,
                f'<a class="wd-room wd-pr" href="#prioritization">'
                f'<b>{esc(at)}</b><span class="wd-room-w">{esc(what)}</span></a>',
            ))
        for idx, s in enumerate(sess, start=10):
            e = same_room(s, events)
            if e:
                used.add(e["id"])
            links = meet_links(e, over(e)) if e else ""
            sort_at = s.get("at") or (e.get("at") if e else "") or "00:00"
            timed.append((
                sort_at,
                idx,
                f"""<span class="wd-room{" skipped" if s.get("skipped") else ""}"
              title="{esc(s.get("label") or s.get("reason") or "")}"
              {f'data-start="{esc(e["start"])}" data-end="{esc(e["end"])}"' if e else ""}>
              <b>{esc(s.get("at", ""))}</b>
              <span class="wd-room-w">{esc((s.get("tab") or s.get("name") or s.get("kind") or "Session"))}</span>
              {f'<span class="wm-go">{links}</span>' if links else ""}</span>""",
            ))
        rest_meets = [e for e in events if e["id"] not in used]
        lo, hi = DESK_HOURS
        front = [e for e in rest_meets if e.get("work") and lo <= (e.get("at") or "") < hi]
        folded = sorted(
            [e for e in rest_meets if e not in front],
            key=lambda e: (e.get("at") or "99:99", e.get("start") or ""),
        )
        for idx, e in enumerate(front, start=100):
            timed.append((e.get("at") or "00:00", idx, meet_row(e, today)))
        timed.sort(key=lambda item: (item[0], item[1]))
        meet_html = "".join(html for _, _, html in timed)
        if folded:
            meet_html += (
                '<details class="wm-more">'
                f'<summary>{len(folded)} other meeting{"s" if len(folded) != 1 else ""}</summary>'
                + "".join(meet_row(e, today) for e in folded) + "</details>"
            )
        # The owner's own work is never folded away, however long the day is.
        # Only the rest competes for the remaining rows.
        own = [r for r in jobs if r["tone"] in {"mine", "late"}]
        others = [r for r in jobs if r not in own]
        room = max(0, ROWS_PER_DAY - len(own))
        shown, rest = own + others[:room], others[room:]
        rows = "".join(
            f"""<a class="wd-job {esc(r["tone"])}{" guess" if r["guess"] else ""}"
               href="#{esc(refs.get(r["ref"], ""))}"
               title="{esc(r["title"])}{esc(
                   f' (really due {day_words(r["on"])})' if r["moved"]
                   else " (no date on the board, placed by its urgency)"
                   if r["guess"] else ""
               )}">
              <span class="wd-n">{esc(r["id"])}</span>
              <span class="wd-w">{esc(r["title"])}</span>
              <span class="wd-who">{esc(r["who"])}{
                  esc(f' · due {day_words(r["on"])}') if r["moved"] else ""
              }</span></a>"""
            for r in shown
        )
        if rest:
            rows += (
                f'<span class="wd-rest">and {len(rest)} more</span>'
            )
        quiet = not sess and not jobs and not events and not flags.get(iso)
        # A day this week that is already behind the owner reads back rather than
        # empty, so Monday on a Wednesday does not look like nothing is due.
        klass = " ".join(
            filter(
                None,
                [
                    "wd",
                    "today" if d == today else "",
                    "past" if d < today else "",
                    "quiet" if quiet else "",
                ],
            )
        )
        head_note = ""
        if d == today:
            head_note = '<span class="wd-today">Today</span>'
        elif d == today + timedelta(days=1):
            head_note = '<span class="wd-soon">Tomorrow</span>'
        cells.append(f"""
      <li class="{klass}">
        <div class="wd-h">
          <span class="wd-d"><b>{esc(d.strftime("%a"))}</b>
            <i>{esc(d.strftime("%-d"))}</i></span>
          {head_note}
          {f'<span class="wd-c">{len(jobs)}</span>' if jobs else ""}
        </div>
        {f'<div class="wd-meets">{meet_html}</div>' if meet_html else ""}
        {f'<div class="wd-jobs">{rows}</div>' if rows else ""}
        {'<span class="wd-none">&ndash;</span>' if quiet else ""}
      </li>""")

    soon = next_live(board)
    lead = ""
    if soon.get("date"):
        when = "today" if soon["date"] == today.isoformat() else day_words(soon["date"])
        lead = f'Next: {soon.get("tab") or soon.get("name") or "session"} {when}'
        if soon.get("at"):
            lead += f' at {soon["at"]}'
    busiest = max(
        ((len(v), k) for k, v in by_day.items()), default=(0, "")
    )
    warn = ""
    if busiest[0] >= 3 and busiest[1]:
        day = "today" if busiest[1] == today.isoformat() else day_words(busiest[1])
        warn = f'{busiest[0]} land {day}'
    # Which week this is. "This week" on a Monday to Friday, and the dates
    # besides, because on a Saturday the strip is showing the week coming and
    # has to say so rather than looking like a stale one.
    first, last = days[0], days[-1]
    this_week = first <= today <= last
    span = (
        f'{first.strftime("%-d")}&ndash;{last.strftime("%-d %b")}'
        if first.month == last.month
        else f'{first.strftime("%-d %b")} &ndash; {last.strftime("%-d %b")}'
    )
    home = monday_of(today)
    gap = (first - home).days // 7
    name = (
        ("This week" if this_week else "Next week") if gap == 0
        else "Next week" if gap == 1
        else "Last week" if gap == -1
        else f"In {gap} weeks" if gap > 0
        else f"{-gap} weeks ago"
    )
    title = f"{name} &middot; {span}"
    flip = f"""
        <span class="week-flip" role="group" aria-label="Which week">
          <button type="button" data-week="{offset - 1}" aria-label="The week before"
                  title="The week before ([)">&lsaquo;</button>
          {f'<button type="button" class="week-now" data-week="0" title="Back to this week">This week</button>' if offset else ""}
          <button type="button" data-week="{offset + 1}" aria-label="The week after"
                  title="The week after (])">&rsaquo;</button>
        </span>"""
    return f"""
    <section class="week{' week-away' if offset else ''}" id="week" aria-label="The week" data-offset="{offset}">
      <header class="week-h">
        {flip}
        <h2>{title}</h2>
        {f'<span class="week-next">{esc(lead)}</span>' if lead else ""}
        {f'<span class="week-busy">{esc(warn)}</span>' if warn else ""}
        {f'<span class="week-ahead">{ahead} after Friday</span>' if ahead else ""}
        {f'<span class="week-late">{late} overdue</span>' if late else ""}
        {calendar_bit(board, days)}
      </header>
      <ol class="week-days">{"".join(cells)}</ol>
    </section>"""


def calendar_bit(board: dict, days: list[date]) -> str:
    """Where the calendar stands: how fresh it is, and notes nobody has read.

    The button reads the calendar again there and then. The notes line is the
    other half of a meeting: Meet attaches Gemini's notes when it ends, and
    until a sweep has read them the board does not know what was said.
    """
    seen = calendar_sync.seen_at()
    data = calendar_sync.load_raw()
    if not seen:
        label, tip = "Connect calendar", "Read your Google Calendar into the week"
    else:
        label, tip = f"Calendar {calendar_sync.ago(seen)}", "Read the calendar again"
    first, last = days[0].isoformat(), days[-1].isoformat()
    off = bool(data.get("from")) and (last < data["from"] or first > data.get("to", ""))
    unread = calendar_sync.unread_notes(board) if days[0] <= date.today() <= days[-1] else []
    fold = ""
    if unread:
        names = ", ".join(f'{e["title"].strip()} ({day_words(e["date"])})' for e in unread[:4])
        fold = (f'<button type="button" class="week-fold" data-cal-fold title="{esc(names)}">'
                f'{len(unread)} meeting note{"s" if len(unread) != 1 else ""} to fold in</button>')
    return f"""{fold}
        {'<span class="week-off">Calendar not read this far</span>' if off else ""}
        <button type="button" class="week-cal" data-cal-sync title="{esc(tip)}">{esc(label)}</button>"""
