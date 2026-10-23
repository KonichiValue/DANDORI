#!/usr/bin/env python3
"""Every meeting on the owner's calendar, kept where the page can draw it.

The week strip used to know two rooms, the standup and the weekly, because
those were the ones `sessions` on the board carried. Everything else the owner
walked into (a programme-wide meeting, a session with the client, a huddle)
they found by opening Google Calendar, and its notes by opening the event again
afterwards. So the desk was a one-stop shop for the work and not for the day.

This reads the calendar and writes `state/calendar.json`, which the page draws
from beside the board. It is not on the board on purpose: a meeting is a fact
about the owner's week, not a job, and a sweep rewriting tickets has no business
touching it. Nothing here ever writes to the calendar; the run that reads it is
handed exactly one tool, the search.

The read goes through a headless Claude run because the Google connection is an
MCP server behind the hub and there is no other way in. It is the cheapest run
this repo makes: Haiku, one tool, no project instructions, about 8 cents. The
tool's answer is too long for the model to read and is saved to a file, which is
the point: this reads that file directly, so the events arrive exactly as Google
sent them and the model is never asked to repeat 190KB of JSON.

    ./calendar_sync.py            fetch now, print what it found
    ./calendar_sync.py --show     print what is stored, fetch nothing
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import settings  # noqa: E402
STORE = settings.STATE_DIR / "calendar.json"
# When it last looked, kept apart from the events. The page reloads when
# calendar.json changes, and an hourly look that found nothing new should not
# take the page away from the owner.
SEEN = settings.STATE_DIR / "calendar-seen.json"
LOCK = settings.STATE_DIR / "calendar.lock"

TOOL = "mcp__google__calendar_search_events"

# Words that make a meeting about Kraken delivery work whatever the client or
# the workstream. The owner's own words (the workstream, the client's names,
# `calendar.work_words` in config.json) come from settings.work_words().
KRAKEN_WORDS = ("cdl",)
# An attachment that is the meeting's notes rather than its slides. The
# Japanese words are what Meet and Docs call notes in a Japanese locale.
NOTES = re.compile(r"gemini|メモ|notes|transcript|議事録|minutes", re.I)


def model() -> str:
    """The cheap model the calendar read runs on: Haiku, unless overridden."""
    return settings.env("CALENDAR_MODEL") or settings.model_id("haiku")


def _word(w: str) -> str:
    """One work word as a pattern. A short Latin word (a client's initials, CE)
    must stand on its own, or two letters would match inside every title. A
    space inside a name may be missing or a hyphen, as titles write it."""
    text = r"[\s\-]?".join(re.escape(p) for p in w.lower().split())
    if len(w) <= 3 and re.fullmatch(r"[A-Za-z0-9]+", w):
        return rf"(?<![a-z0-9]){text}(?![a-z0-9])"
    return text


def work_rx() -> re.Pattern | None:
    """A title that is about this work even when no client address is on the
    invite. Built at call time, so an edit to config.json reaches a running
    server. None when there are no words at all."""
    words = sorted({w for w in settings.work_words() + KRAKEN_WORDS if w}, key=len, reverse=True)
    if not words:
        return None
    return re.compile("|".join(_word(w) for w in words), re.I)


def window(today: date | None = None) -> tuple[date, date]:
    """Monday of last week to the Friday after next: the weeks the strip flips to."""
    today = today or settings.now().date()
    monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday += timedelta(days=7)
    return monday - timedelta(weeks=1), monday + timedelta(weeks=2, days=5)


def tidy(raw: dict, me: str, work: re.Pattern | None = None) -> dict | None:
    """One event the way the page uses it, or None for one it never draws.

    Declined, cancelled and all-day events go: an all-day "Home" is the owner's
    working location, not a meeting, and a declined one is a room they are not in.
    `work` is the title pattern from work_rx(), passed in so a whole week is
    matched against one compiled pattern.
    """
    if work is None:
        work = work_rx()
    if raw.get("status") == "cancelled" or raw.get("all_day"):
        return None
    start, end = str(raw.get("start") or ""), str(raw.get("end") or "")
    if len(start) < 16:
        return None
    people = raw.get("attendees") or []
    mine = next((a for a in people if str(a.get("email", "")).lower() == me), None)
    answer = (mine or {}).get("response_status") or ("accepted" if not people else "needsAction")
    if answer == "declined":
        return None
    emails = [str(a.get("email", "")).lower() for a in people]
    # A meeting with one of the client's own domains on the invite is a room
    # where the client hears from the owner, which is the thing worth drawing
    # loudly.
    domains = settings.client_domains()
    client = any(e.endswith(d) for e in emails for d in domains)
    title = str(raw.get("summary") or "(no title)").strip()
    notes = [
        {"title": str(a.get("title") or "Notes"), "url": str(a.get("file_url") or "")}
        for a in raw.get("attachments") or []
        if a.get("file_url") and "document" in str(a.get("mime_type") or "")
        and NOTES.search(str(a.get("title") or ""))
    ]
    return {
        "id": str(raw.get("id") or ""),
        "title": title,
        "date": start[:10],
        "at": start[11:16],
        "until": end[11:16] if end[:10] == start[:10] else "",
        "start": start,
        "end": end,
        "answer": answer,
        "join": str(raw.get("conference_url") or ""),
        "link": str(raw.get("html_link") or ""),
        "notes": notes,
        "client": client,
        "work": bool(client or (work and work.search(title))),
        "people": len(people),
    }


def results(stream: str) -> list[dict]:
    """Every raw event in the run's tool results, read from wherever they went.

    A short answer comes back inline. A long one, which is every real week, is
    written to a file and the result only names it.
    """
    out: list[dict] = []
    for line in stream.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") != "user":
            continue
        for part in (row.get("message") or {}).get("content") or []:
            if not isinstance(part, dict) or part.get("type") != "tool_result":
                continue
            body = part.get("content")
            if isinstance(body, list):
                body = "".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
            body = str(body or "")
            saved = re.search(r"saved to (\S+?\.(?:txt|json))", body)
            if saved:
                try:
                    body = Path(saved.group(1)).read_text(encoding="utf-8")
                except OSError:
                    continue
            try:
                got = json.loads(body)
            except ValueError:
                continue
            if isinstance(got, list) and got and isinstance(got[0], dict) and "text" in got[0]:
                try:
                    got = json.loads(got[0]["text"])
                except ValueError:
                    continue
            if isinstance(got, dict):
                out += [e for e in got.get("items") or [] if isinstance(e, dict)]
    return out


def fetch(timeout: int = 240) -> dict:
    """Read the calendar now and store it. Returns what was stored, or an error."""
    sys.path.insert(0, str(ROOT))
    import agent  # noqa: E402  (stdlib-only repo; agent.py sits beside this)

    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 300:
        return {"error": "a calendar read is already running"}
    LOCK.parent.mkdir(exist_ok=True)
    LOCK.write_text(str(os.getpid()))
    try:
        kind = agent.pick("claude")
        if kind is None or kind.name != "claude":
            return {"error": "Claude Code is not available to read the calendar"}
        first, last = window()
        cmd = [
            kind.binary, "--print", "--model", model(),
            "--tools", "ToolSearch", "--allowedTools", TOOL,
            "--setting-sources", "user",
            "--settings", json.dumps({"apiKeyHelper": "printenv ANTHROPIC_API_KEY"}),
            "--output-format", "stream-json", "--verbose",
        ]
        # Everything but the one search is taken away, so the run cannot write
        # to the calendar or reach anything else even if it wanted to.
        db = settings.database_mcp()
        for gone in ("mcp__asana__*", "mcp__slack__*", "mcp__notion__*",
                     *((f"mcp__{db}__*",) if db else ()), "mcp__glean*", "mcp__google__gmail_*",
                     "mcp__google__drive_*", "mcp__google__sheets_*", "mcp__google__slides_*",
                     "mcp__google__calendar_create*", "mcp__google__calendar_update*",
                     "mcp__google__calendar_delete*", "mcp__google__calendar_respond*",
                     "mcp__google__calendar_suggest*", "mcp__google__search*"):
            cmd += ["--disallowedTools", gone]
        after = last + timedelta(days=1)
        prompt = (
            f"Call {TOOL} with calendar_id 'primary', "
            f"time_min '{first.isoformat()}T00:00:00{settings.offset(first)}', "
            f"time_max '{after.isoformat()}T00:00:00{settings.offset(after)}', page_size 250. "
            "If the result has a non-null next_page_token, call it again with that page_token, "
            "until there is none. Do not read any saved file. Then reply with only the word DONE."
        )
        try:
            ran = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                 cwd="/tmp", env=kind.env(), timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"error": f"the calendar read ran past {timeout}s"}
        raw = results(ran.stdout)
        if not raw:
            tail = (ran.stderr or ran.stdout)[-300:].strip()
            return {"error": "the calendar answered nothing" + (f": {tail}" if tail else "")}
        me = settings.owner_email()
        work = work_rx()
        seen: dict[str, dict] = {}
        for r in raw:
            e = tidy(r, me, work)
            if e and e["id"] not in seen:
                seen[e["id"]] = e
        events = sorted(seen.values(), key=lambda e: (e["start"], e["title"]))
        now = settings.now().isoformat(timespec="minutes")
        data = {"from": first.isoformat(), "to": last.isoformat(), "events": events}
        if load_raw().get("events") != events or load_raw().get("from") != data["from"]:
            data["fetched_at"] = now
            tmp = STORE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            tmp.replace(STORE)
        SEEN.write_text(json.dumps({"at": now, "events": len(events)}) + "\n", encoding="utf-8")
        return {"at": now, "events": len(events), "from": data["from"], "to": data["to"]}
    finally:
        LOCK.unlink(missing_ok=True)


def load_raw() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def seen_at() -> str:
    """When the calendar was last read, whether or not it had changed."""
    try:
        return str(json.loads(SEEN.read_text(encoding="utf-8")).get("at") or "")
    except (OSError, ValueError):
        return str(load_raw().get("fetched_at") or "")


def events_on(day: str) -> list[dict]:
    return [e for e in load_raw().get("events") or [] if e.get("date") == day]


def due(now: datetime | None = None) -> bool:
    """Whether the server should read the calendar again now.

    Hourly through the working day, and again shortly after a meeting about
    this work ends, because that is when Meet attaches the Gemini notes and the
    page can offer them. Nothing at night or at the weekend: the strip does not
    change while the owner is not looking at it.
    """
    now = now or settings.now()
    if now.weekday() >= 5 or not (7 <= now.hour < 20):
        return False
    last = seen_at()
    try:
        then = datetime.fromisoformat(last) if last else None
    except ValueError:
        then = None
    if then is None or now - then > timedelta(minutes=60):
        return True
    # Ten to forty minutes after a work meeting ended, if not looked at since.
    for e in events_on(now.date().isoformat()):
        if not e.get("work") or e.get("notes"):
            continue
        try:
            end = datetime.fromisoformat(e["end"])
        except (KeyError, ValueError):
            continue
        if timedelta(minutes=10) <= now - end <= timedelta(minutes=40) and then < end + timedelta(minutes=10):
            return True
    return False


def _title_rx(name: str) -> re.Pattern | None:
    """A meeting name as a title pattern: a trailing "(...)" dropped, case and
    the spacing or hyphens between words ignored."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", str(name or "")).strip()
    words = [re.escape(w) for w in re.split(r"[\s\-]+", name) if w]
    return re.compile(r"[\s\-]*".join(words), re.I) if words else None


def records() -> list[tuple[re.Pattern, str]]:
    """Which board record already says a room's notes were read.

    The main standup's notes go in `meeting_note`, matched by
    `meeting.notes_title` or its name. The programme-wide meetings in
    `meeting.also` share `migration_note`, unless an entry names another
    record with `"record"` (an empty one keeps that room out of the check).
    Read at call time, so a config edit reaches a running server.
    """
    m = settings.meeting()
    out = []
    for name in {settings.notes_title(), str(m.get("name") or "")}:
        rx = _title_rx(name)
        if rx:
            out.append((rx, "meeting_note"))
    for a in m.get("also") or []:
        if not isinstance(a, dict):
            continue
        rec = str(a.get("record", "migration_note") or "")
        rx = _title_rx(str(a.get("notes_title") or a.get("name") or ""))
        if rx and rec:
            out.append((rx, rec))
    return out


# Gemini writes one document per meeting. The other attachments are running
# documents shared by every occurrence of a series, so they are a Notes link on
# the strip but never evidence that a meeting is new. The Japanese is the title
# Gemini gives its notes in a Japanese locale.
GEMINI = re.compile(r"gemini|によるメモ", re.I)


def unread_notes(board: dict, days: int = 14) -> list[dict]:
    """Work meetings with Gemini notes no sweep has read yet, newest first.

    Read means the notes link is in one of the board's note records, or the
    record that room belongs to is dated that day or later, or `other_notes`
    has the meeting by its day and title. This is what tells a refresh which
    rooms happened since it last looked, and what the page counts as notes to
    fold in.
    """
    urls: set[str] = set()
    named: list[tuple[str, str]] = []
    for n in board.get("other_notes") or []:
        urls.add(str(n.get("url") or ""))
        title = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", str(n.get("title") or "")).strip().lower()
        named.append((str(n.get("date") or ""), title))
    for key in ("meeting_note", "migration_note"):
        urls.add(str((board.get(key) or {}).get("url") or ""))
    now = settings.now()
    floor = (now.date() - timedelta(days=days)).isoformat()
    rooms = records()
    out = []
    for e in load_raw().get("events") or []:
        gem = [n for n in e.get("notes") or [] if GEMINI.search(n.get("title", ""))]
        if not e.get("work") or not gem or e["date"] < floor:
            continue
        try:
            if datetime.fromisoformat(e["end"]) > now:
                continue
        except (KeyError, ValueError):
            continue
        if {n["url"] for n in gem} & urls:
            continue
        mine = e["title"].strip().lower()
        if any(d == e["date"] and t and (t.startswith(mine) or mine.startswith(t))
               for d, t in named):
            continue
        rec = next((k for rx, k in rooms if rx.search(e["title"])), "")
        if rec and str((board.get(rec) or {}).get("date") or "") >= e["date"]:
            continue
        out.append(e)
    return sorted(out, key=lambda e: e["start"], reverse=True)


def ago(stamp: str) -> str:
    """"just now", "12m ago", "3h ago", or the day."""
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    if then.tzinfo is None:
        then = then.replace(tzinfo=settings.tz())
    mins = int((settings.now() - then).total_seconds() // 60)
    if mins < 2:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    if mins < 24 * 60:
        return f"{mins // 60}h ago"
    return f"{then:%-d %b}"


def main() -> int:
    if "--show" in sys.argv:
        data = load_raw()
        print(f"stored {data.get('fetched_at', 'never')}, looked {seen_at() or 'never'}, "
              f"{data.get('from', '?')} to {data.get('to', '?')}")
        for e in data.get("events") or []:
            mark = "*" if e.get("work") else " "
            extra = (" join" if e.get("join") else "") + (" notes" if e.get("notes") else "")
            print(f" {mark} {e['date']} {e['at']} {e['answer'][:4]:<4} {e['title'][:60]}{extra}")
        return 0
    got = fetch()
    if got.get("error"):
        print(got["error"], file=sys.stderr)
        return 1
    print(f"{got['events']} meetings, {got['from']} to {got['to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
