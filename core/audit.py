#!/usr/bin/env python3
"""Everything on the board that has quietly gone out of date.

A sweep is good at finding what moved and bad at noticing what it left behind.
The evidence is in the logs: a refresh read the CE ticket, correctly recorded
that both files had been re-uploaded, and left the item still titled as the job
of re-uploading them. It knew. It wrote the fact into `progress_note`, which is
additive and safe, and never went back to reshape the item, which is the actual
job. The same sweep left a chase date four days in the past and a thread
stamped days before the ticket's newest event.

None of that needs judgement to spot, so none of it should depend on an agent
choosing to look. This is the part a script can check: dates that have passed,
records that disagree with each other, work that points at something closed.
Whatever it prints is a thing the last sweep should have done and did not.

It never writes. Run it at the end of a refresh, fix what it lists, run it again.

    ./audit.py            everything, grouped, worst first
    ./audit.py --quiet    only the count, for a gate
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime

import board as B
import plugins
import settings

DATE_IN = re.compile(r"\d{4}-\d{2}-\d{2}")
STALE_HOURS = 6


def as_date(text: object) -> date | None:
    """The first ISO date in a string, when there is one.

    Half these fields hold a condition rather than a date, "at the next session if
    the ticket stays quiet", and that is a legitimate way to write a chase. Only
    the ones that committed to a day can be late.
    """
    found = DATE_IN.search(str(text or ""))
    if not found:
        return None
    try:
        return date.fromisoformat(found.group(0))
    except ValueError:
        return None


def days_past(text: object) -> int | None:
    day = as_date(text)
    return None if day is None else (date.today() - day).days


class Report:
    """Findings, grouped by how much they mislead the owner."""

    def __init__(self) -> None:
        self.groups: dict[str, list[str]] = {}

    def add(self, group: str, line: str) -> None:
        self.groups.setdefault(group, []).append(line)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.groups.values())


def check_dates(b: dict, rep: Report) -> None:
    """Dates that have come and gone.

    A chase date is a promise the owner made to themselves. Once it passes, the page has to say so
    rather than keep showing it in the same grey as next Tuesday.
    """
    for ticket, item in B.items(b):
        if not B.is_open(item):
            continue
        where = f"{item.get('id')} ({ticket.get('ref')})"
        waits = item.get("waits_on") or {}
        hold = item.get("hold") or {}

        late = days_past(waits.get("chase_on"))
        if late is not None and late > 0:
            rep.add(
                "Chase dates that have passed",
                f"  {where}: chase was due {waits['chase_on']}, {late} days ago, "
                f"still waiting on {waits.get('who', 'someone')}. Chase it, or move "
                f"the date to when you actually will.",
            )
        revisit = days_past(hold.get("revisit"))
        if revisit is not None and revisit > 0:
            rep.add(
                "Chase dates that have passed",
                f"  {where}: held until {hold['revisit']}, {revisit} days ago. "
                f"Lift the hold or set a new date.",
            )
        if item.get("state") == "waiting" and not waits.get("chase_on"):
            rep.add(
                "Waiting on someone, with nothing to bring it back",
                f"  {where}: with {waits.get('who', 'someone')} since "
                f"{waits.get('since', 'who knows')}, no chase date. It will sit "
                f"there forever. Give it a date or a condition.",
            )


def check_freshness(b: dict, rep: Report) -> None:
    """Threads nobody has looked at since the ticket last moved.

    Two different facts live on a thread. `last_at` is the newest message in it,
    which stays old and honest when the thread is quiet. `checked` is when a sweep
    last looked at it. The stale case this catches is the second one: an event
    landed on the ticket and nobody has since opened the thread it might have come
    from, so the next sweep reads from the wrong point. Comparing `last_at` alone
    flagged every quiet thread on a moving ticket forever, which is why sweeps
    re-read all of them every time; comparing the later of the two fixes that,
    because a thread looked at today clears even when nothing new was said in it.
    """
    for ticket in b.get("tickets", []):
        events = ticket.get("events") or []
        newest = max((as_date(e.get("on")) for e in events if as_date(e.get("on"))), default=None)
        if newest is None:
            continue
        for th in ticket.get("threads") or []:
            looked = max(
                (d for d in (as_date(th.get("checked")), as_date(th.get("last_at"))) if d),
                default=None,
            )
            if looked is not None and looked < newest:
                rep.add(
                    "Threads not looked at since the ticket last moved",
                    f"  {ticket.get('ref')}: thread \"{th.get('label', '')[:40]}\" "
                    f"last looked at {th.get('checked') or th.get('last_at')}, but the "
                    f"ticket has an event on {newest}. Open it and set `checked` (and "
                    f"`last_at` if a newer message is there), or the next sweep re-reads it.",
                )

    stamp = b.get("checked_at")
    if stamp:
        try:
            age = (datetime.now().astimezone() - datetime.fromisoformat(stamp)).total_seconds()
            if age > STALE_HOURS * 3600:
                rep.add(
                    "The board itself",
                    f"  checked_at is {int(age // 3600)}h old ({stamp}). Anything "
                    f"read off the page now is a guess.",
                )
        except ValueError:
            rep.add("The board itself", f"  checked_at is not a timestamp: {stamp!r}")


def check_wiring(b: dict, rep: Report) -> None:
    """Numbers and links that point at nothing, or at something already finished."""
    index = {str(i.get("id")): (t, i) for t, i in B.items(b)}

    for ticket, item in B.items(b):
        if not B.is_open(item):
            continue
        where = f"{item.get('id')} ({ticket.get('ref')})"
        after = item.get("after")
        if not after:
            continue
        lead = index.get(str(after))
        if lead is None:
            rep.add(
                "Sequencing that points at nothing",
                f"  {where}: after={after}, and there is no item {after}. It will "
                f"never unblock, so it shows as a job that is not one.",
            )
        elif not B.is_open(lead[1]):
            rep.add(
                "Sequencing already spent",
                f"  {where}: after={after}, which closed. Harmless, but drop the "
                f"field so the queue reads true.",
            )

    ids = [str(i.get("id")) for _, i in B.items(b)]
    dupes = sorted({n for n in ids if ids.count(n) > 1})
    if dupes:
        rep.add(
            "Two items sharing a number",
            f"  {', '.join(dupes)}. \"Do 20\" is ambiguous and ./tick.py picks one "
            f"at random. Renumber before anything else.",
        )

    highest = max((int(n) for n in ids if n.isdigit()), default=0)
    if b.get("next_id", 0) <= highest:
        rep.add(
            "The board itself",
            f"  next_id is {b.get('next_id')} but the highest item is {highest}. "
            f"The next writer will collide.",
        )


def check_closed_tickets(b: dict, rep: Report) -> None:
    """Work still open under a ticket Asana says is finished."""
    for ticket in b.get("tickets", []):
        asana = ticket.get("asana") or {}
        if not asana.get("completed"):
            continue
        live = [i for i in ticket.get("items", []) if B.is_open(i)]
        if live:
            nums = ", ".join(str(i.get("id")) for i in live)
            rep.add(
                "Open work on a ticket Asana has closed",
                f"  {ticket.get('ref')} is completed in Asana, but {nums} "
                f"{'is' if len(live) == 1 else 'are'} still open. Close them or say "
                f"why they outlive the ticket.",
            )


def check_duplicate_targets(b: dict, rep: Report) -> None:
    """Two open drafts on one ticket addressed to the same person.

    That is one comment, not two. Three numbers that end in a single Asana comment
    cost the owner three readings and a decision about ordering that does not exist.
    """
    for ticket in b.get("tickets", []):
        seen: dict[str, list[str]] = {}
        for item in ticket.get("items", []):
            if not B.is_open(item):
                continue
            draft = item.get("draft") or {}
            target = str(draft.get("target") or "").strip().lower()
            if target:
                seen.setdefault(target, []).append(str(item.get("id")))
        for target, nums in seen.items():
            if len(nums) > 1:
                rep.add(
                    "Several drafts going to the same place",
                    f"  {ticket.get('ref')}: items {', '.join(nums)} all draft to "
                    f"\"{target[:50]}\". Merge them onto the oldest number and drop "
                    f"the rest.",
                )


def check_form_channels(b: dict, rep: Report) -> None:
    """A new post to a workflow-only channel that skips the form's fields.

    Some teams take requests only through a Slack workflow form, so a free post
    there is the wrong message to paste. `slack.form_channels` in config.json
    names them and their fields. A reply in an existing thread is not a new
    request: its link carries a thread_ts or its target says reply.
    """
    forms = [f for f in settings.section("slack").get("form_channels") or []
             if isinstance(f, dict) and f.get("fields")]
    if not forms:
        return
    for ticket, item in B.items(b):
        if not B.is_open(item):
            continue
        for d in B.drafts(item):
            if not B.unsent(d):
                continue
            target = str(d.get("target") or "")
            link = str(d.get("link") or "")
            if "thread_ts=" in link or "reply" in target.lower():
                continue
            for form in forms:
                names = [str(form.get(k)) for k in ("channel", "name") if form.get(k)]
                if not any(n in target or n in link for n in names):
                    continue
                body = str(d.get("body_en") or d.get("body") or "")
                missing = [str(x) for x in form["fields"] if str(x) not in body]
                if missing:
                    rep.add(
                        "Workflow requests not in the form's fields",
                        f"  {item.get('id')} ({ticket.get('ref')}): \"{target[:50]}\" is "
                        f"missing {', '.join(missing)}.",
                    )


def check_terminal_steps(b: dict, rep: Report) -> None:
    """A step telling the owner to type a command the page does in one press.

    "Then ./tick.py 52 once it is posted" sent them to a terminal for the move
    every draft ends in. The Posted it bar under each draft does it, and says
    what it will do; `reply_from` and `expects_reply` on the draft are how a
    writer tells it, not a step.
    """
    for ticket, item in B.items(b):
        if not B.is_open(item):
            continue
        for step in B.open_steps(item):
            if "tick.py" in str(step):
                rep.add(
                    "A step that needs a terminal",
                    f"  {item.get('id')} ({ticket.get('ref')}): \"{str(step)[:70]}\". "
                    f"Take it out: the draft's Posted it button moves the job.",
                )


# A step starts with a verb. One that starts with a determiner is describing.
STATUS_START = re.compile(r"^(?:The|All|Both|None|Nothing|Still|Already|So far|As of)\b")
MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
BARE_REF = re.compile(
    rf"\bthe \d+\b(?!:\d)(?! (?:{MONTHS}|accounts?|bills?|days?|menus?|files?|rates?|held|"
    rf"documents?|statements?|products?|rows?|cases?|minutes?|hours?)\b)"
)


def check_step_shape(b: dict, rep: Report) -> None:
    """Steps that are not steps.

    One item once had five: a status ("The new hold has been on since Tuesday"),
    an instruction with no outcome ("Write down the time they went on"), and
    "the 40" and "both codes" with nothing to say what they were. Each step is a
    thing the owner does now. Status goes in `where_it_stands`, a list of accounts
    in a `prepared` table, and a number is a group with a name.
    """
    account_re = plugins.account_pattern()
    for ticket, item in B.items(b):
        if not B.is_open(item) or item.get("state") == "waiting":
            continue
        where = f"  {item.get('id')} ({ticket.get('ref')})"
        for n, step in enumerate(item.get("steps") or [], 1):
            text = str(step)
            if B.step_done(item, text):
                continue
            if len(text) > 260:
                rep.add("Steps that are not steps",
                        f"{where} step {n} is {len(text)} characters. One action per "
                        f"step; the detail goes in prepared.")
            if len(account_re.findall(text)) > 2:
                rep.add("Steps that are not steps",
                        f"{where} step {n} lists {len(account_re.findall(text))} accounts. "
                        f"Put them in a prepared table and point at it.")
            if STATUS_START.match(text):
                rep.add("Steps that are not steps",
                        f"{where} step {n} reads as status: \"{text[:60]}\". Move it to "
                        f"where_it_stands.")
            elif BARE_REF.search(text):
                rep.add("Steps that are not steps",
                        f"{where} step {n} says \"{BARE_REF.search(text).group(0)}\" with "
                        f"nothing to say what it is. Name the group.")


def check_milestones(b: dict, rep: Report) -> None:
    """A milestone phase that cannot tell the owner what to do.

    A live phase once read "the client, then Kraken" after the client had handed over:
    two owners, one of them finished, and no job behind it. A live phase names
    the jobs that move it, and one owner.
    """
    for ms in b.get("milestones") or []:
        for n, p in enumerate(ms.get("phases") or [], 1):
            where = f"  {ms.get('id')} phase {n} (\"{str(p.get('what', ''))[:50]}\")"
            if re.search(r",? then |→|->", str(p.get("who", ""))):
                rep.add("Milestone phases that do not say who acts",
                        f"{where}: who is \"{p['who']}\". Split the finished handover "
                        f"into a done phase and give the rest one owner.")
            if p.get("state") in ("now", "blocked"):
                open_items = [i for i in p.get("items") or []
                              if any(str(it.get("id")) == str(i) and B.is_open(it) for _, it in B.items(b))]
                if not open_items:
                    rep.add("Milestone phases that do not say who acts",
                            f"{where} is {p['state']} with no open item. Name the job "
                            f"that moves it, or move the phase on.")
                if (d := as_date(p.get("due"))) and d < date.today():
                    rep.add("Milestone phases that do not say who acts",
                            f"{where} was due {p['due']} and is still {p['state']}.")


def check_prepared(b: dict, rep: Report) -> None:
    """Prepared work built before the ticket last moved.

    The `prepared` block is the answer to a question asked at a point in time. If
    the ticket has moved since, the conclusion on the card may be answering the
    old question, and the owner reads that block as current.
    """
    for ticket, item in B.items(b):
        if not B.is_open(item):
            continue
        prepared = item.get("prepared") or {}
        built = as_date(prepared.get("built_at"))
        if built is None:
            continue
        events = ticket.get("events") or []
        newest = max((as_date(e.get("on")) for e in events if as_date(e.get("on"))), default=None)
        if newest is not None and newest > built:
            rep.add(
                "Prepared work older than the ticket",
                f"  {item.get('id')} ({ticket.get('ref')}): prepared "
                f"{prepared.get('built_at')}, ticket moved {newest}. Re-read it "
                f"before trusting the conclusion on the card.",
            )


def check_sessions(b: dict, rep: Report) -> None:
    """A script written for a room that has already happened."""
    sessions = b.get("sessions") or []
    past = [s for s in sessions if (as_date(s.get("date")) or date.today()) < date.today()]
    for s in past:
        rep.add(
            "Sessions that have been and gone",
            f"  {s.get('date')} {s.get('at', '')} {s.get('label') or s.get('kind', '')}"
            f" is in the past and still listed. Drop it, or the page points at the "
            f"wrong room.",
        )
    for_date = as_date((b.get("script") or {}).get("for_date"))
    if for_date and for_date < date.today():
        rep.add(
            "Sessions that have been and gone",
            f"  script.for_date is {(b.get('script') or {}).get('for_date')}. What "
            f"the owner reads out is written for a session that is over.",
        )


CHECKS = (
    check_dates,
    check_freshness,
    check_wiring,
    check_closed_tickets,
    check_duplicate_targets,
    check_form_channels,
    check_terminal_steps,
    check_step_shape,
    check_milestones,
    check_prepared,
    check_sessions,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quiet", action="store_true", help="just the count")
    args = ap.parse_args()

    b = B.load()
    rep = Report()
    for check in CHECKS + plugins.audit_checks():
        check(b, rep)

    if args.quiet:
        print(f"{rep.total} things out of date")
        return 1 if rep.total else 0

    if not rep.total:
        print("Board audit: nothing out of date.")
        return 0

    print(f"Board audit: {rep.total} things the last sweep left behind.\n")
    for group, lines in rep.groups.items():
        print(f"{group}:")
        for line in lines:
            print(line)
        print()
    print("Every line above is a fix, not a warning. Make them, then run this again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
