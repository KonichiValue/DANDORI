#!/usr/bin/env python3
"""Move an item along, then rebuild the desk.

An item has one of three lives: it is with you, it is with somebody else, or it
is finished. Sending a message usually moves it to the middle one, and it comes
back to you on the same number when they reply.

    ./tick.py                            where everything is
    ./tick.py --rebuild                  redraw both pages, change nothing
    ./tick.py 4                          finished, nothing comes back
    ./tick.py 2 -w "Alex"                sent, ball is with Alex, chase in 2 working days
    ./tick.py 2 -w "Alex" -n "asked about the account-level hold"
    ./tick.py 2 --mine                   they replied, it is yours again
    ./tick.py 5 --dropped -n "the client answered it themselves"
    ./tick.py 1 --undo                   back to with you, forget the note
    ./tick.py 52 --draft 1               posted draft 1; the job moves once its last draft has gone
    ./tick.py 59 --step 1                step 1 is done (--undo to untick it); the job stays where it is

The page does the last one from the Posted it button on each draft, so nobody
should need a terminal for it: it waits on whoever the drafts expect a reply
from, or closes as sent when nothing comes back. -w overrides who, and --sent
with it says nothing comes back.

Numbers live on the board and are never reused, so "do 4" means the same thing
next week. State lives in `state/board.json` beside the item it describes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import board as B
import keep
import people
import settings
from render import after_posting, item_state

ROOT = settings.REPO
MARK = {
    "todo": "[ ]",
    "hold": "[!]",
    "waiting": "[~]",
    "done": "[x]",
    "sent": "[x]",
    "dropped": "[-]",
}


def show(data: dict) -> None:
    rows = B.items(data)
    if not rows:
        print("nothing on the board")
        return
    tally: dict[str, int] = {}
    for ticket, item in sorted(rows, key=lambda r: int(r[1].get("id", 99))):
        st = item_state(item)
        tally[st["state"]] = tally.get(st["state"], 0) + 1
        who = f" on {st['who']}" if st.get("who") else ""
        note = f"  ({st['note']})" if st.get("note") else ""
        label = "" if st["state"] == "todo" else f"{st['state']}{who}"
        print(
            f"{MARK.get(st['state'], '[ ]')} {item.get('id')}. {label.ljust(22)} "
            f"{ticket.get('ref', '')}: {item.get('title', '')}{note}"
        )
    finished = sum(n for s, n in tally.items() if s in B.CLOSED_STATES)
    print(
        f"\n{tally.get('todo', 0)} with you, {tally.get('hold', 0)} not yet, "
        f"{tally.get('waiting', 0)} with someone else, {finished} finished"
    )


def rebuild() -> None:
    for script, out in (
        ("render_desk.py", "desk.html"),
        ("render_desk_md.py", "desk.md"),
    ):
        subprocess.run(
            [sys.executable, str(settings.CORE / script), str(B.BOARD), str(settings.OUTPUT_DIR / out)],
            check=True,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
        )


def working_days_on(n: int) -> str:
    day = date.today()
    while n:
        day += timedelta(days=1)
        n -= day.weekday() < 5
    return day.isoformat()


def posted(item: dict, k: int, who: str, note: str, chase: str, close: bool = False) -> str:
    """Draft `k` went. Mark it, and move the job if it was the last one.

    Returns what is wrong, or "" when it landed. A job with two messages is not
    finished by the first, so it stays with the owner and says which one has gone.
    `who` overrides who the job waits on; `close` says nothing comes back.
    """
    drafts = B.drafts(item)
    if not 1 <= k <= len(drafts):
        return f"there is no draft {k} (it has {len(drafts)})"
    d = drafts[k - 1]
    target = d.get("target") or f"draft {k}"
    if not B.unsent(d):
        return f"{target} is already marked sent"
    label, owed = after_posting(item, k)
    # The step that said to post it is done too, or the card goes on telling
    # the owner to send something they have just sent.
    sends = B.step_for_draft(item, d)
    if sends:
        step(item, sends)
    d["sent"] = True
    d["sent_at"] = B.now()
    said = note or f"posted: {target}"
    if label.endswith("more to send"):
        item.setdefault("history", []).append(
            {"at": B.now(), "state": item.get("state", "todo"), "note": said}
        )
        return ""
    who = "" if close else who or owed
    if who:
        item.pop("hold", None)
        item["sent_by_you"] = True
        B.set_state(item, "waiting", said, who=who)
        item["waits_on"]["chase_on"] = chase or working_days_on(2)
    elif label == "say who replies" and not close:
        return "a reply is owed but the draft does not say from whom; add -w WHO"
    else:
        item.pop("hold", None)
        item["sent_by_you"] = True
        B.set_state(item, "sent", said)
    return ""


def step(item: dict, k: int, untick: bool = False) -> str:
    """Tick step `k` off, or put it back to do when `untick` says so.

    A step is a line of the owner's own checklist, not a move of the job: the job
    stays with whoever has it, and only its history learns the step went. Ticking
    a ticked step changes nothing, so a double click on the page cannot undo it.
    Returns what is wrong, or "" when it landed.
    """
    steps = [s for s in item.get("steps") or [] if isinstance(s, str)]
    if not 1 <= k <= len(steps):
        return f"there is no step {k} (it has {len(steps)})"
    text = steps[k - 1]
    ticked = item.get("steps_done") if isinstance(item.get("steps_done"), dict) else {}
    ticked = dict(ticked)
    if (text in ticked) != untick:
        return ""
    if untick:
        ticked.pop(text, None)
        said = f"step {k} back to do: {text[:80]}"
    else:
        ticked[text] = B.now()
        said = f"step {k} done: {text[:80]}"
    if ticked:
        item["steps_done"] = ticked
    else:
        item.pop("steps_done", None)
    item.setdefault("history", []).append(
        {"at": B.now(), "state": item.get("state", "todo"), "note": said}
    )
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ids", nargs="*", help="item numbers from the desk")
    parser.add_argument("-n", "--note", default="", help="what happened, in a few words")
    parser.add_argument("-w", "--waiting", metavar="WHO", help="sent, now with this person")
    parser.add_argument(
        "--chase",
        metavar="WHEN",
        default="",
        help="when to chase if they stay quiet: a date (2026-09-16) or a condition. "
        "Use with -w, or on its own to set it on an item already waiting.",
    )
    parser.add_argument("--mine", action="store_true", help="it is back with you")
    parser.add_argument("--sent", action="store_true", help="finished, and it was a message")
    parser.add_argument("--dropped", action="store_true", help="finished, no longer worth doing")
    parser.add_argument("--undo", action="store_true", help="back to with you, clear the note")
    parser.add_argument(
        "--draft",
        metavar="K",
        type=int,
        help="draft K (1 is `draft`, 2 on are `drafts`) was posted. The job waits on "
        "whoever it expects a reply from (or -w), closes as sent when nothing comes "
        "back, and stays with you while another draft is still to send.",
    )
    parser.add_argument(
        "--step",
        metavar="K",
        type=int,
        help="step K is done (with --undo, back to do). The job's state does not move.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="redraw both pages from the board without moving anything",
    )
    args = parser.parse_args()

    data = B.load()
    if not B.BOARD.exists():
        print(f"no board yet. Run: {settings.command()} refresh", file=sys.stderr)
        return 1

    # An agent that has just edited the board needs the pages redrawn and has no
    # state to move. It used to read this file to find the two render commands.
    if args.rebuild:
        rebuild()
        print(f"{settings.client_rel(settings.OUTPUT_DIR / 'desk.html')} and "
              f"{settings.client_rel(settings.OUTPUT_DIR / 'desk.md')} rebuilt from the board")
        if not args.ids:
            return 0

    if not args.ids:
        show(data)
        return 0

    missing = [i for i in args.ids if B.by_id(data, i)[1] is None]
    if missing:
        print(f"no item numbered {', '.join(missing)}", file=sys.stderr)
        return 1

    people.load(data)
    for ident in args.ids:
        _, item = B.by_id(data, ident)
        if args.step is not None:
            problem = step(item, args.step, args.undo)
            if problem:
                print(f"{ident}: {problem}", file=sys.stderr)
                return 1
            continue
        if args.draft is not None:
            problem = posted(
                item, args.draft, args.waiting or "", args.note, args.chase, args.sent
            )
            if problem:
                print(f"{ident}: {problem}", file=sys.stderr)
                return 1
            continue
        if args.undo or args.mine:
            item.pop("hold", None)
            item.pop("waits_on", None)
            item["sent_by_you"] = False
            B.set_state(item, "todo", args.note)
            continue
        if args.waiting:
            item["sent_by_you"] = True
            B.set_state(item, "waiting", args.note, who=args.waiting)
            # Every wait gets a day, or it drops off the week strip and nobody
            # chases it. Two working days is the follow-up nudge most tools
            # default to; --chase overrides it.
            item["waits_on"]["chase_on"] = args.chase or working_days_on(2)
        elif args.dropped:
            B.set_state(item, "dropped", args.note)
        elif args.sent:
            B.set_state(item, "sent", args.note)
        elif args.chase:
            # --chase on its own: set the chase date on an item already waiting,
            # without moving its state or resetting how long it has been there.
            # `waits_on` may be null on a job that was never with anyone.
            if not isinstance(item.get("waits_on"), dict):
                item["waits_on"] = {}
            item["waits_on"]["chase_on"] = args.chase
        else:
            B.set_state(item, "done", args.note)

    # Snapshot before writing, the same as every agent run does. tick.py is the
    # most frequent writer, so it should not be the one without a way back.
    keep.keep("tick")
    B.save(data)
    rebuild()
    # A person at a terminal wants the whole list back. An agent or the page
    # wants to know the move landed, and the whole list is 24KB it then re-reads
    # on every later turn of its run, so it gets the moved items only.
    if sys.stdout.isatty():
        show(data)
    else:
        for ident in args.ids:
            ticket, item = B.by_id(data, ident)
            st = item_state(item)
            who = f" on {st['who']}" if st.get("who") else ""
            print(f"{ident}. {st['state']}{who}  {ticket.get('ref', '')}: {item.get('title', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
