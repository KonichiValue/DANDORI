#!/usr/bin/env python3
"""When the next prioritization round is, and which of the owner's builds need raising.

The round is optional. If `config.json` has a `prioritization` block, the
prioritization lead it names (`who`) runs a build prioritization every
`every_days` days and, at `thread_at` on the round's morning, posts the result
in `channel`: the builds that got a client order for the next cycle, in order,
and the ones that were not pushed. The lead's standing ask to the CDLs is to
keep their build tickets' dates and status current, and to say which of theirs
should be prioritized or adjusted. After the thread is out it is too late to
say, so the day that matters is the working day before it. With no block there
is no round: every function here answers "none" and the desk draws no card.

The dates come from that block, not the board: a round is `anchor` plus a
multiple of `every_days`, moved to the next working day when it lands on a
weekend or one of `holidays`, and `moved` records a round that went to another
day for any other reason (a holiday the rule already covers may be recorded
there too, with its reason).

What the owner's builds look like comes from the board's `prioritization`
block, which a sweep fills from every CE build whose CDL-Owner is the owner.
That is the only membership rule: a build owned by another CDL never appears,
however close it sits to the owner's tickets. `board.py` documents the shape.
The board's `client_order` field is the client's order number.

    ./prioritize.py          the next round, and what to raise before it
    ./prioritize.py --dates  the next six rounds

It never writes and never sends.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import settings  # noqa: E402

# Done builds are not in the round at all.
FINISHED = {"done", "complete", "completed", "released", "closed", "won't do"}


def config() -> dict:
    """The owner's `prioritization` block, or {} when they have no round."""
    return settings.prioritization()


def lead() -> str:
    """Who runs the round, as the page names them, or "" when nobody is set."""
    return str(config().get("who") or "")


def lead_first() -> str:
    """The lead's first name, for the greeting on the note."""
    return lead().split(" ")[0] if lead() else ""


def today_local() -> date:
    return settings.now().date()


def clock() -> str:
    """The time now as HH:MM in the owner's timezone, which is the one
    `thread_at` is written in."""
    return settings.now().strftime("%H:%M")


def _iso(text) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _every(cfg: dict) -> int:
    """Days between rounds: `every_days`, or fourteen when it is missing or bad."""
    try:
        every = int(cfg.get("every_days") or 14)
    except (TypeError, ValueError):
        return 14
    return every if every > 0 else 14


def working(day: date, cfg: dict) -> bool:
    return day.weekday() < 5 and day.isoformat() not in set(cfg.get("holidays") or [])


def shift(day: date, cfg: dict) -> date:
    """A round that falls on a day nobody works runs on the next one that is."""
    while not working(day, cfg):
        day += timedelta(days=1)
    return day


def actual(planned: date, cfg: dict) -> date:
    """The day a planned round really ran, or will run."""
    for row in cfg.get("moved") or []:
        if row.get("from") == planned.isoformat() and _iso(row.get("to")):
            return _iso(row["to"])
    return shift(planned, cfg)


def rounds(cfg: dict | None = None, start: date | None = None, count: int = 6) -> list[dict]:
    """The rounds from `start` on, planned and actual date, soonest first.

    Counts back from the anchor as well as forward, so a round two weeks before
    the anchor is found without having to be written down anywhere. No anchor,
    which includes no block at all, is no rounds.
    """
    cfg = config() if cfg is None else cfg
    anchor = _iso(cfg.get("anchor"))
    if not anchor:
        return []
    every = _every(cfg)
    start = start or today_local()
    # The first planned date whose actual day could still be on or after start.
    steps = (start - anchor).days // every - 1
    planned = anchor + timedelta(days=every * steps)
    out = []
    while len(out) < count:
        day = actual(planned, cfg)
        if day >= start:
            moved = next(
                (r.get("why", "") for r in cfg.get("moved") or []
                 if r.get("from") == planned.isoformat()), "")
            if not moved and day != planned:
                moved = "the planned day is not a working day"
            out.append({
                "planned": planned.isoformat(),
                "date": day.isoformat(),
                "at": cfg.get("thread_at", "08:30"),
                "ask_by": ask_by(day, cfg).isoformat(),
                "moved": moved,
            })
        planned += timedelta(days=every)
    return out


def previous(cfg: dict | None = None, today: date | None = None) -> dict:
    """The last round that has already run, or {} when there is none."""
    cfg = config() if cfg is None else cfg
    if not _iso(cfg.get("anchor")):
        return {}
    today = today or today_local()
    every = _every(cfg)
    now = clock() if today == today_local() else "23:59"
    past = [r for r in rounds(cfg, today - timedelta(days=every + 7), 3)
            if r["date"] < today.isoformat()
            or (r["date"] == today.isoformat() and now >= r["at"])]
    return past[-1] if past else {}


def ask_by(round_day: date, cfg: dict) -> date:
    """The last working day before the thread goes out.

    The thread is posted first thing on the round's own morning, so anything
    worth raising has to reach the prioritization lead the working day before.
    """
    day = round_day - timedelta(days=1)
    while not working(day, cfg):
        day -= timedelta(days=1)
    return day


def next_round(cfg: dict | None = None, today: date | None = None) -> dict:
    """The next round whose thread has not gone out, with the days left.

    On the round's own day, before the thread time, it is still the next one.
    {} when there is no round.
    """
    cfg = config() if cfg is None else cfg
    if not _iso(cfg.get("anchor")):
        return {}
    today = today or today_local()
    now = clock() if today == today_local() else "00:00"
    for r in rounds(cfg, today, 3):
        if r["date"] == today.isoformat() and now >= r["at"]:
            continue
        r["days"] = (_iso(r["date"]) - today).days
        r["ask_days"] = (_iso(r["ask_by"]) - today).days
        return r
    return {}


def kind(build: dict) -> str:
    """Which of four things a build is, for this round.

    raise    refined and sized, no client order: say it should be ordered
    ordered  has a client order (`client_order`): only its dates and status need to be right
    refining not refined yet, so it cannot be ordered this round
    done     finished, not part of the round
    """
    status = str(build.get("status", "")).strip().lower()
    if status in FINISHED:
        return "done"
    if str(build.get("client_order") or "").strip():
        return "ordered"
    if build.get("refinement") == "🟢" or status == "ready to build":
        return "raise"
    return "refining"


def fixes(build: dict) -> list[str]:
    """What is out of date or missing on a build, in the words of the fix.

    The lead's first ask is that the ticket's own dates and status are current,
    so these are the edits to make in Asana before the round, not things to
    say in the thread.
    """
    out = []
    k = kind(build)
    order = f"{settings.client_short()} Order"
    if k == "ordered":
        if not build.get("due_on"):
            out.append(f"no due date, though it has a {order}")
        if not build.get("wave"):
            out.append(f"{order} but no wave")
    # A CE build's Status after refinement is the refining engineer's to set,
    # not the owner's, so a 🟢 build still reading New is not a fix for them.
    due = _iso(build.get("due_on"))
    if due and due < today_local() and k != "done":
        out.append(f"due date {build['due_on']} has passed")
    return out


def summary(board: dict, today: date | None = None) -> dict:
    """Everything the page, the markdown and the CLI say about the round.

    With no `prioritization` block in config.json, `next` and `last` are {}
    and the renderers draw nothing.
    """
    cfg = config()
    block = board.get("prioritization") or {}
    builds = [b for b in block.get("builds") or [] if isinstance(b, dict)]
    rows = []
    for b in builds:
        k = kind(b)
        if k != "done":
            rows.append({**b, "kind": k, "fixes": fixes(b)})
    # Ordered builds by their client order; the rest keep the board's order,
    # which is the order the owner would ask for them in.
    order = {"raise": 0, "ordered": 1, "refining": 2}
    rows.sort(key=lambda r: (order[r["kind"]],
                             int(r["client_order"]) if str(r.get("client_order") or "").isdigit() else 0))
    nxt = next_round(cfg, today)
    checked = block.get("checked_at", "")
    stale = ""
    last = previous(cfg, today)
    if not builds:
        stale = "No builds have been read yet. A refresh fills this from Asana."
    elif last and checked[:10] < last["date"]:
        stale = f"Read {checked[:10] or 'never'}, before the {last['date']} round."
    elif nxt and nxt.get("ask_days", 99) <= 1 and checked[:10] < (today or today_local()).isoformat():
        stale = f"Read {checked[:10]}. Refresh before you send, the thread is {nxt['date']}."
    return {
        "config": cfg,
        "next": nxt,
        "last": last,
        "last_round": block.get("last_round") or {},
        "rows": rows,
        "raise": [r for r in rows if r["kind"] == "raise"],
        "fixes": [r for r in rows if r["fixes"]],
        "checked_at": checked,
        "stale": stale,
    }


def message(s: dict) -> str:
    """The note to the prioritization lead, in English, built from the board so
    it cannot drift.

    Internal and to a Kraken reader, so KT numbers and sizes are fine here. It
    never goes anywhere by itself: the page shows it with a copy button.
    """
    nxt = s.get("next") or {}
    if not nxt or (not s["raise"] and not s["fixes"]):
        return ""
    when = ""
    if nxt.get("date"):
        when = f" for the {_iso(nxt['date']).strftime('%-d %b')} prioritization"
    hello = f"Hi {lead_first()}" if lead_first() else "Hi"
    order = f"{settings.client_short()} Order"
    lines = [f"{hello}, my builds{when}:"]
    if s["raise"]:
        lines.append("")
        lines.append(f"Please consider for a {order}:")
        for r in s["raise"]:
            bits = [r.get("size", "")]
            if r.get("why"):
                bits.append(r["why"])
            tail = ", ".join(b for b in bits if b)
            lines.append(f"• {r.get('kt', '')} {short(r.get('name', ''))}" + (f" ({tail})" if tail else ""))
    ordered = [r for r in s["rows"] if r["kind"] == "ordered"]
    if ordered:
        lines.append("")
        lines.append("Already ordered, no change: " + ", ".join(
            f"{r.get('kt')} ({order} {r.get('client_order')})" for r in ordered))
    return "\n".join(lines)


def short(name: str) -> str:
    """A build title without the `Client | Workstream |` prefix CE build titles carry."""
    parts = [p.strip() for p in str(name).split("|")]
    return parts[-1] if parts else str(name)


def main() -> int:
    sys.path.insert(0, str(ROOT))
    import board as B

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dates", action="store_true", help="the next six rounds")
    args = ap.parse_args()
    if not config().get("anchor"):
        print("No prioritization round: config.json has no `prioritization` block with an anchor.")
        return 0
    who = lead() or "the prioritization lead"
    if args.dates:
        for r in rounds():
            note = f"  (planned {r['planned']}, {r['moved']})" if r["moved"] else ""
            print(f"{r['date']} {r['at']}  ask {who} by {r['ask_by']}{note}")
        return 0
    s = summary(B.load())
    nxt = s["next"]
    if nxt:
        print(f"Next round {nxt['date']} {nxt['at']} ({nxt['days']} days). "
              f"Ask {who} by {nxt['ask_by']}.")
    if s["stale"]:
        print(f"  {s['stale']}")
    short_name = settings.client_short()
    for r in s["rows"]:
        tag = {"raise": "RAISE", "ordered": f"{short_name}#{r.get('client_order')}", "refining": "refining"}[r["kind"]]
        print(f"  {tag:9} {r.get('kt', ''):10} {r.get('ticket') or '-':8} "
              f"{r.get('status', '')} {r.get('size', '')} due {r.get('due_on') or '-'}")
        for f in r["fixes"]:
            print(f"            fix: {f}")
    text = message(s)
    if text:
        print("\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
