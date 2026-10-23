#!/usr/bin/env python3
"""The desk's scheduled jobs, written for this checkout and this owner.

Three launchd agents, each drawn from `config.json` at install time rather than
shipped as a file with somebody else's home directory in it:

    serve   the page server, from login onwards (./install.sh turns it on)
    post    the fold-in of the main standup's notes, as that meeting ends
    brief   the morning sweep on weekdays (opt in: `DANDORI morning`)

    ./launchd.py on serve        write the plist, load it, replace any old one
    ./launchd.py off brief       unload it and delete the plist
    ./launchd.py status          which are loaded
    ./launchd.py show post       print the plist without installing it

Run it again after changing the meeting or the timezone in config.json: the
times are baked into the plist, because launchd has no way to ask.

An agent under the lower-case com.dandori.* label is booted out and deleted
first (launchd labels are case-sensitive), so an upgraded DANDORI never runs a
job twice.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

import settings

ROOT = settings.REPO
AGENTS = Path.home() / "Library" / "LaunchAgents"
PREFIX = "com.DANDORI"
OLD_PREFIXES = ("com.dandori",)
JOBS = ("serve", "post", "brief")

# launchd starts a job with almost nothing on PATH, and claude, node and
# python3 all live somewhere it would not look.
PATH = ":".join([str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin",
                 "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])

WEEKDAY = {d: i + 1 for i, d in enumerate(settings.DAYS)}  # launchd: Mon is 1, Sun is 0 or 7


def label(job: str) -> str:
    return f"{PREFIX}.{job}"


def plist_path(job: str, prefix: str = PREFIX) -> Path:
    return AGENTS / f"{prefix}.{job}.plist"


def _hhmm(text: str, fallback: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(text).split(":")[:2])
        return h, m
    except ValueError:
        return fallback


def build(job: str) -> dict:
    """The plist for one job, as a dict plistlib can write."""
    env = {"PATH": PATH, "PYTHONPATH": str(settings.CORE)}
    if settings.tz_name():
        env["TZ"] = settings.tz_name()
    logs = settings.LOGS_DIR
    plist: dict = {
        "Label": label(job),
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": env,
    }
    if job == "serve":
        # Whichever python3 this shell resolves, since /usr/bin may not hold one.
        python = shutil.which("python3") or "/usr/bin/python3"
        plist.update(
            ProgramArguments=[python, str(settings.CORE / "serve.py"), "--port", str(settings.port())],
            RunAtLoad=True,
            KeepAlive=True,
            StandardOutPath=str(logs / "serve.log"),
            StandardErrorPath=str(logs / "serve.log"),
        )
    elif job == "post":
        # As the main standup ends. run_post.sh then polls for the notes until
        # 45 minutes after, because the notes land a few minutes late and a
        # meeting sometimes overruns.
        h, m = settings.meeting_ends()
        days = settings.meeting()["days"]
        plist.update(
            ProgramArguments=["/bin/zsh", str(settings.SCRIPTS / "run_post.sh")],
            StartCalendarInterval=[{"Weekday": WEEKDAY[d], "Hour": h, "Minute": m} for d in days],
            StandardOutPath=str(logs / "launchd-post.out.log"),
            StandardErrorPath=str(logs / "launchd-post.err.log"),
        )
    elif job == "brief":
        # Weekdays only: nothing on a desk moves on a Saturday, and a brief
        # written then is stale by Monday. A laptop asleep at the hour runs it
        # on waking, which launchd does for a calendar interval on its own.
        h, m = _hhmm(settings.morning_sweep() or "08:15", (8, 15))
        plist.update(
            ProgramArguments=["/bin/zsh", "-lc",
                              f"'{ROOT / 'bin' / 'DANDORI'}' refresh >> '{logs / 'launchd-brief.out.log'}' 2>&1"],
            StartCalendarInterval=[{"Weekday": WEEKDAY[d], "Hour": h, "Minute": m}
                                   for d in settings.DAYS[:5]],
            RunAtLoad=False,
            StandardOutPath=str(logs / "launchd-brief.out.log"),
            StandardErrorPath=str(logs / "launchd-brief.err.log"),
        )
    else:
        raise SystemExit(f"no such job: {job} (one of {', '.join(JOBS)})")
    return plist


def _launchctl(*args: str) -> bool:
    return subprocess.run(["launchctl", *args], capture_output=True).returncode == 0


def off(job: str, prefix: str = PREFIX) -> None:
    uid = os.getuid()
    path = plist_path(job, prefix)
    _launchctl("bootout", f"gui/{uid}/{prefix}.{job}") or (
        path.exists() and _launchctl("unload", "-w", str(path)))
    path.unlink(missing_ok=True)


def on(job: str) -> bool:
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS.mkdir(parents=True, exist_ok=True)
    for old in OLD_PREFIXES:
        off(job, old)
    off(job)
    if job == "serve":
        subprocess.run(["pkill", "-f", f"serve.py --port {settings.port()}"], capture_output=True)
        time.sleep(0.5)
    path = plist_path(job)
    with path.open("wb") as fh:
        plistlib.dump(build(job), fh)
    uid = os.getuid()
    return _launchctl("bootstrap", f"gui/{uid}", str(path)) or _launchctl("load", "-w", str(path))


def loaded() -> set[str]:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    return {line.split()[-1] for line in out.splitlines() if any(p in line for p in (PREFIX, *OLD_PREFIXES))}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    verb, job = argv[0], (argv[1] if len(argv) > 1 else "")
    if verb == "status":
        names = loaded()
        for j in JOBS:
            state = "on" if label(j) in names else "off"
            old = " (old label still loaded)" if any(f"{p}.{j}" in names for p in OLD_PREFIXES) else ""
            print(f"{j:6} {state}{old}")
        return 0
    if job not in JOBS:
        print(f"usage: launchd.py on|off|show <{'|'.join(JOBS)}>", file=sys.stderr)
        return 2
    if verb == "show":
        sys.stdout.write(plistlib.dumps(build(job)).decode())
        return 0
    if verb == "on":
        ok = on(job)
        print(f"{label(job)} {'loaded' if ok else 'written but launchd would not load it'}")
        return 0 if ok else 1
    if verb == "off":
        off(job)
        for old in OLD_PREFIXES:
            off(job, old)
        print(f"{label(job)} off")
        return 0
    print(f"unknown verb: {verb}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
