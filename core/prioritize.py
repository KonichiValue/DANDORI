#!/usr/bin/env python3
"""When the next prioritization round is, and which of the owner's builds need raising.

The round is optional. If `config.json` has a `prioritization` block, the
prioritization lead it names (`who`) runs a build prioritization every
`every_days` days and, at `thread_at` on the round's morning, opens the
prioritization confirmation thread in `channel` where CDLs reply with the builds
they want prioritized or adjusted before `cutoff_at` (for example, the afternoon
CDL prioritization meeting on normal Mondays, or earlier on a holiday Tuesday
right before CE's Tuesday commitment meeting). On a normal round day the owner
raises builds in that morning's thread; if the owner is off / PTO on the round
day, `ask_by` shifts back to the previous working day so they post beforehand.
With no block there is no round: every function here answers "none" and the desk
draws no card.

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

import calendar_sync  # noqa: E402
import settings  # noqa: E402

# Done builds are not in the round at all.
FINISHED = {
    "done", "complete", "completed", "released", "closed", "won't do",
    "cancelled", "withdrawn", "cancelled / withdrawn",
}

# Active engineering states: already picked up, so never ask to prioritize them.
ACTIVE_BUILD_STATUS = {
    "in progress", "doing", "building", "in review", "review", "code review",
    "review / signoff", "client verification", "testing", "in testing",
    "ready for release",
}


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


def owner_off(day: date, cfg: dict) -> bool:
    """Whether the owner is on PTO / off on `day` (from config `off_days` or Google Calendar)."""
    off = {str(d)[:10] for d in cfg.get("off_days") or [] if d} | calendar_sync.owner_off_days()
    return day.isoformat() in off


def cutoff_for(round_day: date, planned: date, cfg: dict) -> str:
    """When raising closes on `round_day`.

    Normal Monday rounds stay open until `cutoff_at` (e.g. 13:30 CDL meeting), as
    do rounds pulled earlier to the preceding Friday (`round_day < planned`).
    When a Monday holiday pushes the round later to Tuesday morning (`round_day > planned`),
    CE's Tuesday 11:00 finalization meeting runs the same morning, so raising closes
    earlier (`holiday_cutoff_at`) unless `moved` sets an explicit `cutoff_at`.
    """
    for row in cfg.get("moved") or []:
        if row.get("from") == planned.isoformat() and row.get("cutoff_at"):
            return str(row["cutoff_at"])
    if round_day > planned:
        return str(cfg.get("holiday_cutoff_at") or "10:00")
    return str(cfg.get("cutoff_at") or cfg.get("thread_at") or "13:30")


def working_days_since(start: date, end: date, cfg: dict) -> int:
    """Working days strictly after `start` up to and including `end`."""
    if end <= start:
        return 0
    d = start + timedelta(days=1)
    n = 0
    while d <= end:
        if working(d, cfg):
            n += 1
        d += timedelta(days=1)
    return n


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
                moved = "Mon holiday · runs Tue morning before 10:00"
            ask_day = ask_by(day, cfg)
            out.append({
                "planned": planned.isoformat(),
                "date": day.isoformat(),
                "at": cfg.get("thread_at", "08:30"),
                "cutoff_at": cutoff_for(day, planned, cfg),
                "ask_by": ask_day.isoformat(),
                "pto_shifted": ask_day != day,
                "moved": moved,
            })
        planned += timedelta(days=every)
    return out


def previous(cfg: dict | None = None, today: date | None = None) -> dict:
    """The last round that has already closed, or {} when there is none."""
    if isinstance(cfg, date) and today is None:
        today, cfg = cfg, None
    cfg = config() if cfg is None else cfg
    if not _iso(cfg.get("anchor")):
        return {}
    today = today or today_local()
    every = _every(cfg)
    now = clock() if today == today_local() else "23:59"
    past = [r for r in rounds(cfg, today - timedelta(days=every + 7), 3)
            if r["date"] < today.isoformat()
            or (r["date"] == today.isoformat() and now >= r["cutoff_at"])]
    return past[-1] if past else {}


def ask_by(round_day: date, cfg: dict) -> date:
    """The day the owner raises builds for `round_day`.

    Normally the morning of `round_day` itself, once the prioritization thread
    goes out. If the owner is on PTO / off on `round_day` (or `before_thread` is
    explicitly enabled), step back to the previous working day they are in.
    """
    if not cfg.get("before_thread") and not owner_off(round_day, cfg):
        return round_day
    day = round_day - timedelta(days=1)
    while not working(day, cfg) or owner_off(day, cfg):
        day -= timedelta(days=1)
    return day


def next_round(cfg: dict | None = None, today: date | None = None) -> dict:
    """The next round whose raising window has not closed yet, with days left.

    On the round's own day, before `cutoff_at`, it is still the active round.
    {} when there is no round.
    """
    if isinstance(cfg, date) and today is None:
        today, cfg = cfg, None
    cfg = config() if cfg is None else cfg
    if not _iso(cfg.get("anchor")):
        return {}
    today = today or today_local()
    now = clock() if today == today_local() else "00:00"
    for r in rounds(cfg, today, 3):
        if r["date"] == today.isoformat() and now >= r["cutoff_at"]:
            continue
        r["days"] = (_iso(r["date"]) - today).days
        r["ask_days"] = (_iso(r["ask_by"]) - today).days
        return r
    return {}


def is_scheduled(build: dict, today: date | None = None, cfg: dict | None = None) -> bool:
    """Whether a build is actively committed into a Wave or being built.

    A ticket in a `Wave` (or assigned to an engineer / actively building) has a
    committed cycle window. A bare `client_order` with no `Wave`
    only counts as scheduled during the 2-day handoff right after a round before
    CE assigns Waves; past that window, an empty `Wave` means the build did not
    make capacity or was swapped out mid-cycle and needs follow-up next round.
    """
    if str(build.get("wave") or "").strip():
        return True
    if str(build.get("engineer") or "").strip():
        return True
    status = str(build.get("status") or "").strip().lower()
    if status in ACTIVE_BUILD_STATUS:
        return True
    if str(build.get("client_order") or "").strip():
        prev = previous(cfg, today)
        prev_d = _iso(prev.get("date")) if prev else None
        ref_d = today or today_local()
        if prev_d is None or (prev_d <= ref_d and working_days_since(prev_d, ref_d, cfg or config()) <= 2):
            return True
    return False


def has_motivation(build: dict) -> bool:
    """A refined build is only raised when `push` is not False and `why` states
    a clear reason to push it in this round."""
    if build.get("push") is False:
        return False
    return bool(str(build.get("why") or "").strip())


def kind(
    build: dict,
    raised_kts: set[str] | None = None,
    nxt_date: str = "",
    today: date | None = None,
    cfg: dict | None = None,
) -> str:
    """Which of six things a build is, for this round.

    raise    refined, not in a Wave or ordered, and has a clear 1-sentence motivation (`why`)
    raised   already raised in the thread for this round
    ordered  already has a committed `wave`, fresh `client_order`, or an engineer on it
    parked   refined, but deliberately held back (`push: false` or no motivation to push now)
    refining not refined yet, so it cannot be ordered this round
    done     finished, not part of the round
    """
    status = str(build.get("status", "")).strip().lower()
    if status in FINISHED:
        return "done"
    if is_scheduled(build, today=today, cfg=cfg):
        return "ordered"
    ref = str(build.get("refinement") or build.get("ref") or "").strip()
    if ref == "🟢" or status == "ready to build":
        kt = str(build.get("kt") or "").strip()
        if (raised_kts and kt in raised_kts) or (nxt_date and build.get("raised_for") == nxt_date):
            return "raised"
        if not has_motivation(build):
            return "parked"
        return "raise"
    return "refining"


def fixes(build: dict) -> list[str]:
    """What is out of date on a build, in the words of the fix.

    CE engineers own Status, Wave, and `due_on` on CE build tickets (`due_on` stays
    empty on normal Wave-ordered tickets, which carry `Wave` and `Committed Date`
    instead). Only flag an explicit `due_on` that has already passed.
    """
    out = []
    k = kind(build)
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
    nxt = next_round(cfg, today)
    nxt_date = nxt.get("date") or ""
    nxt_planned = nxt.get("planned") or ""
    last = previous(cfg, today)
    last_round = block.get("last_round") if isinstance(block.get("last_round"), dict) else {}
    lr_date = str(last_round.get("date") or "").strip()
    lr_ordered = {str(k).strip() for k in (last_round.get("ordered") or []) if k}
    raised_block = block.get("raised") if isinstance(block.get("raised"), dict) else {}
    rb_round = str(raised_block.get("round") or "").strip()
    raised_kts = (
        {str(k).strip() for k in (raised_block.get("kts") or []) if k}
        if nxt_date and rb_round in {nxt_date, nxt_planned}
        else set()
    )
    if nxt_date and lr_date == nxt_date:
        raised_kts |= lr_ordered
    # Right after a round closes (up to 2 working days before CE sets Wave in Asana),
    # builds ordered in `last_round` are awaiting Wave assignment rather than re-raising.
    handoff_ordered: set[str] = set()
    prev_d = _iso(last.get("date")) if last else None
    ref_d = today or today_local()
    if prev_d and lr_date == last.get("date") and prev_d <= ref_d and working_days_since(prev_d, ref_d, cfg) <= 2:
        handoff_ordered = lr_ordered
    rows = []
    for b in builds:
        k = kind(b, raised_kts, nxt_date, today=today, cfg=cfg)
        if k == "raise" and str(b.get("kt") or "").strip() in handoff_ordered:
            k = "ordered"
        if k != "done":
            rows.append({**b, "kind": k, "fixes": fixes(b)})
    # Ordered builds by their client order; the rest keep the board's order,
    # which is the order the owner would ask for them in.
    order = {"raise": 0, "raised": 1, "ordered": 2, "parked": 3, "refining": 4}
    rows.sort(key=lambda r: (order[r["kind"]],
                             int(r["client_order"]) if str(r.get("client_order") or "").isdigit() else 999))
    checked = block.get("checked_at", "")
    stale = ""
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
        "last_round": last_round,
        "raised_info": raised_block if raised_kts else {},
        "rows": rows,
        "raise": [r for r in rows if r["kind"] == "raise"],
        "raised": [r for r in rows if r["kind"] == "raised"],
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
    if not nxt or not s["raise"]:
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
            bits = [r.get("size", ""), r.get("url", "")]
            meta = ", ".join(b for b in bits if b)
            mot = str(r.get("why") or "").strip().rstrip(".")
            head = f"- {r.get('kt', '')} {short(r.get('name', ''))}" + (f" ({meta})" if meta else "")
            lines.append(f"{head} - {mot}." if mot else head)
    ordered = [r for r in s["rows"] if r["kind"] == "ordered"]
    if ordered:
        lines.append("")
        sched_bits = []
        for r in ordered:
            detail = (
                f"{order} {r['client_order']}"
                + (f", {r['wave']}" if r.get("wave") else "")
                if r.get("client_order")
                else (r.get("wave") or r.get("status") or "scheduled")
            )
            sched_bits.append(f"{r.get('kt')} ({detail})")
        lines.append("Already scheduled in wave, no change: " + ", ".join(sched_bits))
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
            by_txt = f"before {r['cutoff_at']}" if r["ask_by"] == r["date"] else f"by {r['ask_by']} (off {r['date']})"
            print(f"{r['date']} {r['at']}  ask {who} {by_txt}{note}")
        return 0
    s = summary(B.load())
    nxt = s["next"]
    if nxt:
        by_txt = (
            f"on {nxt['date']} before {nxt.get('cutoff_at') or nxt['at']}"
            if nxt["ask_by"] == nxt["date"]
            else f"by {nxt['ask_by']} (off {nxt['date']})"
        )
        print(f"Next round {nxt['date']} {nxt['at']} ({nxt['days']} days). "
              f"Ask {who} {by_txt}.")
    if s["stale"]:
        print(f"  {s['stale']}")
    short_name = settings.client_short()
    for r in s["rows"]:
        tag = {
            "raise": "RAISE",
            "raised": "RAISED",
            "ordered": f"{short_name}#{r.get('client_order')}" if r.get("client_order") else (r.get("wave") or "IN WAVE")[:9],
            "parked": "hold",
            "refining": "refining",
        }[r["kind"]]
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
