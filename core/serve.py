#!/usr/bin/env python3
"""Serve the desk page locally, so the page can do things a file:// page cannot.

Two jobs. It renders the page fresh on every load, from the JSON and the state
file, so what you see is never a stale build. And it gives the Refresh button
somewhere to send its click: the button asks this server to run the agent, the
server reports progress, the page reloads itself when the work lands.

Local only. It binds to the loopback address and every call carries a key
generated at startup, so nothing else on the machine can start an agent run.

    python3 core/serve.py            start on 127.0.0.1 at the desk's port (8787 by default)
    python3 core/serve.py --port N   somewhere else
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, NamedTuple
from urllib.parse import unquote

import agent
import board
import calendar_sync
import keep
import render
import render_desk
import settings
import setup_page

ROOT = settings.REPO
KEY = secrets.token_urlsafe(16)
# Which server drew the page, without giving away the key: /api/status answers
# anyone, and in --lan mode that means anything on the wifi. A restart makes a new
# key, so a page drawn by the last server has every button refused with "bad key"
# while its status line looks perfectly healthy. The page compares this and
# reloads, which is how it picks up the new key.
BOOT = hashlib.blake2s(KEY.encode(), digest_size=4).hexdigest()
# The port the desk itself answers on, and the only one that gets to write
# state/serve.json. The terminal command (`desk.command`), the Dock icon and
# launchd all expect it here.
DESK_PORT = settings.port()
# A refresh is a sweep. A prep is that same sweep and then the script written on
# top of it, so it needs longer before the timeout counts it dead: a lean sweep
# that finishes in time only to have the script it set up killed is the worst
# outcome, because the page then shows yesterday's words on a board swept today.
#
# These are the runaway guard, not the liveness check: the stall watchdog below
# is what catches a dead run. A heavy day is what sized them: with a new ticket
# and an extra meeting to fold in, the sweep half of a prep alone once took
# sixteen minutes on Opus, three of its turns thinking for two to six minutes
# each, and the old twenty-minute cap stopped it before it wrote a word. The run
# that then got through took 34 minutes, so the cap sits well clear of that.
TIMEOUT_SECS = 1800
PREP_TIMEOUT_SECS = 2700
# A launched agent that streams nothing for this long is a dead session (a lapsed
# login, the MCP servers down), not slow work: a real sweep emits a tool call
# within seconds and keeps emitting. Stop it here with a re-auth message rather
# than making the owner watch the full timeout for a run that was never going to do
# anything. "0 tool calls in 900s" is the exact failure this catches.
#
# Claude Code takes its time coming up, though: a cold start spent 160 seconds
# before its first token on this gateway, and killing a run that was only still
# connecting is the worse mistake. So the clock does not start until the agent
# says something, and STARTUP_GRACE_SECS is how long it has to say anything.
#
# While a request to the model is in flight, silence means something else again.
# Thinking arrives whole rather than streamed, so a long think on a 300k-token
# context is minutes with nothing to show, and 150s is what once killed a prep
# 43 tool calls in with every server connected. MODEL_STALL_SECS is that
# case, and a request that truly hangs is still caught: the CLI's own request
# timeout is ten minutes and it reports its retries, which count as life.
STALL_SECS = 150
STARTUP_GRACE_SECS = 300
MODEL_STALL_SECS = 480

job = {"state": "idle", "message": "", "url": "", "at": ""}
job_lock = threading.Lock()

# What the agent is doing this second, for the card that is waiting on it. A
# question takes thirty seconds to two minutes, and a button that says "Sending"
# for all of it is indistinguishable from one that has broken. The agent prints
# as it goes, so the page can show the last thing it said and how long it has
# been going, which is the difference between waiting and wondering.
# One entry per run, keyed like `procs`: "sweep" for a refresh or prep, the ask id
# for a question. A single shared slot meant two questions at once showed each
# other's progress, and the first to finish blanked the line for the rest.
live: dict[str, dict] = {}
live_lock = threading.Lock()

# Questions wait in a line rather than being turned away. Asking about one job
# and then noticing something on the next card is the normal way to read the
# page, and "something is already running, ask again" makes the owner hold the
# second question in their head until the first lands.
ask_queue: "queue.Queue[tuple[str, str, str, str]]" = queue.Queue()

# Questions run a pool at a time, so asking on a second card does not wait out
# the first. Cancel needs the process per question, not one shared slot, or
# cancelling B would reach for A's process.
#
# Nothing here waits for anything else any more. A question used to wait up to
# five minutes for a sweep, and a sweep for every question, because each was a
# read-modify-write of the whole board and the later save erased the earlier.
# board.save() now merges under a lock, field by field, so a question answered
# mid-refresh and the refresh itself both land. Only two sweeps exclude each
# other, through state/refresh.lock, since two of them would both take the
# same event onto the same ticket.
ASK_WORKERS = 4
procs: dict[str, "subprocess.Popen[str]"] = {}   # ask_id (or "sweep") -> live process
asks_running = 0                                  # questions in their agent run right now
cancelled: set[str] = set()
cancel_lock = threading.Lock()


def cancel_ask(ask_id: str) -> str:
    """Stop a question, whether it is running or still in the line."""
    with cancel_lock:
        cancelled.add(ask_id)
        proc = procs.get(ask_id)
    if proc is not None:
        try:
            proc.terminate()
        except OSError:
            pass
        return "stopped"
    # Still in the line, so nothing will touch its row until the worker reaches
    # it. Say so on the row now, or the card redrawn a second later would draw
    # it as still waiting.
    row = next((r for r in load_asks() if r.get("id") == ask_id), None)
    if row and row.get("state") == "queued":
        row.update(state="cancelled", answer="You called this one off.")
        save_ask(row)
    return "dropped from the queue"


def ask_worker() -> None:
    """Drain the queue, one question at a time, for the life of the server."""
    while True:
        args = ask_queue.get()
        try:
            with cancel_lock:
                dead = args[0] in cancelled
            if dead:
                row = next((r for r in load_asks() if r.get("id") == args[0]), None)
                if row:
                    row.update(state="cancelled", answer="You called this one off.")
                    save_ask(row)
                continue
            run_ask(*args)
        except Exception as exc:  # a thread that dies takes the queue with it
            row = next((r for r in load_asks() if r.get("id") == args[0]), None)
            if row:
                row.update(state="failed", answer=f"Could not run the question: {exc}")
                save_ask(row)
            set_job("failed", f"could not run the question: {exc}")
        finally:
            ask_queue.task_done()


def pending_asks() -> dict[str, str]:
    """Every question not yet answered, by id, so a card can find its own.

    A card used to watch the one global job state, which is wrong the moment two
    things are in flight: an ask that finished while a refresh was starting left
    the card spinning on "Sending" with its answer already written to disk.
    """
    with cancel_lock:
        gone = set(cancelled)
    return {
        str(r.get("id")): str(r.get("state"))
        for r in load_asks()
        if r.get("state") in ("queued", "running") and str(r.get("id")) not in gone
    }


def set_live(key: str, last: str = "", add: int = 0, start: bool = False,
             step: bool = False) -> None:
    with live_lock:
        if start or key not in live:
            live[key] = {"since": time.time(), "last": "", "chars": 0, "steps": 0}
            if start:
                return
        if last:
            live[key]["last"] = last
        if step:
            live[key]["steps"] = live[key].get("steps", 0) + 1
        live[key]["chars"] += add


def clear_live(key: str) -> None:
    with live_lock:
        live.pop(key, None)


def live_view() -> dict[str, dict]:
    """Each run's progress as the page reads it: seconds, last words, size."""
    with live_lock:
        return {
            k: {"seconds": int(time.time() - v["since"]), "last": v["last"],
                "written": v["chars"], "steps": v.get("steps", 0)}
            for k, v in live.items()
        }


BOARD = settings.STATE_DIR / "board.json"
ASKS = settings.STATE_DIR / "asks.json"
asks_lock = threading.Lock()

def icon_src(name: str) -> str:
    """The icon path with the file's own timestamp on it.

    Chrome caches an installed app's icon against the URL it was fetched from,
    so an icon redrawn at the same path never reaches the Dock. Moving the URL
    whenever the file changes is what makes a redraw actually show up.
    """
    icon = settings.PACKAGING / name
    stamp = int(icon.stat().st_mtime) if icon.exists() else 0
    return f"/{name}?v={stamp}"


# Chrome reads this when you install the page as an app, and takes the Dock icon
# from it. Without it the installed app wears the Chrome logo. Built per request,
# so redrawing the icon is enough: nothing has to remember to restart the server.
def manifest() -> dict:
    return {
        "name": settings.desk_name(),
        "short_name": "DANDORI",
        "description": "Every open ticket of yours, what is left to do, and what to say.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f4f6fa",
        "theme_color": "#0d1524",
        "icons": [
            {"src": icon_src("icon-192.png"), "sizes": "192x192",
             "type": "image/png", "purpose": "any"},
            {"src": icon_src("icon-512.png"), "sizes": "512x512",
             "type": "image/png", "purpose": "any"},
        ],
    }


render_lock = threading.Lock()
# The board mtime the served desk.md was last rendered from, so a plain reload
# does not redraw a file the browser never sees.
_md_rendered_mtime: float | None = None


def rebuild() -> str:
    """Render the desk and hand back the HTML, so a load is always current.

    Held behind a lock so two overlapping loads do not run two renders into the
    same file at once, which could hand back a half-written page.
    """
    global _md_rendered_mtime
    html = settings.OUTPUT_DIR / "desk.html"
    with render_lock:
        # desk.html on every load: it carries the "checked N hours ago" line, so
        # it has to be current even when the board itself has not moved.
        subprocess.run(
            [sys.executable, str(settings.CORE / "render_desk.py"), str(BOARD), str(html)],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        # desk.md is read by chats, never served from here, so only redraw it when
        # the board changed since we last wrote it. A plain reload of the page
        # then costs one render, not two.
        md = settings.OUTPUT_DIR / "desk.md"
        try:
            bmtime: float | None = BOARD.stat().st_mtime
        except OSError:
            bmtime = None
        if bmtime is None or not md.exists() or _md_rendered_mtime != bmtime:
            subprocess.run(
                [sys.executable, str(settings.CORE / "render_desk_md.py"), str(BOARD), str(md)],
                cwd=ROOT,
                check=False,
                stdout=subprocess.DEVNULL,
            )
            _md_rendered_mtime = bmtime
        return html.read_text(encoding="utf-8")


def last_refreshed() -> str:
    """When the board was last swept, by any route, as the header says it.

    The job's own time only knows runs this server started; a sweep from a chat
    or a terminal writes `checked_at` on the board and nothing here. It is the
    second key in the file, so the head of it is enough at every status poll.
    """
    try:
        with BOARD.open("rb") as fh:
            head = fh.read(4096).decode("utf-8", "ignore")
    except OSError:
        return ""
    m = re.search(r'"checked_at"\s*:\s*"([^"]+)"', head)
    if not m:
        return ""
    try:
        at = datetime.fromisoformat(m.group(1))
    except ValueError:
        return ""
    now = datetime.now(at.tzinfo) if at.tzinfo else datetime.now()
    when = at.strftime("%H:%M") if at.date() == now.date() else at.strftime("%-d %b %H:%M")
    return f"Last refreshed {when}"


def set_job(state: str, message: str = "", url: str = "", kind: str = "sweep") -> None:
    with job_lock:
        job.update(
            state=state, message=message, url=url, kind=kind,
            at=datetime.now().strftime("%H:%M"),
        )


def ask_job(state: str, message: str = "", url: str = "") -> None:
    """The header line, from a question, unless a sweep is using it.

    Each card shows its own question's progress, so the header is only a
    courtesy for asks. A refresh running beside them owns it: "Answered in 40s"
    over a sweep still going would read as the sweep being finished.
    """
    with job_lock:
        if job.get("kind") == "sweep" and job["state"] == "running":
            return
    set_job(state, message, url, kind="ask")


def guarded(work: Callable[[], None], what: str) -> Callable[[], None]:
    """Run a worker so that a crash in it reaches the page.

    The button sets "running" before the thread starts, so anything that escapes
    the worker leaves the spinner turning for ever: the page has been told work
    began and will never be told otherwise. That is exactly what happened to a
    server left running across this change, where `blockers()` raised
    `TimeoutExpired` on a `cursor-agent status` that never answered and Refresh
    read "Starting" until it was restarted. A stuck spinner is worse than a
    failure, because a failure says what to do next.
    """

    def run() -> None:
        try:
            work()
        except Exception:
            detail = traceback.format_exc()
            try:
                settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
                with (settings.LOGS_DIR / "serve.log").open("a", encoding="utf-8") as fh:
                    fh.write(f"\n=== {datetime.now():%H:%M:%S} {what} crashed ===\n{detail}\n")
            except OSError:
                pass
            set_job("failed",
                    f"the {what} could not start, which is a fault in the desk rather "
                    f"than in your work. Nothing was changed. See {settings.client_rel(settings.LOGS_DIR / 'serve.log')}")

    return run


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
URL_IN = re.compile(r"https://\S+")


# The CLI holds its prose to the end in text mode, so a sweep printed nothing
# for as long as it ran and the page had a fixed sentence to show. Asking for
# events instead means every tool call arrives the moment it starts, which is
# what the wait actually consists of. Which flags turn streaming on, and how to
# read what comes back, is what the two CLIs disagree about most, so `agent.py`
# owns both and this file only ever sees ("call" | "said" | "result", text).

# The same step, said in the words the owner would use. The log keeps the raw phrase,
# because that is what a slow run is diagnosed from, but the line under the
# spinner is read by someone waiting rather than someone debugging, and
# "mcp asana: get_task_stories" tells them nothing they wanted to know. Tested
# against the raw phrase in order, so the specific cases come before the general.
PLAIN = (
    ("apply.py", "Writing what it found"),
    ("note.py", "Writing what it found"),
    ("tick.py", "Moving the job"),
    ("audit.py", "Checking what is out of date"),
    ("board.save", "Writing the change"),
    ("digest.py", "Reading the board"),
    ("import board", "Reading the board"),
    ("board.json", "Reading the board"),
    ("asana", "Reading the Asana ticket"),
    ("slack", "Reading the Slack thread"),
    ("notion", "Reading the Notion notes"),
    ("miro", "Reading the Miro board"),
    ("database", "Looking at the data"),
    ("kraken-core", "Reading the Kraken code"),
    ("prompts/", "Reading its own instructions"),
    ("docs/", "Reading the working files"),
)


def plain_phrase(phrase: str) -> str:
    """What that step means, for the person watching rather than the log."""
    low = phrase.lower()
    verb = phrase.split(" ", 1)[0].lower()
    # The verb wins over the path, because "Editing docs/..." is a change they are
    # about to see and reporting it as reading would be a lie about the direction.
    if verb in ("editing", "writing", "deleting"):
        return "Writing the change"
    db = agent.DB.lower()
    for probe, words in ((db, "Looking at the data"),) * bool(db) + PLAIN:
        if probe in low:
            return words
    if verb in ("reading", "searching", "looking", "listing"):
        return "Reading around the ticket"
    if verb == "running":
        return "Working through it"
    return phrase[:70]


def mmss(secs: float) -> str:
    return f"{int(secs) // 60:d}:{int(secs) % 60:02d}"


def read_events(stream, said: list[str], noise: list[str], calls: list[str],
                began: float, beat: list[float], fh=None,
                kind: agent.Kind | None = None,
                thinking: list[bool] | None = None, key: str = "sweep") -> None:
    """Turn the agent's event stream into progress, and into a readable log.

    Three things come out of one pass: the final message, whatever the CLI said
    that was not an event (reconnect notices, warnings), and a timed list of the
    tool calls. The last of those is how a slow sweep is diagnosed later: the log
    shows where the minutes went rather than only what it concluded.

    Which events mean what is the CLI's business, so `kind.event` answers that
    and this only has to know the three things it can be told.

    `thinking`, when given, is kept true while a model request is in flight, so
    the watchdog can tell a model at work from a session that has gone.
    """
    reader = kind or agent.KINDS[0]
    thinking = thinking if thinking is not None else [False]
    try:
        for raw in stream:
            clean = ANSI.sub("", raw).strip()
            if not clean:
                continue
            beat[0] = time.time()   # any line from the agent is a sign of life
            if not clean.startswith("{"):
                noise.append(clean)
                set_live(key, last=clean[-200:])
                if fh is not None:
                    fh.write(clean + "\n")
                    fh.flush()
                continue
            try:
                event = json.loads(clean)
            except ValueError:
                noise.append(clean)
                continue
            what, text = reader.event(event)
            if what in ("model", "idle"):
                thinking[0] = what == "model"
            elif what == "said":
                said.append(text)
                set_live(key, add=len(text))
                # A sweep narrates between calls ("Now let me check the..."), and
                # half a sentence of that is noise on the progress line. It shows
                # the step it is on instead; an ask, which makes few calls, keeps
                # the tail of what it is writing.
                tail = "".join(said)[-200:].replace("\n", " ").strip()
                if tail and key != "sweep":
                    set_live(key, last=tail)
            elif what == "call":
                # The tool runs now, not the model, so a hung MCP call gets
                # the ordinary stall window rather than the model's.
                thinking[0] = False
                calls.append(text)
                set_live(key, last=plain_phrase(text), step=True)
                if fh is not None:
                    fh.write(f"  [{mmss(time.time() - began)}] {text}\n")
                    fh.flush()
            elif what == "result":
                # The whole final message, which is cleaner than the deltas: it
                # is the report without the narration between tool calls.
                said[:] = [text]
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


class Ran(NamedTuple):
    text: str      # the agent's final message
    noise: str     # what the CLI said that was not an event
    code: int | None  # None means it was still going at the timeout
    took: int
    calls: int
    stalled: bool = False  # killed for producing nothing, not for running long


# What the CLI says when it never reached the model. The case that taught it:
# Zscaler let its private-app session expire, an ask started seconds later, and
# every TLS handshake to the gateway failed for three minutes. The CLI's own
# retries gave up and the card read "API Error: Unable to connect to API
# (UNKNOWN_CERTIFICATE_VERIFICATION_ERROR)". Nothing was wrong with the question
# or the certificates; it only needed asking again once Zscaler settled.
CONNECTION_LOST = re.compile(
    r"Unable to connect to API|CERTIFICATE|Connection dropped|Can't reach the API"
    r"|ECONNRESET|ECONNREFUSED|ENOTFOUND|ETIMEDOUT|EAI_AGAIN|socket hang up",
    re.I,
)

# How long to give the network before each further try. A Zscaler reconnect
# settles in under a minute; one that wants the owner to sign in again does not settle
# at all, and the message afterwards says so.
RECONNECT_WAITS = (30, 90)


def connection_lost(ran: Ran) -> bool:
    """Whether a run failed only because it never reached the model.

    No tool call is the safety half: nothing was read or written, so running it
    again cannot apply anything twice.
    """
    # An answer that only mentions ECONNRESET is still an answer, so the final
    # message counts only when it is the CLI's own "API Error" line.
    if ran.calls or (ran.text and not ran.text.startswith("API Error")):
        return False
    return bool(CONNECTION_LOST.search(f"{ran.text}\n{ran.noise}"))


CONNECTION_NOTE = (
    "Could not reach the model: the connection kept failing, which on this Mac is "
    "almost always Zscaler reconnecting or asking you to sign in again. Nothing was "
    "read or changed. Check Zscaler shows connected, then press Send."
)


def run_stream(cmd: list[str], payload: str, timeout: int, fh=None, key: str = "sweep",
               kind: agent.Kind | None = None) -> Ran:
    """One agent run, reported as it goes."""
    runner = kind or agent.KINDS[0]
    began = time.time()
    set_live(key, start=True)
    # The CLI takes some seconds to come up before it says anything, and a blank
    # progress line in that gap reads as a hang.
    set_live(key, last="Starting the agent")
    # The prompt goes in on stdin rather than as an argument. A refresh prompt is
    # 30KB and a prep prompt 25KB, which is a large fraction of the shell's
    # argument limit and pointless to spend when the CLI reads stdin.
    proc = subprocess.Popen(
        cmd + runner.stream,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=runner.env(),
    )
    with cancel_lock:
        procs[key] = proc
    # Written and closed before the reader starts, so the agent sees end-of-input
    # and begins. A CLI still waiting on stdin never emits, which the stall check
    # would then report as a lapsed session.
    try:
        proc.stdin.write(payload)
        proc.stdin.close()
    except OSError:
        pass
    said: list[str] = []
    noise: list[str] = []
    calls: list[str] = []
    beat = [began]   # last time the agent emitted anything; read_events bumps it
    thinking = [False]  # a model request is in flight; read_events keeps it
    reader = threading.Thread(
        target=read_events,
        args=(proc.stdout, said, noise, calls, began, beat, fh, runner, thinking, key),
        daemon=True,
    )
    reader.start()
    # Poll rather than one long wait, so a dead session (no output at all) is
    # caught after STALL_SECS instead of at the full timeout. A run that is
    # working keeps bumping beat; one that launched into nothing never does.
    code: int | None = None
    stalled = False
    while True:
        try:
            code = proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            now = time.time()
            # Before the first word, the grace period applies: a cold start on
            # this gateway has taken over two minutes to its first token, and
            # killing a run that was only still connecting is the worse error.
            # With a model request open, the model's window; otherwise the
            # tools', which are fast when they are alive at all.
            quiet = now - beat[0]
            silent_start = not said and not calls
            if silent_start:
                allowed = STARTUP_GRACE_SECS
            elif thinking[0]:
                allowed = MODEL_STALL_SECS
            else:
                allowed = STALL_SECS
            stalled = quiet >= allowed
            if stalled or now - began >= timeout:
                proc.kill()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
                break
    reader.join(timeout=10)
    with cancel_lock:
        procs.pop(key, None)
    return Ran("".join(said).strip(), "\n".join(noise), code,
               round(time.time() - began), len(calls), stalled)


def blockers() -> tuple[list[str], str]:
    """Everything standing between the button and a real run, in one look.

    Which CLI is answering is decided here rather than held open, because it can
    change between one press and the next: a `claude` whose MCP grants lapse
    should fall through to Cursor without the server being restarted.
    """
    return agent.blockers(agent.pick())


# The last diagnostics run, for the Settings panel. Its own state rather than
# `job`, because pressing Test connections must not look like a sweep starting:
# the Refresh button stays live throughout and the board never moves.
health: dict = {"state": "cold", "at": "", "rows": [], "note": ""}
health_lock = threading.Lock()


def run_check() -> None:
    """Every connection a sweep needs, tested the way a sweep would test it.

    Three separate questions, and one failed prep is what proved they are
    separate. All four MCP servers reported Connected while the prep died six
    minutes in, because what had failed was the model credential, which nothing
    on this page had ever checked.

    So: the credential (can a run reach a model at all), the servers (has the
    owner approved the grants), and the tools (would a run starting now actually have
    Asana and Slack in context, which is the only one that predicts a working
    sweep). `agent.tools_ready` is the expensive one and it goes last.
    """
    started = datetime.now().strftime("%H:%M")
    with health_lock:
        health.update(state="running", at=started, rows=[], note="")

    rows: list[dict] = []

    def add(name: str, ok: bool | None, said: str, fix: str = "") -> None:
        rows.append({"name": name, "ok": ok, "said": said, "fix": fix})
        with health_lock:
            health["rows"] = list(rows)

    kind = agent.pick()
    if kind is None:
        message = agent.blockers(None)[1]
        add("Agent", False, message, "Install Claude Code or remove DANDORI_AGENT.")
        with health_lock:
            health.update(state="done", note="Nothing can run.")
        return
    add("Agent", True, f"{kind.name} is installed.")

    cred = bool(kind.signed_in())
    add(
        "Model credential",
        cred,
        "Reachable." if cred else "apiKeyHelper gave nothing back.",
        "" if cred else "Unlock 1Password, or run `op signin`, then try again.",
    )

    try:
        states = kind.mcp_states()
    except (OSError, subprocess.SubprocessError) as exc:
        states = {}
        add("MCP servers", None, f"Could not ask: {exc}")
    for name in ("asana", "slack", "google", "notion") + ((agent.DB,) if agent.DB else ()):
        if name not in states:
            add(name, None, "Not configured.", "Run ./setup-mcp.sh")
            continue
        state = states[name]
        add(
            name,
            state == "connected",
            {"connected": "Connected.", "needs-auth": "Needs authorising."}.get(
                state, state.title() + "."
            ),
            "" if state == "connected" else "Press Log in, or run ./setup-mcp.sh",
        )

    if not cred:
        with health_lock:
            health.update(state="done", note="No credential, so the tool check was skipped.")
        return
    agent.forget_readiness()
    ready, why = agent.tools_ready(kind)
    add(
        "Tools in a real run",
        ready,
        "Asana and Slack arrive with their tools." if ready else why,
        "" if ready else "Run ./setup-mcp.sh, then test again.",
    )
    bad = [r for r in rows if r["ok"] is False]
    with health_lock:
        health.update(
            state="done",
            note="Everything a sweep needs is there."
            if not bad
            else f"{len(bad)} thing{'s' if len(bad) != 1 else ''} to fix before a sweep.",
        )


# The calendar read, for the week strip. Its own state for the same reason as
# `health`: reading the calendar is not a sweep, and the Refresh button stays
# live while it runs.
cal: dict = {"state": "idle", "said": ""}
cal_lock = threading.Lock()


def read_calendar() -> None:
    """One calendar read, its outcome kept where /api/status can report it."""
    with cal_lock:
        if cal["state"] == "running":
            return
        cal.update(state="running", said="Reading the calendar")
    try:
        got = calendar_sync.fetch()
    except Exception as exc:  # noqa: BLE001  (a thread that raises says nothing)
        got = {"error": str(exc)}
    with cal_lock:
        if got.get("error"):
            cal.update(state="failed", said=str(got["error"])[:200])
        else:
            cal.update(state="done", said=f"{got['events']} meetings")


def calendar_loop() -> None:
    """Read the calendar when it is due: hourly, and just after a work meeting.

    Never dies: a lapsed Google grant or a locked credential is one failed read,
    reported on the page, and the next due read tries again.
    """
    time.sleep(20)
    while True:
        try:
            if calendar_sync.due():
                read_calendar()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(60)


def connect_one(kind: agent.Kind, args: list[str], label: str, done) -> bool:
    """Run one auth command, show the link it prints, wait for it to land.

    Driving Terminal through AppleScript needs an automation permission this
    process does not have, so the command runs as a child here and the page
    shows the link. The owner approves it in the browser and this notices.
    """
    try:
        proc = subprocess.Popen(
            [kind.binary, *args],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=kind.env(),
        )
    except OSError as exc:
        login_say("needs_login", f"could not start {label}: {exc}")
        return False

    login_say("signing_in", f"Starting {label}")
    lines: list[str] = []
    threading.Thread(
        target=lambda: [lines.append(ANSI.sub("", ln)) for ln in proc.stdout],
        daemon=True,
    ).start()

    url, deadline = "", time.time() + 25
    while time.time() < deadline and not url and not done():
        for line in list(lines):
            found = URL_IN.search(line)
            if found:
                url = found.group(0).rstrip('.,")')
                break
        if not url:
            time.sleep(0.5)

    login_say(
        "signing_in",
        f"Approve {label} in your browser, then come back here." if url else
        f"{label} printed no link. In a Terminal run: {kind.binary} {' '.join(args)}",
        url,
    )

    end = time.time() + 240
    while time.time() < end:
        if done():
            return True
        if proc.poll() is not None:
            time.sleep(2)
            return done()
        time.sleep(3)
    return False


def login_hint(left: list[str]) -> str:
    """What to press next, for a blocker the Log in button can actually clear.

    Log in walks MCP grants. A locked 1Password vault is not one, so telling
    the owner to "approve each one" after a credential failure sent them to a
    button that walked a grant nobody needed and then said "Connected". For the
    credential, Log in is only a retry of the vault, so it says that.
    """
    if "account" in left:
        return " Then press Log in to fetch it again."
    if set(left) <= set(agent.NEEDED_MCPS) | {"tools"}:
        return " Press Log in and approve each one."
    return ""


# Held for the whole of a sign-in walk. A second press of Log in while one is
# out used to start a second walk, and a sweep started mid-walk had its
# "running" overwritten by the walk's closing "Connected", so the page showed
# an idle desk over a live prep and Refresh answered 409 to nobody.
login_lock = threading.Lock()


def signing_in() -> bool:
    with job_lock:
        return job["state"] == "signing_in"


def login_say(state: str, message: str, url: str = "") -> None:
    """The header line from a sign-in walk, unless a sweep is live under it.

    A walk outlasts the press that started it by minutes, and its last word
    landing over a prep that had begun meanwhile is what left the page idle
    over a running sweep.
    """
    with cancel_lock:
        if "sweep" in procs:
            return
    with job_lock:
        if job["state"] == "running" and job.get("kind") == "sweep":
            return
    set_job(state, message, url)


def start_login() -> None:
    """Walk everything that needs authorising, one browser approval at a time.

    The account sign-in is only half of it. Asana and Slack are separate grants,
    and a refresh without them reads nothing at all.

    A server nobody has configured cannot be logged in to, so that case is sent
    to `setup-mcp.sh` rather than attempted: `claude mcp login asana` on a
    machine with no Asana server would fail in a way that reads like a lapsed
    grant when it is really a missing one.

    Only the servers a run may call are walked. `claude mcp list` reports every
    server in the user's config, and a Glean grant this desk never uses held the
    page on "Approve Glean_Default" for minutes.

    Every phase says what it is doing as state `signing_in`, which the page
    shows as progress rather than as the red error it was pressed to fix.
    """
    if not login_lock.acquire(blocking=False):
        return
    try:
        _walk_logins()
    finally:
        login_lock.release()


def _walk_logins() -> None:
    kind = agent.pick()
    if kind is None:
        login_say("needs_login", agent.blockers(None)[1])
        return
    login_say("signing_in", "Fetching the model credential. 1Password may ask for Touch ID.")
    if not kind.signed_in():
        # The credential comes from apiKeyHelper, not a browser flow, so there
        # is no link this could open.
        login_say("needs_login", agent.blockers(kind)[1] + login_hint(["account"]))
        return
    login_say("signing_in", "Credential fetched. Checking which connections need approving")
    try:
        states = kind.mcp_states()
    except (OSError, subprocess.SubprocessError) as exc:
        login_say("needs_login", f"could not read the connections: {exc}")
        return
    absent = [n for n in agent.NEEDED_MCPS if n not in states]
    if absent:
        names = " and ".join(n.title() for n in absent)
        login_say("needs_login",
                f"{names} {'is' if len(absent) == 1 else 'are'} not set up on this "
                f"machine yet. In a Terminal run: ./setup-mcp.sh")
        return
    wanted = agent._mcp_allow()
    for name, state in states.items():
        if state != "needs-auth" or name not in wanted:
            continue
        connect_one(
            kind,
            kind.login_args(name),
            f"the {name.title()} connection",
            lambda n=name: kind.mcp_states().get(n) == "connected",
        )
    # A grant has just changed, so any remembered readiness answer is about the
    # world before the login. Ask again from scratch, or the page reports the
    # problem the owner has this second finished fixing.
    agent.forget_readiness()
    login_say("signing_in", "Checking that a run would have Asana and Slack")
    left, message = blockers()
    if left:
        login_say("needs_login", message)
    else:
        login_say("idle", "Signed in and connected. Nothing has run yet: press Refresh or prep.")


LOCK = settings.STATE_DIR / "refresh.lock"


# A lock older than the longest job could ever run is stale by definition: the
# holder crashed, or the server was killed mid-ask and restarted. Age is the only
# honest test for an ask, which writes the server's own pid into the lock, so the
# pid stays alive long after the ask that wrote it has gone and the pid check
# alone would wedge the board for the life of the server.
MAX_LOCK_AGE_SECS = PREP_TIMEOUT_SECS + 120


def lock_held() -> bool:
    """True when a refresh, prep or ask is genuinely running, here or in a terminal."""
    if not LOCK.exists():
        return False
    try:
        if time.time() - LOCK.stat().st_mtime > MAX_LOCK_AGE_SECS:
            LOCK.unlink(missing_ok=True)  # stale: older than any job could run
            return False
        pid = int(LOCK.read_text().split()[0])
        os.kill(pid, 0)
    except (ValueError, IndexError, OSError):
        LOCK.unlink(missing_ok=True)  # stale: whoever held it is gone
        return False
    return True


JOBS = {
    # endpoint name -> prompt, what the page says while it runs
    "refresh": ("core/prompts/refresh.md", "Reading every open ticket and the threads behind them"),
    "prep": ("core/prompts/prep.md", "Sweeping everything, then writing your script"),
}

ASK_TIMEOUT_SECS = 420


def ask_model() -> str:
    """Which model answers a question from a card.

    Opus 5.5, like every other run here. A question and a change arrive through
    the same box and half of what comes out of it is read by the client, so the
    answer is worth the wait.

    The wait divides in two, and the logs say where: an ask that only answers
    comes back in 11 to 34 seconds, and one that rewrites a draft takes 100 to
    200 with six to nine tool calls. Session startup is the floor of the first
    number, so it is a tenth of a rewrite rather than most of it, and the rest is
    the client-language text itself. A long draft can be 4.5KB across
    `body_ruby` and `body_en`, and asking for a simpler version means emitting all of it again.
    No model choice shortens that, which is why the lever worth pulling is in
    `prompts/ask.md`: edit the board by item number in one step, and never rebuild
    pages that a page load redraws anyway.

    `config.json` under `ask` pins one when speed matters more than the wording,
    and `DANDORI_ASK_MODEL` does it for a single question.
    """
    return agent.model_for("ask")


def ask_model_ids() -> set[str]:
    """What the picker may send. Anything else is ignored rather than passed to the
    CLI, so a stale page or a typed request can never name a model nobody chose."""
    return {m for m, _, _ in agent.ask_models()}


def load_asks() -> list[dict]:
    """Every question asked from a card, and what came back.

    Kept beside the board rather than in it. The board is the work; this is the
    conversation about the work, and an agent rewriting the board should never
    be able to lose it.
    """
    if not ASKS.exists():
        return []
    try:
        return json.loads(ASKS.read_text(encoding="utf-8")).get("asks", [])
    except (ValueError, OSError):
        return []


def save_ask(row: dict) -> None:
    """Write one question, or the answer that arrives minutes later."""
    with asks_lock:
        rows = [r for r in load_asks() if r.get("id") != row.get("id")]
        rows.append(row)
        ASKS.parent.mkdir(exist_ok=True)
        ASKS.write_text(
            json.dumps({"asks": rows[-200:]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def forget_ask(ident: str) -> int:
    """Throw one exchange away, and anything asked off the back of it.

    The record is the owner's. A question they regret asking, or an answer that turned out
    to be about the wrong job, should not sit on the card forever, and a
    follow-up has no meaning once the thing it followed is gone.
    """
    with asks_lock:
        rows = load_asks()
        doomed = {ident}
        # A follow-up to a follow-up is possible, so keep pulling until nothing
        # new falls in.
        while True:
            more = {
                r.get("id") for r in rows if r.get("parent") in doomed and r.get("id")
            } - doomed
            if not more:
                break
            doomed |= more
        left = [r for r in rows if r.get("id") not in doomed]
        if len(left) == len(rows):
            return 0
        ASKS.parent.mkdir(exist_ok=True)
        ASKS.write_text(
            json.dumps({"asks": left}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return len(rows) - len(left)


def clear_asks(ref: str = "") -> int:
    """Throw away one conversation, or every one when `ref` is empty.

    The panel's Clear. A question still queued or running stays, because its
    answer is on its way back to that row and would only land as an orphan.
    """
    with asks_lock:
        rows = load_asks()
        left = [
            r for r in rows
            if (ref and r.get("ref") != ref)
            or r.get("state") in ("queued", "running")
        ]
        if len(left) == len(rows):
            return 0
        ASKS.parent.mkdir(exist_ok=True)
        ASKS.write_text(
            json.dumps({"asks": left}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return len(rows) - len(left)


prefs_lock = threading.Lock()
PREF_KEY = re.compile(r"(desk|fold)-[\w@.:-]{1,80}")


def save_prefs(changes: dict) -> int:
    """Fold a batch of pref changes into state/prefs.json. None deletes one."""
    with prefs_lock:
        now_set = render.prefs()
        n = 0
        for k, v in changes.items():
            if not PREF_KEY.fullmatch(str(k)):
                continue
            if v is None:
                now_set.pop(k, None)
            else:
                now_set[str(k)] = str(v)[:200]
            n += 1
        # A fold is remembered by name, and names come and go with the board, so
        # the oldest go first once there are more than anyone could have opened.
        folds = [k for k in now_set if k.startswith("fold-")]
        for k in folds[:-400]:
            now_set.pop(k, None)
        tmp = render.PREFS.with_name("prefs.json.tmp")
        tmp.write_text(json.dumps(now_set, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, render.PREFS)
        return n


# What the status button on a job may do. The same moves tick.py already has, and
# nothing that takes free text other than a note and a name.
TICKS = {
    "done": lambda n, who, note: [n] + (["-n", note] if note else []),
    "sent": lambda n, who, note: [n, "--sent"] + (["-n", note] if note else []),
    "dropped": lambda n, who, note: [n, "--dropped"] + (["-n", note] if note else []),
    "waiting": lambda n, who, note: [n, "-w", who] + (["-n", note] if note else []),
    "mine": lambda n, who, note: [n, "--mine"],
    "undo": lambda n, who, note: [n, "--undo"],
    # One draft posted, from the bar under it. The draft's number is added
    # below. `who` is only there when the owner picked someone; otherwise tick.py reads
    # who answers off the drafts.
    "posted": lambda n, who, note: [n] + (["-w", who] if who else []),
    # One line of the job's own checklist ticked, or unticked with `off`. The
    # step's number is added below. The job itself does not move.
    "step": lambda n, who, note: [n],
}


def tick_from_page(body: dict) -> tuple[int, dict]:
    """The status button: move one job from the page, the way ./tick.py does.

    The owner presses it themselves, on the job they mean, so this is them saying
    it is done rather than an agent inferring it. Undo puts back exactly what was
    there before, from the copy keep.py took, rather than guessing.
    """
    n = str(body.get("n", "")).strip()
    verb = str(body.get("verb", "done"))
    who = str(body.get("who", "")).strip()[:80]
    note = str(body.get("note", "")).strip()[:300]
    if not n.isdigit():
        return 400, {"error": "say which job and what happened to it"}
    # The page's buttons come from static/ on every load, but this file only on
    # a restart, so a move it does not know is almost always a stale server.
    if verb not in TICKS:
        return 400, {"error": f"this desk server does not know '{verb}' yet; restart it "
                     "(launchctl kickstart -k gui/$UID/com.DANDORI.serve)"}
    if verb == "waiting" and not who:
        return 400, {"error": "say who it is with"}
    k = str(body.get("draft", "")).strip()
    if verb == "posted" and not k.isdigit():
        return 400, {"error": "say which draft went"}
    step = str(body.get("step", "")).strip()
    if verb == "step" and not step.isdigit():
        return 400, {"error": "say which step"}
    _, item = board.by_id(board.load(), n)
    if item is None:
        return 404, {"error": f"there is no job {n}"}
    if verb == "undo" and body.get("before"):
        return undo_tick(n, body["before"])
    before = {k: item.get(k) for k in TICK_FIELDS if k in item}
    args = TICKS[verb](n, who, note)
    if verb == "posted":
        # "Nothing comes back" from the bar's menu closes it as sent instead.
        args += ["--draft", k] + (["--sent"] if body.get("close") else [])
    if verb == "step":
        args += ["--step", step] + (["--undo"] if body.get("off") else [])
    done = subprocess.run(
        [sys.executable, str(settings.CORE / "tick.py"), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    if done.returncode:
        return 500, {"error": (done.stderr or done.stdout).strip()[-300:] or "tick.py failed"}
    said = {
        "done": f"{n} done", "sent": f"{n} sent", "dropped": f"{n} dropped",
        "waiting": f"{n} is with {who}", "mine": f"{n} is yours again",
        "undo": f"{n} is back with you",
        "step": f"Step {step} on {n} {'back to do' if body.get('off') else 'done'}",
    }.get(verb) or posted_said(n)
    reply = {"said": said, "before": before, **drawn()}
    if verb == "posted":
        # The step that said to post it ticks with it. The page paints it, since
        # it does not reload to show its own move.
        reply["steps"] = ticked_steps(n, before.get("steps_done"))
    return 200, reply


def ticked_steps(n: str, had) -> list[int]:
    """Which of job `n`'s steps are done now that were not in `had`, 1-based."""
    _, item = board.by_id(board.load(), n)
    had = had if isinstance(had, dict) else {}
    now = (item or {}).get("steps_done")
    now = now if isinstance(now, dict) else {}
    steps = [s for s in (item or {}).get("steps") or [] if isinstance(s, str)]
    return [k for k, text in enumerate(steps, 1) if text in now and text not in had]


def drawn() -> dict:
    """The stamps the board now has, for a page that has just moved it itself.

    The page already shows the move, so it takes these as what it was drawn
    from and does not reload to show the same thing again. A reload after every
    press was the flash that made the status look like it broke the page.
    """
    return {"stamp": render.stamp(), "board": render.stamp(False)}


def posted_said(n: str) -> str:
    """The toast after a Posted it press: where the job went, read back off the board."""
    _, item = board.by_id(board.load(), n)
    st = render.item_state(item or {})
    if st["state"] == "waiting":
        return f"Posted. {n} is waiting on {st['who'] or 'them'}"
    if st["closed"]:
        return f"Posted. {n} is closed"
    left = sum(1 for d in board.drafts(item or {}) if board.unsent(d))
    return f"Posted. {left} more to send on {n}"


# The fields a tick moves, so Undo can put exactly these back.
TICK_FIELDS = ("state", "state_at", "state_note", "waits_on", "hold", "closed_at",
               "sent_by_you", "draft", "drafts", "steps_done")


def undo_tick(n: str, before: dict) -> tuple[int, dict]:
    """Put a job back the way it was a moment ago, and say so in its history."""
    if not isinstance(before, dict):
        return 400, {"error": "nothing to put back"}
    b = board.load()
    _, item = board.by_id(b, n)
    if item is None:
        return 404, {"error": f"there is no job {n}"}
    for k in TICK_FIELDS:
        if k in before and before[k] is not None:
            item[k] = before[k]
        else:
            item.pop(k, None)
    if item.get("state") not in board.OPEN_STATES + board.CLOSED_STATES:
        item["state"] = "todo"
    item.setdefault("history", []).append(
        {"at": board.now(), "state": item.get("state", "todo"), "note": "undone from the page"}
    )
    keep.keep("undo")
    try:
        board.save(b)
    except ValueError as exc:
        return 409, {"error": str(exc)[:300]}
    # desk.md too, which a chat reads; the page itself redraws on its own.
    subprocess.run([sys.executable, str(settings.CORE / "tick.py"), "--rebuild"], cwd=ROOT,
                   capture_output=True, timeout=60)
    return 200, {"said": f"{n} put back", **drawn()}


def act_on(body: dict) -> tuple[int, dict]:
    """Make the move an answer proposed, now that the owner has pressed it.

    Only a move the answer actually wrote, on a job that card is about, in one
    of the four shapes `./tick.py` already knows. The button carries the words,
    but the server reads them again from the saved answer, so nothing the page
    sends can move an item the agent did not propose for this card.
    """
    ident = str(body.get("id", ""))
    n, verb, who = str(body.get("n", "")), str(body.get("verb", "")), str(body.get("who", ""))
    row = next((r for r in load_asks() if r.get("id") == ident), None)
    if not row or row.get("state") != "answered":
        return 404, {"error": "no such answer"}
    if row.get("acted_on"):
        return 409, {"error": row["acted_on"]}
    _, moves = render.proposed(row.get("answer", ""))
    if not any((m["n"], m["verb"], m["who"]) == (n, verb, who) for m in moves):
        return 400, {"error": "that answer did not propose this"}
    ticket, item = find_subject(row.get("ref", ""))
    mine = {str(item.get("id"))} if item else {
        str(i.get("id")) for i in ticket.get("items", [])
    }
    if n not in mine:
        return 400, {"error": f"{n} is not on this card"}
    args = {
        "done": [n],
        "mine": [n, "--mine"],
        "dropped": [n, "--dropped"] + (["-n", who] if who else []),
        "waiting": [n, "-w", who],
    }[verb]
    done = subprocess.run(
        [sys.executable, str(settings.CORE / "tick.py"), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    if done.returncode:
        return 500, {"error": (done.stderr or done.stdout).strip()[-300:] or "tick.py failed"}
    said = {
        "done": f"Marked {n} done",
        "mine": f"{n} is yours again",
        "dropped": f"Dropped {n}",
        "waiting": f"{n} is with {who}",
    }[verb]
    row["acted_on"] = f"{said}, {datetime.now().strftime('%H:%M')}"
    save_ask(row)
    return 200, {"acted_on": row["acted_on"]}


def find_subject(ref: str) -> tuple[dict, dict]:
    """The ticket and item a question is about, from `item:8` or `ticket:<ref>`.

    Resolved here rather than trusted from the page, so the prompt is built from
    the board itself and a question can never be asked about something that is
    not on it. `board` is the whole desk, for the questions that are not about
    any one job: what to start on, what tomorrow needs, what the owner is missing.
    """
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    if ref == "board":
        return {"ref": "board", "title_en": "the whole desk", "items": []}, {}
    kind, _, which = ref.partition(":")
    for ticket in board.get("tickets", []):
        if kind == "ticket" and str(ticket.get("ref")) == which:
            return ticket, {}
        for item in ticket.get("items", []):
            if kind == "item" and str(item.get("id")) == which:
                return ticket, item
    return {}, {}


def board_context(question: str, extra: list[str] | None = None) -> str:
    """A question about the desk itself rather than one job on it.

    "What do I start on", "what does tomorrow need from me", "what am I
    missing": answering those means seeing everything open at once, so this is
    the shape of the whole board rather than one card, and the file itself is one
    read away when the shape is not enough.
    """
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    lines = ["", "---", "", "The owner is asking about the desk as a whole, not one job."]
    for s in board.get("sessions", [])[:3]:
        lines.append(
            f"Session: {s.get('kind', '')} on {s.get('date', '')} at {s.get('at', '')}"
            f", {s.get('title') or s.get('name', '')}"
            + (" (not running)" if s.get("skipped") else "")
        )
    lines.append("")
    lines.append("Every open job, by ticket:")
    shut = {"done", "dropped"}
    for t in board.get("tickets", []):
        open_items = [i for i in t.get("items", []) if i.get("state") not in shut]
        if not open_items:
            continue
        lines.append(f"  {t.get('ref', '')} ({t.get('title_en', '')}):")
        for i in open_items:
            mins = i.get("est_minutes")
            waits = (i.get("waits_on") or {}).get("who", "")
            lines.append(
                f"    {i.get('id')}. [{i.get('state', 'todo')}] {i.get('title', '')}"
                + (f" ({mins} min)" if mins else "")
                + (f", waiting on {waits}" if waits else "")
                + (f", promised to {i['committed_to']}" if i.get("committed_to") else "")
            )
    for n in board.get("news", [])[:5]:
        lines.append(f"Around the owner: {n.get('topic', '')}, {n.get('what', '')}")
    lines += [
        "",
        "`state/board.json` has all of it, including the drafts and prepared work,",
        "so read it rather than answering from these titles alone.",
    ]
    lines += extra or []
    lines += [
        "",
        "The owner's question:",
        "",
        question.strip(),
        "",
    ]
    return "\n".join(lines)


def chain_to(parent: str) -> list[dict]:
    """The exchange a follow-up is a follow-up to, and whatever it came off.

    Asked from inside an answer, the question is usually one word: "why", "is
    that in the thread". Those are whole questions there and nowhere else, so
    everything above them travels with it, oldest first.
    """
    rows = {r.get("id"): r for r in load_asks()}
    chain: list[dict] = []
    seen = set()
    at = parent
    while at and at in rows and at not in seen:
        seen.add(at)
        chain.append(rows[at])
        at = rows[at].get("parent")
    return list(reversed(chain))


# How much of a conversation travels with a follow-up. The side panel makes
# twenty-exchange conversations normal, and every earlier answer whole on every
# question is a long prompt for "and the second one?". The newest few are what
# "that" can mean; the rest are named as being there.
FOLLOW_KEEP = 6


def followed(chain: list[dict]) -> list[str]:
    if not chain:
        return []
    lines = [
        "",
        "The owner is following up on this, and the last exchange is the one they mean.",
        "Oldest first:",
    ]
    if len(chain) > FOLLOW_KEEP:
        lines.append(f"  ({len(chain) - FOLLOW_KEEP} earlier exchanges in this "
                     "conversation are left out; they are in state/asks.json.)")
        chain = chain[-FOLLOW_KEEP:]
    for r in chain:
        lines += [f"  Q: {r.get('question', '')}", f"  A: {r.get('answer', '')}"]
    return lines


ASK_NOTE_CAP = 220
EARLIER_CAP = 360


def trim_history(hist: list | None) -> list:
    """History with its note bodies capped.

    A history note now carries the full body of a draft once it was sent, so it
    is not lost when the item comes back to todo. That is the record, but it is
    also the least likely thing a live question needs, and left whole it puts a
    4KB client-language draft into every ask about the item forever. Cap it: the fact
    stays, the essay goes to board.json.
    """
    out = []
    for h in hist or []:
        note = h.get("note", "")
        if isinstance(note, str) and len(note) > ASK_NOTE_CAP:
            note = note[:ASK_NOTE_CAP] + " …(full text in board.json)"
        out.append({**h, "note": note})
    return out


PREPARED_KEEP = ("what", "conclusion", "built_at", "unanswered", "files")
PREPARED_SUMMARISE = ("table", "findings", "notes", "sources")


def slim_prepared(prep: dict | None) -> dict | None:
    """Prepared work with the bulky evidence summarised, not spelled out.

    The prepared block is the heaviest thing an item carries: one has run to 12KB
    across its table, findings, notes and sources, and a ticket ask dumps every
    open item's block whole, so one ticket alone once shipped 25KB before the
    question. At
    ticket level the specific row or source is rarely the point, and when it is
    the agent has the item number to read it. Keep the short, load-bearing keys
    (what it is, the conclusion, the honest-gaps list, the files it produced);
    replace the long evidence with a count so the shape is still visible.
    """
    if not prep:
        return prep
    slim = {k: v for k, v in prep.items() if k in PREPARED_KEEP}
    for k in PREPARED_SUMMARISE:
        v = prep.get(k)
        if isinstance(v, list) and v:
            slim[k] = f"{len(v)} {k} (full text in board.json under this item id)"
        elif isinstance(v, dict) and v:
            rows = v.get("rows")
            n = len(rows) if isinstance(rows, list) else len(v)
            slim[k] = f"{n}-row {k} (full text in board.json under this item id)"
    return slim


def ask_item(item: dict, full: bool) -> dict:
    """One item shaped for the prompt: whole for the job the owner clicked, summarised
    for the others on the ticket beside it."""
    out = dict(item)
    if item.get("history"):
        out["history"] = trim_history(item["history"])
    if not full and item.get("prepared"):
        out["prepared"] = slim_prepared(item["prepared"])
    return out


def ask_context(ref: str, question: str, parent: str = "") -> str:
    """Everything the owner can see about the job, so they do not have to type it.

    This is the whole point of asking from the card rather than from a fresh
    chat: "is that true about refinement" is a complete question when the draft
    that says it is attached to it.
    """
    ticket, item = find_subject(ref)
    chain = chain_to(parent)
    if ref == "board":
        return board_context(question, followed(chain))
    lines = [
        "",
        "---",
        "",
        f"Ticket: {ticket.get('ref', '')}, {ticket.get('title_en', '')}",
        f"Asana: {ticket.get('asana_url') or 'no ticket raised yet'}",
        f"Where it stands: {ticket.get('where_it_stands', '')}",
    ]
    if ticket.get("threads"):
        lines.append("Threads on this ticket:")
        for th in ticket["threads"]:
            lines.append(
                f"  - {th.get('label', '')} ({th.get('where', '')}): {th.get('url', '')}"
            )
    if item:
        lines += [
            "",
            f"The owner is asking about item {item.get('id')}: {item.get('title', '')}",
            "",
            "The item, verbatim from the board:",
            "```json",
            json.dumps(ask_item(item, full=True), ensure_ascii=False, indent=2),
            "```",
        ]
    else:
        # Open items in full, closed ones as a line each. A finished job's draft
        # and prepared work can be most of a ticket by weight and none of it by
        # relevance, and everything sent costs the owner time waiting for the answer.
        shut = {"done", "dropped"}
        items = ticket.get("items", [])
        live_items = [ask_item(i, full=False) for i in items if i.get("state") not in shut]
        lines += [
            "",
            "The owner is asking about the ticket as a whole. Its open items "
            "(prepared findings summarised; read board.json by item id for the full text):",
            "```json",
            json.dumps(live_items, ensure_ascii=False, indent=2),
            "```",
        ]
        closed = [i for i in items if i.get("state") in shut]
        if closed:
            lines.append("")
            lines.append("Already closed on this ticket, for context only:")
            for i in closed:
                lines.append(
                    f"  {i.get('id')}. [{i.get('state')}] {i.get('title', '')}"
                )
        # The same box sits on the speaking view, where what is on the card is
        # the words rather than the work, so the words travel with the question.
        # Without this, "rewrite what I say here" arrives with nothing to rewrite.
        if ticket.get("prep"):
            board = json.loads(BOARD.read_text(encoding="utf-8"))
            script = board.get("script") or {}
            for s in board.get("sessions", []):
                if s.get("date") == script.get("for_date"):
                    lines.append(
                        f"The script on the board is for the {s.get('kind', '')} on "
                        f"{s.get('date', '')}, {s.get('title') or s.get('name', '')}."
                    )
                    break
            lines += [
                "",
                "What the owner says about it at that session, the `prep` block verbatim:",
                "```json",
                json.dumps(ticket["prep"], ensure_ascii=False, indent=2),
                "```",
            ]
    above = {r.get("id") for r in chain}
    earlier = [
        r
        for r in load_asks()
        if r.get("ref") == ref and r.get("answer") and r.get("id") not in above
    ]
    if earlier:
        # Only the gist of what was asked before. The follow-up chain below goes
        # whole, because that is the exchange the owner is replying to; these are the
        # card's other questions, and a 2KB draft answer from last week repeated
        # in every new question is weight the model reads and the owner waits for.
        lines += ["", "Already asked about this, oldest first (answers cut short):"]
        for r in earlier[-3:]:
            a = " ".join(str(r.get("answer", "")).split())
            if len(a) > EARLIER_CAP:
                a = a[:EARLIER_CAP] + " …"
            lines += [f"  Q: {r.get('question', '')}", f"  A: {a}"]
    lines += followed(chain)
    lines += ["", "The owner's question:", "", question.strip(), ""]
    return "\n".join(lines)


def run_ask(ask_id: str, ref: str, question: str, parent: str = "",
            chosen: str = "") -> None:
    """One question, answered against the board and pinned back onto the card."""
    global asks_running
    # The row already exists: the request handler wrote it as `queued` so the card
    # could find itself immediately. Keep its asked_at, which is when the owner asked
    # rather than when the queue got round to it.
    row = next((r for r in load_asks() if r.get("id") == ask_id), None) or {
        "id": ask_id,
        "ref": ref,
        "question": question.strip(),
        "asked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    if parent:
        row["parent"] = parent
    row["state"] = "running"
    save_ask(row)
    # An ask may rewrite the item it was asked about, so it gets a copy too.
    keep.keep("ask")
    prompt = settings.PROMPTS / "ask.md"
    if not prompt.exists():
        row.update(state="failed", answer="core/prompts/ask.md is missing.")
        save_ask(row)
        ask_job("failed", "core/prompts/ask.md is missing")
        return
    left, message = blockers()
    if left:
        row.update(state="failed", answer=message)
        save_ask(row)
        ask_job("needs_login", message + login_hint(left))
        return
    # No waiting on a sweep: board.save() merges, so this answer and a refresh
    # running beside it both land. See ASK_WORKERS.
    with cancel_lock:
        if ask_id in cancelled:
            row.update(state="cancelled", answer="You called this one off.")
            save_ask(row)
            return
        asks_running += 1

    which = ref.replace("item:", "job ").replace("ticket:", "")
    ask_job("running", f"Thinking about {which}")
    # The model the owner picked in the composer, when it is one this desk offers.
    model = chosen if chosen in ask_model_ids() else ask_model()
    row["model"] = model
    save_ask(row)
    kind = agent.pick()
    if kind is None:
        message = agent.blockers(None)[1]
        row.update(state="failed", answer=message)
        save_ask(row)
        ask_job("failed", message)
        with cancel_lock:
            asks_running -= 1
        return
    cmd = kind.command(model, effort=agent.effort_for("ask"), job="ask")
    log = settings.LOGS_DIR / f"ask-{datetime.now():%Y-%m-%d}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Streamed rather than collected, so the card can say what it is doing
        # rather than only that it is doing something. The answer is the final
        # message, which keeps the CLI's own chatter off the card.
        payload = settings.prompt(prompt) + ask_context(ref, question, parent)
        waits = list(RECONNECT_WAITS)
        while True:
            ran = run_stream(cmd, payload, ASK_TIMEOUT_SECS, key=ask_id, kind=kind)
            with cancel_lock:
                called_off = ask_id in cancelled
            if called_off or not waits or not connection_lost(ran):
                break
            wait = waits.pop(0)
            ask_job("running", f"The connection to the model dropped. Trying {which} "
                               f"again in {wait}s")
            for _ in range(wait):
                with cancel_lock:
                    if ask_id in cancelled:
                        break
                time.sleep(1)
        if called_off:
            # Terminated on purpose, so the non-zero exit below is not a fault
            # and must not be reported to the owner as one.
            row.update(state="cancelled", answer="You called this one off.")
            save_ask(row)
            ask_job("idle", "Cancelled")
            return
        if ran.code is None:
            if ran.stalled:
                msg = ("The agent started but produced nothing, so its session has "
                       "most likely lapsed. Press Log in to re-authenticate, then "
                       "press Send to try again.")
            else:
                msg = "The question ran past seven minutes and was stopped."
            row.update(state="failed", answer=msg)
            save_ask(row)
            ask_job("failed", msg)
            return
        answer, took = ran.text, ran.took
        # Logged with the model, the wait and the number of steps, so "this is
        # slow" is a number that can be compared rather than a feeling.
        with log.open("a", encoding="utf-8") as fh:
            fh.write(
                f"\n=== {datetime.now():%H:%M:%S} {ref} on {kind.name}/{model}, "
                f"{took}s, {ran.calls} tool calls ===\n{question}\n\n{answer}\n"
            )
        if connection_lost(ran):
            row.update(state="failed", answer=CONNECTION_NOTE)
            save_ask(row)
            ask_job("failed", "could not reach the model; check Zscaler, then press Send")
            return
        if ran.code != 0 or not answer:
            # The CLI's own last words, when it said any, are the reason: "exited
            # 1" alone hid a fallback refusing the model id for a whole afternoon.
            why = ran.noise.strip().splitlines()[-1][:300] if ran.noise.strip() else ""
            row.update(
                state="failed",
                answer=answer or (f"{kind.name} exited {ran.code} without answering"
                                  + (f": {why}" if why else ".")),
            )
            save_ask(row)
            ask_job("failed", f"the question came back empty. See {log.name}")
            return
        row.update(state="answered", answer=answer, took_secs=took, model=model)
        save_ask(row)
        ask_job("done", f"Answered in {took}s")
    except (OSError, ValueError) as exc:
        # ValueError covers a JSONDecodeError from reading the board mid-write:
        # without it the exception escapes the thread and the card spins on
        # "running" for good.
        row.update(state="failed", answer=f"Could not run the question: {exc}")
        save_ask(row)
        ask_job("failed", f"could not run the question: {exc}")
    finally:
        with cancel_lock:
            asks_running -= 1
        clear_live(ask_id)


def agent_model(kind: str) -> str:
    """Which model a refresh or a prep runs on.

    Opus 5.5 by default, the same as everything else here: a sweep decides where
    every item stands and which drafts the threads have overtaken, and being
    wrong about that is expensive in a way a cheaper model does not repay.
    `config.json` names one per kind, `DANDORI_MODEL` overrides either for a single
    run, and `DANDORI_REFRESH_MODEL` or `DANDORI_PREP_MODEL` does it for one of them.
    """
    return agent.model_for(kind)


def run_agent(kind: str) -> None:
    """One agent pass over a prompt, then the page reloads itself."""
    prompt_name, running_note = JOBS[kind]
    # A patch left by a run that died before applying it would block this run's
    # Write (the tool will not overwrite a file it has not read) and could be
    # applied by mistake. It is unapplied work from a dead run, so it goes.
    for tmp_dir in (settings.TMP_DIR, ROOT / "tmp"):
        for stale in tmp_dir.glob("*.json"):
            stale.unlink(missing_ok=True)
    prompt = settings.resolve_prompt(prompt_name)
    if not prompt.exists():
        set_job("failed", f"{prompt_name} is missing")
        return
    runner = agent.pick()
    if runner is None:
        set_job("failed", agent.blockers(None)[1])
        return
    if lock_held():
        set_job("failed", "a refresh or prep is already running. Let it finish.")
        return

    left, message = blockers()
    if left and set(left) <= set(agent.NEEDED_MCPS) | {"tools"}:
        # A lapsed grant is the one blocker this can walk the owner through, so it
        # does, rather than stopping on a message telling them to press Log in.
        # start_login shows each browser link on the page and waits for it.
        set_job("signing_in", "Reconnecting before the run")
        start_login()
        left, message = blockers()
    if left:
        set_job("needs_login", message + login_hint(left))
        return

    set_job("running", running_note)
    # Which button is running, so the progress line can say "Refresh" or
    # "Prep" as a title rather than repeating the long running note.
    with job_lock:
        job["job"] = kind
    keep.keep(kind)
    log = settings.LOGS_DIR / f"{kind}-{datetime.now():%Y-%m-%d}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    model = agent_model(kind)
    cmd = runner.command(model, effort=agent.effort_for(kind), job=kind)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(f"{os.getpid()} desk-server {kind} {datetime.now():%H:%M}\n")

    timeout = PREP_TIMEOUT_SECS if kind == "prep" else TIMEOUT_SECS
    waits = list(RECONNECT_WAITS)
    try:
        for attempt in (1, 2, 3):
            with log.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"\n=== {datetime.now():%H:%M:%S} {kind} on "
                    f"{runner.name}/{model}/{agent.effort_for(kind) or 'default'} ===\n"
                )
                fh.flush()
                ran = run_stream(cmd, settings.prompt(prompt), timeout, fh,
                                 kind=runner)
                fh.write(
                    f"\n{ran.text}\n"
                    f"=== {kind} ended after {ran.took}s, {ran.calls} tool calls, "
                    f"exit {ran.code}{', stalled' if ran.stalled else ''} ===\n"
                )
            if ran.code is None:
                if ran.stalled and ran.calls:
                    # It was working and then stopped answering, which is a
                    # hung request or tool, not a lapsed login: pointing the owner
                    # at setup-mcp.sh then sent them to check servers that were
                    # all connected.
                    set_job("failed",
                            f"the {kind} stopped answering after {ran.calls} steps "
                            f"and was stopped. It may have written part of the "
                            f"board, so read {log.name} before pressing it again.")
                elif ran.stalled:
                    set_job("failed",
                            f"the {kind} went quiet and was stopped, so the agent "
                            "session has most likely lapsed or a connection is down. "
                            f"Check ./setup-mcp.sh --check, then try again. "
                            f"See {log.name}")
                else:
                    set_job("failed", f"the {kind} ran past {timeout // 60} minutes and was stopped")
                return
            # A clean exit is not the same as work done. A run whose connection
            # drops can still exit nought having read nothing, and reporting that
            # as "Refreshed" is the worst of the outcomes: the owner walks into the room
            # on a board that nobody swept.
            # The run found a connection dead mid-sweep and stopped, as
            # prompts/refresh.md tells it to. Its grants were fine at preflight,
            # so this is a lapse since: start the reconnect rather than calling
            # a one-line stop "Refreshed".
            if ran.code == 0 and AUTH_STOP.search(ran.text) and len(ran.text) < 400:
                set_job("signing_in", "A connection dropped mid-run. Reconnecting")
                start_login()
                if not blockers()[0]:
                    set_job("failed", f"{ran.text.strip()} Reconnected: press "
                                      f"{'Refresh' if kind == 'refresh' else 'Write prep'} again.")
                return
            if ran.code == 0 and ran.text:
                if not BOARD.exists():
                    set_job("failed", f"the {kind} finished without writing {settings.client_rel(BOARD)}. "
                                      f"Check {settings.client_rel(log)} or press it again.")
                    return
                # The run writes the board and nothing else; desk.md, which a
                # chat reads, is redrawn here so a run never spends a turn on it.
                subprocess.run([sys.executable, str(settings.CORE / "tick.py"), "--rebuild"],
                               cwd=ROOT, capture_output=True, timeout=60)
                set_job("done", "Refreshed" if kind == "refresh" else "Script written")
                return
            # Worth running again only when it cannot have changed anything. No
            # tool call means no edit, so nothing can be applied twice; a sweep
            # that stopped halfway must not be repeated, because the board would
            # take the same event on two numbers.
            if connection_lost(ran) and waits and attempt < 3:
                wait = waits.pop(0)
                set_job("running", f"the connection to the model dropped. Trying again in {wait}s")
                time.sleep(wait)
                set_job("running", running_note)
                continue
            if attempt == 1 and not ran.text and not ran.calls:
                set_job("running", f"the connection dropped before it started. {running_note}")
                continue
            if connection_lost(ran):
                set_job("failed", f"the {kind} could not reach the model, so nothing was "
                                  "read or changed. Check Zscaler shows connected, then "
                                  "press it again.")
                return
            set_job("failed", failure_note(kind, ran, log.name))
            return
    finally:
        clear_live("sweep")
        LOCK.unlink(missing_ok=True)


# How a run says it stopped on a dead connection, per "Stop rather than improvise".
AUTH_STOP = re.compile(
    r"\b(asana|slack)\b.{0,80}\b(missing|disconnected|not connected|authenticat|authori[sz]|"
    r"unavailable|no tools?)",
    re.I | re.S,
)


def failure_note(kind: str, ran: Ran, log_name: str) -> str:
    """Why it stopped, in words, rather than an exit code and a filename.

    The message is read on the page by someone about to walk into a standup, so
    it has to say whether the board was touched and whether pressing the button
    again is worth anything.
    """
    said = f"{ran.text}\n{ran.noise}"
    if not ran.text and not ran.calls:
        return (
            "the connection to the agent dropped twice before it started, so nothing "
            "was read or changed. Press Refresh again, or type refresh in a chat on "
            "this folder."
        )
    if not ran.text:
        return (
            f"the {kind} stopped after {ran.calls} steps without reporting, so the board "
            f"may be half swept. Read {log_name} before pressing it again."
        )
    if re.search(r"not logged in|unauthori[sz]ed|401", said, re.I):
        return "the agent is signed out. Press Log in, then Refresh."
    if re.search(r"rate limit|quota|429", said, re.I):
        return "the agent hit a usage limit. Wait a few minutes, then Refresh."
    last = [ln for ln in ANSI.sub("", said).splitlines() if ln.strip()]
    if last:
        return f"the {kind} stopped: {last[-1][:160]} (exit {ran.code}, {log_name})"
    return (
        f"the agent exited {ran.code} without saying anything. The board is as it was. "
        f"See {log_name}"
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # quiet: this runs behind an app icon
        pass

    def send(self, code: int, body: bytes, ctype: str) -> None:
        # The desk page is a megabyte of markup: ten tickets, both views, every
        # fold and a hundred composers, all drawn on every load because that is
        # what makes the page never stale. It compresses to about a fifth of
        # that, and gzip costs 13ms against a 9ms render, which is worth it on
        # anything but the loopback where it is free anyway. It matters most on
        # the desk command's `phone`, where the same page goes over the wifi.
        #
        # Only text, only above the size where the header costs more than the
        # saving, and only when the browser said it would take it.
        if (
            len(body) > 1400
            and ("text/" in ctype or "json" in ctype or "javascript" in ctype)
            and "gzip" in (self.headers.get("Accept-Encoding") or "")
        ):
            # Level 1, not 6. On this page 6 spends 15ms to save 233KB and 1
            # spends 5ms to save 293KB: the extra 10ms of latency buys 60KB
            # that neither loopback nor a phone on the same wifi will notice.
            packed = gzip.compress(body, 1)
            if len(packed) < len(body):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(packed)))
                self.send_header("Vary", "Accept-Encoding")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(packed)
                return
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json_out(self, code: int, payload: dict) -> None:
        self.send(code, json.dumps(payload).encode(), "application/json")

    def keyed(self) -> bool:
        return KEY in (self.path.split("?", 1)[1] if "?" in self.path else "")

    def local(self) -> bool:
        client = self.client_address[0] if self.client_address else ""
        return client in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def own_hosts(self) -> tuple[str, ...]:
        port = self.server.server_address[1]
        return (f"127.0.0.1:{port}", f"localhost:{port}")

    def host_ok(self) -> bool:
        # A loopback connection is not enough: a page on another domain that
        # rebinds its name to 127.0.0.1 arrives from loopback too, still as
        # that domain, and could read the page and the key in it. Its Host
        # header names that domain, so only this Mac's own names get through.
        return (self.headers.get("Host") or "").strip().lower() in self.own_hosts()

    def origin_ok(self) -> bool:
        # A browser sends Origin on every cross-site POST. The terminal's curl
        # sends none, so an absent Origin is not a foreign one.
        origin = (self.headers.get("Origin") or "").strip().lower()
        return not origin or origin in tuple(f"http://{h}" for h in self.own_hosts())

    def do_GET(self) -> None:
        if not self.host_ok():
            self.send(421, b"misdirected", "text/plain")
            return
        path = self.path.split("?", 1)[0]
        if path == "/health":
            # No key here: the terminal reads it from state/serve.json.
            self.json_out(
                200,
                {
                    "ok": True,
                    "version": settings.version(),
                    "version_name": settings.version_name(),
                },
            )
            return
        if path == "/api/events":
            # The earlier half of one ticket's timeline, as HTML, for the button
            # the card draws when there is more history than it carries. A GET
            # with no key: it is the same board the page is already showing, it
            # writes nothing, and keying it would mean handing the key to a
            # page that may have been opened from disk.
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            want = ""
            for bit in query.split("&"):
                if bit.startswith("ref="):
                    want = unquote(bit[4:])
            try:
                data = board.load()
            except (OSError, ValueError) as exc:
                self.json_out(500, {"error": str(exc)})
                return
            row = next(
                (t for t in data.get("tickets", []) if t.get("ref") == want), None
            )
            if row is None:
                self.json_out(404, {"error": "no such ticket"})
                return
            self.send(
                200,
                render_desk.earlier_events(row.get("events", [])).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if path == "/api/week":
            # Another week on the strip, for the arrows. Unkeyed like
            # /api/thread: it writes nothing and shows what the page shows.
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            try:
                offset = int(next(
                    (b[7:] for b in query.split("&") if b.startswith("offset=")), "0"
                ))
                data = board.load()
            except ValueError:
                self.json_out(400, {"error": "which week?"})
                return
            except OSError as exc:
                self.json_out(500, {"error": str(exc)})
                return
            self.send(
                200, render_desk.week_html(data, offset).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if path == "/api/thread":
            # One card's conversation, redrawn. When an answer lands the page
            # swaps this in where the old one was, rather than reloading and
            # putting the owner somewhere else on the board. Unkeyed for the same
            # reason as /api/events: it is what the page already shows.
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            want = next(
                (unquote(b[4:]) for b in query.split("&") if b.startswith("ref=")), ""
            )
            # The side panel draws the same record as a chat, with nothing folded.
            chat = "chat=1" in query.split("&")
            render.load_asks(ASKS)
            self.send(
                200, render.thread_html(want, chat).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if path == "/api/status":
            with job_lock:
                state = dict(job)
            runs = live_view()
            sweep = runs.pop("sweep", None)
            if sweep and state.get("kind") == "sweep":
                state.update(sweep)
                # A live sweep is running whatever the last message said, so the
                # page never offers Refresh over one it would refuse with a 409.
                if state.get("state") not in ("running", "done", "failed"):
                    state["state"] = "running"
            if state.get("state") == "idle" and (state.get("message") or "Last refreshed").startswith("Last refreshed"):
                state["message"] = last_refreshed() or state["message"]
            # Each question's own progress, so two cards thinking at once each
            # say what they are doing rather than sharing one line.
            state["live"] = runs
            state["asks"] = pending_asks()
            with health_lock:
                if health["state"] != "cold":
                    state["health"] = dict(health, rows=list(health["rows"]))
            with cal_lock:
                state["calendar"] = dict(cal)
            # What the board looks like now. The page holds the one it was drawn
            # from, so a `./tick.py <n>` in a terminal reloads the browser rather
            # than sitting there being quietly wrong.
            state["stamp"] = render.stamp()
            state["board"] = render.stamp(False)
            state["boot"] = BOOT
            state["version"] = settings.version_label()
            self.json_out(200, state)
            return
        if path == "/manifest.webmanifest":
            self.send(200, json.dumps(manifest()).encode(), "application/manifest+json")
            return
        if path.startswith("/doc/"):
            # A document an item hands the owner: a handover sheet for the client, and
            # anything else the board carries as a file rather than as prose.
            rel = unquote(path[len("/doc/"):]).lstrip("/")
            doc = None
            if rel and ".." not in Path(rel).parts:
                for base in (settings.DOCS_DIR, settings.CLIENT_SCRIPTS_DIR, settings.CLIENT_DIR):
                    if not base.exists():
                        continue
                    cand = (base / rel).resolve()
                    try:
                        if cand.is_relative_to(base.resolve()) and cand.is_file():
                            doc = cand
                            break
                    except ValueError:
                        continue
            if doc is None:
                self.send(404, b"no such document", "text/plain")
                return
            kinds = {".pdf": "application/pdf",
                     ".html": "text/html; charset=utf-8",
                     ".md": "text/markdown; charset=utf-8",
                     ".txt": "text/plain; charset=utf-8",
                     ".csv": "text/csv; charset=utf-8",
                     ".json": "application/json; charset=utf-8",
                     ".py": "text/plain; charset=utf-8"}
            ctype = kinds.get(doc.suffix.lower(), "application/octet-stream")
            self.send(200, doc.read_bytes(), ctype)
            return
        if path in ("/icon-192.png", "/icon-512.png", "/favicon.ico"):
            name = {"/favicon.ico": "icon-192.png"}.get(path, path.lstrip("/"))
            icon = settings.PACKAGING / name
            if not icon.exists():
                icon = settings.PACKAGING / "icon.png"
            if not icon.exists():
                self.send(404, b"no icon built", "text/plain")
                return
            self.send(200, icon.read_bytes(), "image/png")
            return
        if path == "/setup":
            # The first-run page, and the one Settings links to for changing an
            # answer. Drawn fresh like the desk, so it always shows the file.
            # Loopback only, even in --lan mode: its buttons install software.
            if not self.local():
                self.send(403, b"setup is only open on this Mac", "text/plain")
                return
            import render_setup

            html = render_setup.render(states=setup_page.steps())
            html = html.replace("__DESK_KEY__", KEY).replace("__DESK_BOOT__", BOOT)
            self.send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/setup/state":
            if not self.keyed():
                self.json_out(403, {"error": "bad key"})
                return
            self.json_out(200, setup_page.state())
            return
        if path in ("/", "/index.html"):
            if not BOARD.exists() or not setup_page.setup.CONFIG.exists():
                # A desk nobody has set up, or set up and never swept, has no
                # page to show. The setup page is the next thing either way.
                self.send_response(302)
                self.send_header("Location", "/setup")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                html = rebuild()
            except subprocess.CalledProcessError as exc:
                self.send(500, f"render failed: {exc}".encode(), "text/plain")
                return
            # A finished run has been collected by this load, so clear it.
            # Otherwise the page reloads, sees "done" again, and reloads forever.
            with job_lock:
                if job["state"] == "done":
                    job.update(state="idle", message=last_refreshed() or f"Last refreshed {job['at']}")
            html = html.replace("__DESK_KEY__", KEY).replace("__DESK_BOOT__", BOOT)
            self.send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        self.send(404, b"not here", "text/plain")

    def do_POST(self) -> None:
        if not self.host_ok():
            self.send(421, b"misdirected", "text/plain")
            return
        if not self.origin_ok():
            self.json_out(403, {"error": "bad origin"})
            return
        path = self.path.split("?", 1)[0]
        if not self.keyed():
            self.json_out(403, {"error": "bad key"})
            return
        if path in ("/api/refresh", "/api/prep"):
            with job_lock:
                if job["state"] == "running" and job.get("kind") == "sweep":
                    self.json_out(409, dict(job))
                    return
            if lock_held():
                self.json_out(409, {"state": "running",
                                    "message": "a refresh or prep is already running"})
                return
            if signing_in():
                self.json_out(409, {"state": "signing_in",
                                    "message": "The sign-in is still going. Finish it "
                                               "first; the buttons come back when it is done."})
                return
            kind = path.rsplit("/", 1)[1]
            set_job("running", "Starting")
            threading.Thread(
                target=guarded(lambda: run_agent(kind), kind), daemon=True
            ).start()
            self.json_out(202, {"state": "running"})
            return
        if path == "/api/ask":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self.json_out(400, {"error": "unreadable question"})
                return
            ref = str(body.get("ref", ""))
            question = str(body.get("question", "")).strip()
            if not question:
                self.json_out(400, {"error": "no question"})
                return
            ticket, _ = find_subject(ref)
            if not ticket:
                self.json_out(404, {"error": f"nothing on the board matches {ref}"})
                return
            parent = str(body.get("parent", "")).strip()
            chosen = str(body.get("model", "")).strip()
            chosen = chosen if chosen in ask_model_ids() else ""
            ask_id = f"{int(time.time())}-{secrets.token_hex(3)}"
            # Written before the reply goes back, so the card can find itself on
            # the very next poll rather than racing the worker for it.
            save_ask(
                {
                    "id": ask_id,
                    "ref": ref,
                    "question": question,
                    "asked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "state": "queued",
                    "model": chosen or ask_model(),
                }
                | ({"parent": parent} if parent else {})
            )
            ahead = ask_queue.qsize()
            ask_queue.put((ask_id, ref, question, parent, chosen))
            if not ahead:
                ask_job("running", "Starting")
            self.json_out(202, {"state": "queued", "id": ask_id, "ahead": ahead})
            return
        if path == "/api/cancel":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self.json_out(400, {"error": "unreadable"})
                return
            ask_id = str(body.get("id", "")).strip()
            if not ask_id:
                self.json_out(400, {"error": "no question named"})
                return
            self.json_out(200, {"state": "cancelled", "how": cancel_ask(ask_id)})
            return
        if path == "/api/prefs":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self.json_out(400, {"error": "unreadable"})
                return
            if not isinstance(body, dict):
                self.json_out(400, {"error": "prefs are a map"})
                return
            self.json_out(200, {"saved": save_prefs(body)})
            return
        if path == "/api/tick":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self.json_out(400, {"error": "unreadable"})
                return
            code, reply = tick_from_page(body)
            self.json_out(code, reply)
            return
        if path == "/api/act":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self.json_out(400, {"error": "unreadable"})
                return
            code, reply = act_on(body)
            self.json_out(code, reply)
            return
        if path == "/api/forget":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                ident = str(body.get("id", ""))
            except (ValueError, OSError, AttributeError):
                self.json_out(400, {"error": "unreadable"})
                return
            # Clear: one conversation by its ref, or all of them.
            if body.get("ref") or body.get("all") is True:
                self.json_out(200, {"forgot": clear_asks(str(body.get("ref") or ""))})
                return
            if not ident:
                self.json_out(400, {"error": "nothing named"})
                return
            gone = forget_ask(ident)
            if not gone:
                self.json_out(404, {"error": "no such question"})
                return
            self.json_out(200, {"forgot": gone})
            return
        if path == "/api/login":
            with job_lock:
                busy = dict(job) if (login_lock.locked() or (
                    job["state"] == "running" and job.get("kind") == "sweep")) else None
            if busy:
                self.json_out(202, busy)
                return
            set_job("signing_in", "Starting the sign-in")
            threading.Thread(
                target=guarded(start_login, "sign-in"), daemon=True
            ).start()
            self.json_out(202, {"state": "signing_in", "message": "Starting the sign-in"})
            return
        if path == "/api/check":
            # What Settings shows when the owner presses Test connections. Started in a
            # thread and collected from /api/status, because the real check
            # spends about 16 seconds starting a headless run to count its
            # tools, and holding the request open for that is how a page ends up
            # looking hung.
            threading.Thread(target=guarded(run_check, "check"), daemon=True).start()
            self.json_out(202, {"state": "checking"})
            return
        if path in ("/api/setup/run", "/api/setup/save"):
            if not self.local():
                self.json_out(403, {"error": "setup is only open on this Mac"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self.json_out(400, {"error": "unreadable"})
                return
            if not isinstance(body, dict):
                self.json_out(400, {"error": "expected a map"})
                return
            if path.endswith("/run"):
                code, reply = setup_page.run(str(body.get("action", "")))
            else:
                code, reply = setup_page.save_answers(body)
            self.json_out(code, reply)
            return
        if path == "/api/calendar":
            # Read the calendar now. Collected from /api/status like the check,
            # since a read is a headless run of about forty seconds. A read that
            # finds something new rewrites state/calendar.json and the page
            # reloads on its own.
            with cal_lock:
                busy = cal["state"] == "running"
            if not busy:
                threading.Thread(target=read_calendar, daemon=True).start()
            self.json_out(202, {"state": "running"})
            return
        self.json_out(404, {"error": "not here"})


def local_server(port: int) -> ThreadingHTTPServer:
    """A page server reachable only from this Mac."""
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DESK_PORT)
    parser.add_argument(
        "--no-claim",
        action="store_true",
        help=f"do not write state/serve.json, so `{settings.command()} open` keeps "
        "pointing at the real desk. Implied on any port but the default.",
    )
    args = parser.parse_args()

    settings.STATE_DIR.mkdir(parents=True, exist_ok=True)
    for _ in range(ASK_WORKERS):
        threading.Thread(target=ask_worker, daemon=True).start()
    # Only the real desk reads the calendar on a timer. A preview on another
    # port would spend a run an hour for a page nobody is looking at.
    if args.port == DESK_PORT or settings.env("CALENDAR_LOOP"):
        threading.Thread(target=calendar_loop, daemon=True).start()

    # Claim the port before announcing the key. `serve.json` is how the terminal
    # command and the browser find this server, so a second copy started by hand
    # while launchd already holds the desk's port used to write its key there and then die on the bind,
    # leaving every URL in the file rejected by the server that is actually up.
    # Failing here says the real thing: one is already running.
    try:
        server = local_server(args.port)
    except OSError as exc:
        print(f"port {args.port} is taken, so this one stopped: {exc}", file=sys.stderr)
        print(f"{settings.desk_name()} is already being served. Open it with: {settings.command()} open",
              file=sys.stderr)
        return 1

    # `serve.json` is how the terminal command and the Dock icon find the desk,
    # so only the desk writes it. A second copy on another port (a preview while
    # working on the page, two of them side by side) used to overwrite the file
    # and point every `open` at itself, which outlives it: the file stays behind
    # when the spare is stopped and the command then holds a key no running server accepts.
    # `--no-claim` is for a spare that should be reachable but not findable.
    if args.port == DESK_PORT and not args.no_claim:
        (settings.STATE_DIR / "serve.json").write_text(
            json.dumps({"port": args.port, "key": KEY, "started": time.time()}) + "\n",
            encoding="utf-8",
        )
    print(f"http://127.0.0.1:{args.port}/?k={KEY}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
