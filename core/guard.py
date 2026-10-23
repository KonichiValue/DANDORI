#!/usr/bin/env python3
"""Refuse any shell command that mentions ktdb, before it runs.

A PreToolUse hook on Bash, wired in by `agent.Claude.settings()` for every job.
ktdb pulls production rows into whatever runs it; ktdb-web exists so those rows
reach a person in a browser and never a model's context. A deny rule only
matches the start of a command, so `~/.local/bin/ktdb`, `sh -c "ktdb ..."` and
`python3 -c` would walk past one. This looks at the whole command. Exit 2
refuses the call and shows the model the reason; a refusal here beats any
allow rule. A call it cannot read is refused too.
"""

import json
import sys


def main() -> int:
    try:
        event = json.loads(sys.stdin.read())
        command = str((event.get("tool_input") or {}).get("command") or "")
    except Exception:  # noqa: BLE001 - fail closed
        print("Refused by guard.py: the command could not be read.", file=sys.stderr)
        return 2
    if "ktdb" in command.lower():
        print("Refused by guard.py: ktdb is never run from a desk job. Production "
              "data is read by a person in ktdb-web, not by an agent.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
