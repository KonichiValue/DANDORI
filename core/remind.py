#!/usr/bin/env python3
"""Push the few jobs that must not be missed into Apple Reminders.

The page is the list. Copying every open job into Reminders as well only made a
second list that went stale and pinged about things the page already showed, so
this pushes an item only when it carries a `remind` block (`board.py` says when
one is earned: rarely). Each goes into a Reminders list named for the work (the
client's short name and the workstream from `config.json`, or the desk's name
when either is unset), due on `remind.on` at `remind.at` (09:30 without one),
with the why, the open steps and the link.

Every reminder this desk wrote is cleared first, so a job that closed or lost
its `remind` drops off, and re-running never duplicates.

Usage:
    python3 remind.py state/board.json
    python3 remind.py state/board.json --list "Work"
    python3 remind.py state/board.json --dry-run
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import board as B
import settings
from render import item_state, plain

# The tag every reminder this desk writes carries in its body, so a re-run can
# clear its own and nothing else. The lower-case tag is still recognised, so a
# reminder pushed under it is cleared rather than duplicated.
TAG = "[DANDORI]"
OLD_TAGS = ("[dandori]",)


def default_list() -> str:
    """The Reminders list to push to, worked out at call time from the settings."""
    client = settings.section("client")
    short = str(client.get("short") or client.get("name") or "").strip()
    stream = settings.workstream().strip()
    if short and stream:
        return f"{short} {stream}"
    return settings.desk_name()


def applescript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    return result.stdout.strip()


def quote(value: str) -> str:
    """AppleScript string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def clean_date(value: str) -> str:
    """Chase dates are written for humans, as in '2026-08-26, at the onsite'."""
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return date.today().isoformat()


def as_date(value: str, at: str = "") -> str:
    """An AppleScript date expression for `at` (HH:MM, default 09:30) on the ISO date."""
    d = datetime.strptime(clean_date(value), "%Y-%m-%d")
    try:
        hh, mm = (int(x) for x in (at or "09:30").split(":", 1))
    except ValueError:
        hh, mm = 9, 30
    return (
        f'(current date) - (time of (current date)) + ({hh} * hours) + ({mm} * minutes) '
        f'+ ({(d.date() - date.today()).days} * days)'
    )


def build(data: dict) -> list[dict]:
    """The open items that carry a `remind` block, and nothing else."""
    out = []
    for ticket in data.get("tickets", []):
        ref = ticket.get("ref", "")
        for action in ticket.get("items", []):
            rem = action.get("remind")
            if not isinstance(rem, dict) or not rem.get("on"):
                continue
            if item_state(action)["closed"]:
                continue
            body = [rem.get("why", "")]
            body += [f"{n}. {b}" for n, b in enumerate(action.get("steps", []), 1)
                     if not B.step_done(action, b)]
            if action.get("done_when"):
                body.append(f"Finished when: {action['done_when']}")
            if action.get("link"):
                body.append(action["link"])
            out.append(
                {
                    "title": f"{action.get('id', '?')}. {ref}: {action.get('title', '')}",
                    "due": clean_date(str(rem["on"])),
                    "at": str(rem.get("at") or ""),
                    "body": "\n".join(plain(str(b)) for b in body if b),
                    "minutes": action.get("est_minutes"),
                }
            )
    return sorted(out, key=lambda r: (r["due"], r["at"], r["title"]))


def push(items: list[dict], list_name: str, tag: str, old_tags: tuple[str, ...] = ()) -> None:
    """Clear this desk's reminders and write `items`. With none, only clear,
    and never create an empty list just to clear it."""
    if not items:
        lines = [
            "tell application \"Reminders\"",
            f"  if exists list {quote(list_name)} then",
            f"    tell list {quote(list_name)}",
            *[f"      delete (every reminder whose completed is false and body contains {quote(t)})"
              for t in (tag, *old_tags)],
            "    end tell",
            "  end if",
            "end tell",
        ]
        applescript("\n".join(lines))
        return
    lines = [
        "tell application \"Reminders\"",
        f"  if not (exists list {quote(list_name)}) then make new list with properties {{name:{quote(list_name)}}}",
        f"  tell list {quote(list_name)}",
    ]
    for t in (tag, *old_tags):
        lines.append(
            f"    delete (every reminder whose completed is false and body contains {quote(t)})"
        )
    for item in items:
        note = f"{item['body']}\n\n{tag}"
        lines.append(
            "    make new reminder with properties {"
            f"name:{quote(item['title'])}, "
            f"body:{quote(note)}, "
            f"due date:{as_date(item['due'], item.get('at', ''))}"
            "}"
        )
    lines += ["  end tell", "end tell"]
    applescript("\n".join(lines))


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2

    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    list_name = default_list()
    if "--list" in args:
        i = args.index("--list")
        list_name = args[i + 1]
        del args[i : i + 2]

    src = Path(args[0])
    if not src.exists():
        print(f"missing {src}", file=sys.stderr)
        return 1
    data = json.loads(src.read_text(encoding="utf-8"))
    items = build(data)
    tag = TAG

    if dry:
        for item in items:
            print(f"{item['due']} {item['at'] or '09:30'}  {item['title']}")
        if not items:
            print("no job carries a remind block")
        return 0

    try:
        push(items, list_name, tag, OLD_TAGS)
    except RuntimeError as exc:
        print(f"could not reach Reminders: {exc}", file=sys.stderr)
        return 1
    print(f"pushed {len(items)} reminders to '{list_name}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
