#!/usr/bin/env python3
"""Make the writes a sweep makes, without hand-writing a board script.

The board is edited by `import board; mutate; board.save(b)`. A sweep does the
same handful of edits over and over: stamp a thread it just looked at, add an
event, mark the board swept. Hand-writing python for each is what sends an agent
reading `board.py` and dumping `state/board.json` to remember a field name, which
is most of what makes a sweep slow. This does exactly those edits, one command
each, so the agent never has to reconstruct the schema.

    ./note.py checked <ticket> [--thread SUBSTR] [--last-at "YYYY-MM-DD HH:MM"] [--last-from NAME]
        Stamp `checked` = now on the ticket's threads (all of them, or only those
        whose url contains SUBSTR). --last-at/--last-from also move the watermark,
        but only when a genuinely newer message is there.

    ./note.py event <ticket> --who WHO --what TEXT [--so-what T] [--where W]
                             [--url URL] [--on YYYY-MM-DD] [--at HH:MM]
        Append one event to the ticket's timeline.

    ./note.py swept
        Set the board's `checked_at` to now.

`<ticket>` is matched by `ref` (the tag the owner says out loud, e.g.
HOLD-RELEASE) or by gid. This never touches item state (that is `./tick.py`'s
job), never sends
anything, and never rebuilds the pages (the sweep does that once at the end with
`./tick.py --rebuild`). `board.save` refuses a shape no renderer can survive, so
a clean exit is the confirmation.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import board as B


def find_ticket(b: dict, key: str) -> dict:
    for t in b.get("tickets", []):
        if t.get("ref") == key or str(t.get("id")) == key:
            return t
    refs = ", ".join(t.get("ref", "?") for t in b.get("tickets", []))
    sys.exit(f"no ticket {key!r} on the board. Known: {refs}")


def cmd_checked(b: dict, args: argparse.Namespace) -> str:
    t = find_ticket(b, args.ticket)
    threads = t.get("threads") or []
    if args.thread:
        threads = [th for th in threads if args.thread in (th.get("url") or "")]
    if not threads:
        sys.exit(f"no matching thread on {args.ticket}")
    now = B.now()
    for th in threads:
        th["checked"] = now
        if args.last_at:
            th["last_at"] = args.last_at
        if args.last_from:
            th["last_from"] = args.last_from
    return f"stamped checked on {len(threads)} thread(s) of {t.get('ref')}"


def cmd_event(b: dict, args: argparse.Namespace) -> str:
    t = find_ticket(b, args.ticket)
    t.setdefault("events", []).append({
        "on": args.on or datetime.now().strftime("%Y-%m-%d"),
        "at": args.at,
        "who": args.who,
        "what": args.what,
        "so_what": args.so_what,
        "where": args.where,
        "source_url": args.url,
    })
    return f"added an event to {t.get('ref')} ({len(t['events'])} total)"


def cmd_swept(b: dict, _args: argparse.Namespace) -> str:
    b["checked_at"] = B.now()
    return f"checked_at set to {b['checked_at']}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("checked", help="stamp thread(s) as looked-at now")
    p.add_argument("ticket")
    p.add_argument("--thread", default="", help="only threads whose url contains this")
    p.add_argument("--last-at", dest="last_at", default="", help="newest message, if one is there")
    p.add_argument("--last-from", dest="last_from", default="", help="who sent the newest message")
    p.set_defaults(fn=cmd_checked)

    p = sub.add_parser("event", help="append one event to a ticket's timeline")
    p.add_argument("ticket")
    p.add_argument("--who", required=True)
    p.add_argument("--what", required=True)
    p.add_argument("--so-what", dest="so_what", default="")
    p.add_argument("--where", default="")
    p.add_argument("--url", default="")
    p.add_argument("--on", default="")
    p.add_argument("--at", default="")
    p.set_defaults(fn=cmd_event)

    p = sub.add_parser("swept", help="set the board's checked_at to now")
    p.set_defaults(fn=cmd_swept)

    args = ap.parse_args()
    b = B.load()
    if not b.get("tickets"):
        print("no board yet", file=sys.stderr)
        return 1
    message = args.fn(b, args)
    B.save(b)  # refuses a shape no renderer can survive
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
