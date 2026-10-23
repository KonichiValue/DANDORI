#!/usr/bin/env python3
"""Keep a copy of the board before anything writes it.

`state/board.json` is the only durable file here and it is not in git, so a
sweep that decides a ticket no longer belongs to the owner can take a week of
numbered work with it and leave nothing to read back. That has happened: a
reassignment in Asana dropped a whole ticket, and the only way back was the
rendered pages and the scratch scripts a sweep had left in a temp folder.

So every agent run takes a copy first. Small file, stdlib only, forty deep,
which is a fortnight of runs at three a day.

    python3 keep.py refresh
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import settings

ROOT = settings.REPO
BOARD = settings.STATE_DIR / "board.json"
HISTORY = settings.STATE_DIR / "history"
DEEP = 40


def keep(why: str = "run") -> Path | None:
    """Copy the board aside, named for the time and what is about to run."""
    if not BOARD.exists():
        return None
    HISTORY.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in why if c.isalnum() or c in "-_") or "run"
    out = HISTORY / f"board-{datetime.now():%Y%m%d-%H%M%S}-{safe}.json"
    shutil.copy2(BOARD, out)
    # Oldest first, so the newest DEEP copies are the ones that stay.
    old = sorted(HISTORY.glob("board-*.json"))[:-DEEP]
    for path in old:
        path.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    kept = keep(sys.argv[1] if len(sys.argv) > 1 else "run")
    print(f"kept {kept.relative_to(ROOT)}" if kept else "no board to keep")
