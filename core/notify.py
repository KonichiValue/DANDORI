#!/usr/bin/env python3
"""Proactive background automation and macOS notifications for the desk.

Runs every 10 minutes via `com.DANDORI.doctor` (`launchd.py`) and handles
zero-touch background automation so the owner does not have to run manual
commands or shortcuts:

1. Auto-Prep Before Rooms: within 70 minutes before any client or speaking
   session today (`config.json` `meeting` and `meeting.also`, or `sessions`),
   if `script.for_date` is not yet built for today (or if the afternoon weekly
   needs a fresh `13:00` script after the morning standup), starts `DANDORI prep`
   automatically in the background.
2. Auto-Fold Gemini Notes: after any work meeting on `calendar.json` ends,
   refreshes the calendar and, as soon as Gemini attaches a notes doc that is
   not yet on the board (`calendar_sync.unread_notes` / `standup_notes`), starts
   `DANDORI refresh` automatically in the background to fold it in.
3. Auto-Heal Expired Logins: in pre-meeting windows (08:30-10:15 and 12:00-12:50
   on weekdays), checks MCP grants and Cloudfarer database tokens; if a login
   has expired, starts the browser sign-in flow (`/api/login` or
   `kraken cloudfarer login`) and sends a notification with the link.
4. Proactive Notifications & Apple Reminders Sync: sends native macOS alerts
   15 minutes before meetings and on Prioritization Eve, and keeps Apple
   Reminders (`remind.py`) synced with open board items.

    ./core/notify.py --check            run all automatic checks and actions
    ./core/notify.py --sync-reminders   sync open board items to Apple Reminders
    ./core/notify.py --send "Title" "Message"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CORE = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE))

import board as B  # noqa: E402
import calendar_sync  # noqa: E402
import doctor  # noqa: E402
import prioritize  # noqa: E402
import remind  # noqa: E402
import scout  # noqa: E402
import settings  # noqa: E402

SEEN_FILE = settings.STATE_DIR / "notify-seen.json"
REFRESH_LOCK = settings.STATE_DIR / "refresh.lock"


def _load_seen() -> dict:
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_seen(seen: dict) -> None:
    settings.STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(SEEN_FILE)


def _quote(text: str) -> str:
    return '"' + str(text or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def send_notification(title: str, message: str, subtitle: str = "", sound: str = "Glass") -> bool:
    """Display a native macOS banner notification via osascript."""
    parts = [f"display notification {_quote(message)} with title {_quote(title)}"]
    if subtitle:
        parts.append(f"subtitle {_quote(subtitle)}")
    if sound:
        parts.append(f"sound name {_quote(sound)}")
    script = " ".join(parts)
    res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
    return res.returncode == 0


def notify_once(key: str, title: str, message: str, subtitle: str = "") -> bool:
    """Send a notification only if `key` has not already been sent."""
    seen = _load_seen()
    if key in seen:
        return False
    ok = send_notification(title, message, subtitle=subtitle)
    if ok:
        seen[key] = settings.now().isoformat(timespec="minutes")
        if len(seen) > 250:
            for old_k in list(seen.keys())[:-250]:
                seen.pop(old_k, None)
        _save_seen(seen)
    return ok


def _mark_done(key: str) -> bool:
    """Record an automatic action key so it only triggers once per window."""
    seen = _load_seen()
    if key in seen:
        return False
    seen[key] = settings.now().isoformat(timespec="minutes")
    _save_seen(seen)
    return True


def _sweep_busy() -> bool:
    """Whether a refresh, prep, or post run is currently in flight."""
    if REFRESH_LOCK.exists():
        try:
            pid = int(REFRESH_LOCK.read_text(encoding="utf-8").split()[0])
            os.kill(pid, 0)
            return True
        except (OSError, ValueError, IndexError):
            pass
    port = settings.port()
    serve_file = settings.STATE_DIR / "serve.json"
    if serve_file.exists():
        try:
            key = json.loads(serve_file.read_text(encoding="utf-8")).get("key", "")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status?k={key}", timeout=1.5) as resp:
                st = json.load(resp).get("state")
                if st in ("running", "signing_in"):
                    return True
        except Exception:
            pass
    return False


def _trigger_desk_job(job: str, reason: str) -> bool:
    """Start `refresh`, `prep`, or `login` on the local server (or via bin/DANDORI)."""
    if job in ("refresh", "prep") and _sweep_busy():
        return False
    port = settings.port()
    serve_file = settings.STATE_DIR / "serve.json"
    if serve_file.exists():
        try:
            key = json.loads(serve_file.read_text(encoding="utf-8")).get("key", "")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/{job}?k={key}",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status in (200, 202):
                    send_notification(
                        f"{settings.desk_name()}: Auto-{job.title()}",
                        reason,
                    )
                    return True
        except Exception:
            pass
    if job in ("refresh", "prep"):
        logs = settings.LOGS_DIR
        logs.mkdir(parents=True, exist_ok=True)
        log_fh = (logs / f"auto-{job}.log").open("a", encoding="utf-8")
        subprocess.Popen(
            [str(settings.REPO / "bin" / "DANDORI"), job],
            stdout=log_fh,
            stderr=log_fh,
            cwd=settings.REPO,
        )
        send_notification(f"{settings.desk_name()}: Auto-{job.title()}", reason)
        return True
    return False


def _hhmm_to_minutes(hhmm: str) -> int | None:
    try:
        h, m = (int(x) for x in str(hhmm).strip().split(":")[:2])
        return h * 60 + m
    except ValueError:
        return None


def auto_heal_auth(now: datetime) -> list[str]:
    """Automatically walk expired MCP or Cloudfarer logins before meetings."""
    acted: list[str] = []
    if now.weekday() >= 5:
        return acted
    now_mins = now.hour * 60 + now.minute
    # Pre-meeting windows: 08:30-10:20 and 12:00-12:55
    in_window = (8 * 60 + 30 <= now_mins <= 10 * 60 + 20) or (12 * 60 <= now_mins <= 12 * 60 + 55)
    if not in_window:
        return acted

    today = now.date().isoformat()
    slot = "am" if now.hour < 11 else "pm"

    # 1. Check MCP grants (`asana`, `slack`)
    mcp_check = doctor.check_mcp(deep=True)
    if mcp_check["status"] != "ok" and "login needed" in mcp_check["detail"]:
        key = f"auto-auth-mcp:{today}:{slot}"
        if _mark_done(key):
            _trigger_desk_job("login", f"Reconnecting expired MCP login ({mcp_check['detail']}).")
            acted.append(key)

    # 2. Check Cloudfarer token if database is configured
    db_check = doctor.check_database()
    if db_check["status"] != "ok" and "expired" in db_check["detail"].lower():
        key = f"auto-auth-cf:{today}:{slot}"
        kraken = shutil.which("kraken")
        if kraken and _mark_done(key):
            subprocess.Popen(
                [kraken, "cloudfarer", "login"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            send_notification(
                f"{settings.desk_name()}: Database Login",
                "Started `kraken cloudfarer login` in your browser so replica queries work before your meeting.",
            )
            acted.append(key)

    return acted


def auto_prep_for_rooms(b: dict, now: datetime) -> list[str]:
    """Automatically run `prep` 35-75 minutes before any speaking room today."""
    acted: list[str] = []
    if now.weekday() >= 5 or _sweep_busy():
        return acted

    today = now.date().isoformat()
    dow = settings.DAYS[now.weekday()]
    now_mins = now.hour * 60 + now.minute

    # Collect today's rooms from both `sessions` on the board and `config.json` `meeting`.
    rooms: list[tuple[str, str]] = []
    for s in b.get("sessions") or []:
        if not s.get("skipped") and s.get("date") == today and s.get("at"):
            rooms.append((str(s["at"]), str(s.get("title") or s.get("label") or s.get("kind") or "session")))

    m = settings.meeting()
    if dow in (m.get("days") or []) and m.get("time_local"):
        rooms.append((str(m["time_local"]), str(m.get("name") or "Standup")))
    for a in m.get("also") or []:
        if isinstance(a, dict) and dow in (a.get("days") or []) and a.get("time_local"):
            rooms.append((str(a["time_local"]), str(a.get("name") or a.get("kind") or "Meeting")))

    script = b.get("script") or {}
    for_date = str(script.get("for_date") or "")
    script_at = str(script.get("at") or "")

    for at_str, title in rooms:
        r_mins = _hhmm_to_minutes(at_str)
        if r_mins is None:
            continue
        mins_until = r_mins - now_mins
        # Trigger between 25 and 90 minutes before the room starts so today's
        # script is ready for the 90-minute pre-room delta script sync window.
        if not (25 <= mins_until <= 90):
            continue
        needs_prep = for_date != today or (at_str >= "12:30" and script_at != at_str)
        if not needs_prep:
            continue
        key = f"auto-prep:{today}:{at_str}"
        if _mark_done(key):
            if _trigger_desk_job("prep", f"Building speaking script for {title} ({at_str})."):
                acted.append(key)
            break

    return acted


def auto_fold_meeting_notes(b: dict, now: datetime) -> list[str]:
    """Automatically sync calendar and run `refresh` when Gemini notes land after a meeting."""
    acted: list[str] = []
    if now.weekday() >= 5 or _sweep_busy():
        return acted

    # If a work meeting ended recently without notes in calendar.json yet, fetch calendar first.
    if calendar_sync.stale_for_sweep(now) or calendar_sync.due(now):
        calendar_sync.fetch()

    unread = calendar_sync.unread_notes(b)
    st = calendar_sync.standup_notes(b)
    if st and st.get("ready") and st.get("docs"):
        unread = [st, *unread]

    for ev in unread:
        key = f"auto-fold:{ev.get('date')}:{ev.get('title', '')[:30]}"
        if _mark_done(key):
            title = ev.get("title") or "meeting"
            if _trigger_desk_job("refresh", f"Folding Gemini notes from {title} ({ev.get('at', '')}) into the board."):
                acted.append(key)
            break

    return acted


def sync_reminders(quiet: bool = False) -> bool:
    """Push open board items to Apple Reminders using `remind.py`."""
    if os.environ.get("DANDORI_NO_REMIND") == "1":
        return False
    if not B.BOARD.exists():
        return False
    try:
        data = B.load()
        items = remind.build(data)
        if not items:
            return True
        remind.push(items, remind.default_list(), remind.TAG, remind.OLD_TAGS)
        if not quiet:
            print(f"Synced {len(items)} open item(s) to Apple Reminders ({remind.default_list()!r})")
        return True
    except Exception as exc:
        if not quiet:
            print(f"Could not sync Reminders: {exc}", file=sys.stderr)
        return False


def sync_reminders_bg() -> None:
    """Fire-and-forget Reminders sync so `tick.py` returns immediately."""
    if os.environ.get("DANDORI_NO_REMIND") == "1" or not B.BOARD.exists():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(CORE / "notify.py"), "--sync-reminders", "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=settings.REPO,
        )
    except OSError:
        pass


def check_and_notify() -> list[str]:
    """Run all zero-touch automations and notifications."""
    fired: list[str] = []
    now = settings.now()

    # 1. Auto-heal expired logins before meetings.
    fired.extend(auto_heal_auth(now))

    if not B.BOARD.exists():
        return fired

    b = B.load()
    today = now.date().isoformat()
    desk = settings.desk_name()

    # 2. Auto-prep before today's rooms.
    fired.extend(auto_prep_for_rooms(b, now))

    # 2b. Guaranteed once-per-workday Full Refresh (even when the Mac was shut
    # down at 08:15 and booted later in the morning).
    if not fired and scout.daily_full_refresh_due(now) and not _sweep_busy():
        slot_key = f"auto-daily-refresh:{today}:{now.hour}:{now.minute // 45}"
        if _mark_done(slot_key):
            scout.record_full_attempt()
            if _trigger_desk_job("refresh", "Running today's daily full refresh."):
                fired.append(slot_key)

    # 3. Auto-fold Gemini notes after meetings end.
    fired.extend(auto_fold_meeting_notes(b, now))

    # 4. 15-minute meeting bell for any work/client meeting starting soon.
    for ev in calendar_sync.events_on(today):
        if not ev.get("work"):
            continue
        try:
            start = datetime.fromisoformat(ev["start"])
        except (KeyError, ValueError):
            continue
        mins_until = (start - now).total_seconds() / 60.0
        if 3 <= mins_until <= 20:
            key = f"meeting:{ev.get('id')}:{today}"
            room_tag = "Client in room" if ev.get("client") else "Work meeting"
            msg = f"Starts at {ev.get('at')} ({int(mins_until)} min)."
            if notify_once(key, f"{desk}: {ev.get('title')}", msg, subtitle=room_tag):
                fired.append(key)

    # 5. Prioritization Eve alert (when ask_days is 0 or 1 and builds need raising/fixing).
    if settings.prioritization() and 11 <= now.hour <= 18:
        ps = prioritize.summary(b)
        nxt = ps.get("next") or {}
        to_raise = ps.get("raise") or []
        to_fix = ps.get("fixes") or []
        if nxt and nxt.get("ask_days", 99) in (0, 1) and (to_raise or to_fix):
            key = f"prio-eve:{nxt.get('date')}"
            kts = ", ".join(str(r.get("kt") or "?") for r in to_raise[:3])
            msg = (
                f"{len(to_raise)} build(s) to raise ({kts}) and {len(to_fix)} Asana fix(es) "
                f"due by {nxt.get('ask_by')} for the {nxt.get('date')} round."
            )
            if notify_once(key, f"{desk}: Prioritization deadline", msg):
                fired.append(key)

    return fired


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="run proactive automations and notifications")
    ap.add_argument("--sync-reminders", action="store_true", help="sync open items to Apple Reminders")
    ap.add_argument("--quiet", action="store_true", help="suppress stdout messages")
    ap.add_argument("--send", nargs=2, metavar=("TITLE", "MESSAGE"), help="send a one-off notification")
    args = ap.parse_args(argv)

    if args.send:
        ok = send_notification(args.send[0], args.send[1])
        return 0 if ok else 1

    if args.sync_reminders:
        ok = sync_reminders(quiet=args.quiet)
        return 0 if ok else 1

    fired = check_and_notify()
    if not args.quiet:
        if fired:
            print("Triggered:", ", ".join(fired))
        else:
            print("Nothing due right now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
