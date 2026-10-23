#!/usr/bin/env python3
"""Push the open board items into Apple Reminders.

The page is a good place to read the list and a bad place to remember it. This
puts every item that is still with the owner into a Reminders list named for
the work (the client's short name and the workstream from `config.json`, such
as "EE payments", or the desk's name when either is unset) with its number,
ticket and estimate, due today, or on the chase date when it is held or sitting
with someone else. Every reminder carries the link to act in.

Finished items are skipped, and existing reminders are cleared first, so
re-running after a refresh does not duplicate.

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


def as_date(value: str) -> str:
    """An AppleScript date expression for 09:30 on the given ISO date."""
    d = datetime.strptime(clean_date(value), "%Y-%m-%d")
    return (
        f'(current date) - (time of (current date)) + (9 * hours) + (30 * minutes) '
        f'+ ({(d.date() - date.today()).days} * days)'
    )


def build(data: dict) -> list[dict]:
    today = date.today().isoformat()
    out = []
    for ticket in data.get("tickets", []):
        ref = ticket.get("ref", "")
        for action in ticket.get("items", []):
            state = item_state(action)
            if state["closed"]:
                continue
            hold = action.get("hold") or {}
            waits = action.get("waits_on") or {}
            mins = action.get("est_minutes")
            title = f"{action.get('id', '?')}. {ref}: {action.get('title', '')}"
            if hold:
                title = f"{title} (not yet)"
            elif state["state"] == "waiting":
                title = f"{title} (chase {state.get('who', 'them')})"
            due = hold.get("revisit") or waits.get("chase_on") or today
            body = [action.get("why", "")]
            if hold:
                body.append(f"On hold: {hold.get('why', '')}")
                body.append(f"Waiting for: {hold.get('until', '')}")
            elif waits.get("what"):
                body.append(f"{waits.get('who', 'They')} owe: {waits['what']}")
            body += [f"{n}. {b}" for n, b in enumerate(action.get("steps", []), 1)
                     if not B.step_done(action, b)]
            if action.get("done_when"):
                body.append(f"Finished when: {action['done_when']}")
            if action.get("link"):
                body.append(action["link"])
            if action.get("draft"):
                body.append("--- draft ---")
                body.append(plain(action["draft"].get("body_ruby", "")))
            out.append(
                {
                    "title": title,
                    "due": clean_date(due),
                    "body": "\n".join(b for b in body if b),
                    "minutes": mins,
                }
            )
    return sorted(out, key=lambda r: r["title"])


def push(items: list[dict], list_name: str, tag: str, old_tags: tuple[str, ...] = ()) -> None:
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
            f"due date:{as_date(item['due'])}"
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
    if not items:
        print("nothing to push")
        return 0

    tag = TAG

    if dry:
        for item in items:
            print(f"{item['due']}  {item['title']}")
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
