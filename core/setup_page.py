"""The setup page's half of the server: where a new DANDORI stands, and the buttons that move it.

serve.py routes `/setup` and `/api/setup/*` here, and `render_setup.py` draws
the page. A new owner runs `./install.sh` once, the app opens on this page, and
everything after that is a click: install Claude Code, connect the model
gateway, connect Asana and Slack, answer the questions, install the app, run
the first refresh.

Two kinds of check, kept apart so the page never hangs on the slow one. The
cheap ones (is there a config, a board, an app) are read on every poll. The
expensive ones (does the credential answer, which MCP grants are live) cost
seconds and may ask 1Password for Touch ID, so they run in a thread when the
page opens or when the owner presses Check again, and the page polls for them.

Anything interactive, the Kraken harness and the profile interview, opens in
Terminal through a `.command` file: driving Terminal through AppleScript needs
an automation grant this process does not have, and `open` does not.

It never sends anything anywhere, and it writes only `config.json`,
`me/profile.md` and the launchd agents, the same files `./setup.py` writes.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import agent
import launchd
import settings
import setup

ROOT = settings.REPO
LOG = settings.LOGS_DIR / "setup.log"
BOARD = settings.STATE_DIR / "board.json"
SERVERS = ("asana", "slack", "google", "notion")

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
URL_IN = re.compile(r"https://[^\s\"'<>]+")

# Where a fresh install puts things. A server started by launchd has these on its
# PATH already; one started some other way may not, and Claude Code installs
# itself into ~/.local/bin, so a server that cannot see it would report it
# missing a second after installing it.
for extra in (str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin"):
    if extra not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + extra

probe: dict = {"state": "cold", "phase": "", "at": "", "claude": "",
               "credential": False, "mcps": {}, "kraken": False}
probe_lock = threading.Lock()

work: dict = {"state": "idle", "action": "", "said": "", "url": "", "lines": [], "at": ""}
work_lock = threading.Lock()


# ---------------------------------------------------------------- where it stands

def _probe() -> None:
    kind = agent.Claude()
    has_kraken = bool(shutil.which("kraken"))
    with probe_lock:
        probe.update(phase="claude", kraken=has_kraken)
    if not shutil.which(kind.binary):
        with probe_lock:
            probe.update(claude="", credential=False, mcps={},
                         phase="", state="done", at=datetime.now().strftime("%H:%M"))
        return
    try:
        out = subprocess.run([kind.binary, "--version"], capture_output=True,
                             text=True, timeout=30, stdin=subprocess.DEVNULL)
        ver = (out.stdout or "").strip().split("\n")[0] or "installed"
    except (OSError, subprocess.SubprocessError):
        ver = "installed"
    with probe_lock:
        probe.update(claude=ver, phase="gateway")
    # Asked fresh: the owner may have finished the harness a second ago, and
    # the memo would otherwise answer for the world before it.
    agent.Claude._credential = None
    agent.forget_readiness()
    cred = bool(kind.credential())
    with probe_lock:
        probe.update(credential=cred, phase="connect")
    try:
        mcps = kind.mcp_states()
    except (OSError, subprocess.SubprocessError):
        mcps = {}
    with probe_lock:
        probe.update(mcps=mcps, phase="", state="done",
                     at=datetime.now().strftime("%H:%M"))


def recheck() -> None:
    with probe_lock:
        if probe["state"] == "running":
            return
        probe["state"] = "running"
        probe["phase"] = "claude"

    def run() -> None:
        try:
            _probe()
        except Exception as exc:  # noqa: BLE001  (a thread that raises says nothing)
            with probe_lock:
                probe.update(phase="", state="done",
                             at=datetime.now().strftime("%H:%M"))
            note(f"The check stopped: {exc}")

    threading.Thread(target=run, daemon=True).start()


def ours(job: str) -> bool:
    """Whether the launchd agent for `job` is loaded and runs this checkout."""
    import plistlib

    if launchd.label(job) not in launchd.loaded():
        return False
    try:
        with launchd.plist_path(job).open("rb") as fh:
            return Path(plistlib.load(fh).get("WorkingDirectory", "")) == ROOT
    except (OSError, ValueError):
        return False


def app_path() -> Path:
    name = f"{settings.desk_name()}.app"
    home_app = Path.home() / "Applications" / name
    sys_app = Path("/Applications") / name
    if sys_app.exists() and not home_app.exists():
        return sys_app
    return home_app


def steps() -> list[dict]:
    """Each step's state, in the order the page draws them.

    `done` is green, `todo` is the next thing, `optional` may be skipped, and
    `checking` means the slow probe has not answered yet.
    """
    with probe_lock:
        cold = probe["state"] == "cold"
        p = dict(probe, mcps=dict(probe["mcps"]))
    if cold:
        recheck()
    phase = (p.get("phase") or "claude") if p["state"] != "done" else ""
    have_cfg = setup.CONFIG.exists()
    cfg = setup.load()
    gaps = [g for g in setup.missing(cfg) if g != "me/profile.md" and "optional" not in g] if have_cfg else []
    mcps = p["mcps"]
    needed = [n for n in agent.NEEDED_MCPS if mcps.get(n) != "connected"]
    out = []

    def add(ident: str, state: str, said: str, **extra) -> None:
        out.append({"id": ident, "state": state, "said": said, **extra})

    if phase == "claude":
        add("claude", "checking", "Looking for Claude Code")
    else:
        add("claude", "done" if p["claude"] else "todo",
            p["claude"] or "Not installed yet.")
    if phase in ("claude", "gateway"):
        add("gateway", "checking", "Asking the model gateway for a key")
    elif not p["claude"]:
        add("gateway", "todo", "Needs Claude Code first.")
    else:
        add("gateway", "done" if p["credential"] else "todo",
            "Claude Code reaches its models." if p["credential"] else
            "No key yet, or 1Password is locked.")
    if phase in ("claude", "gateway", "connect"):
        add("connect", "checking", "Asking which connections are live", mcps={})
    elif not p["claude"]:
        add("connect", "todo", "Needs Claude Code first.", mcps={})
    else:
        add("connect", "done" if not needed else "todo",
            "Asana and Slack are connected." if not needed else
            f"{agent.human([n.title() for n in needed])} still to connect.",
            mcps={n: mcps.get(n, "missing") for n in SERVERS})
    if not have_cfg:
        add("answers", "todo", "Not answered yet.")
    elif gaps:
        add("answers", "todo", f"{len(gaps)} still to answer.", gaps=gaps)
    else:
        add("answers", "done", f"Saved for {cfg['owner'].get('name')}.")
    add("profile", "done" if _profile_written() else "optional",
        "Written." if _profile_written() else
        "Recommended. The drafts read much more like you with it.")
    app = app_path()
    serving = ours("serve")
    add("app", "done" if app.exists() and serving else "todo",
        f"{app.name} is in your Applications folder." if app.exists() and serving else
        f"{app.name} is not installed yet." if not app.exists() else
        "The page is not set to start at login.")
    add("refresh", "done" if BOARD.exists() else "todo",
        "Your board is built." if BOARD.exists() else "Not run yet.")
    return out


def _profile_written() -> bool:
    """A profile with something of the owner's in it, not only the template."""
    try:
        text = setup.PROFILE.read_text(encoding="utf-8")
    except OSError:
        return False
    return "_e.g." not in text or len(text) > len(setup.PROFILE_EXAMPLE.read_text(encoding="utf-8")) + 600


def state() -> dict:
    with probe_lock:
        cold = probe["state"] == "cold"
    if cold:
        recheck()
    with work_lock:
        busy = dict(work, lines=list(work["lines"]))
    with probe_lock:
        at = probe["at"]
    return {"steps": steps(), "work": busy, "checked_at": at,
            "ready": setup.CONFIG.exists() and BOARD.exists()}


# ---------------------------------------------------------------- the buttons

def set_work(state_: str, action: str = "", said: str = "", url: str = "") -> None:
    with work_lock:
        if state_ == "running" and work.get("action") != action:
            work["lines"] = []
        work.update(state=state_, action=action, said=said, url=url,
                    at=datetime.now().strftime("%H:%M"))


def note(line: str) -> None:
    with work_lock:
        work["lines"] = (work["lines"] + [line])[-14:]
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%H:%M:%S} {line}\n")
    except OSError:
        pass


def _stream(action: str, cmd: list[str], said: str, done_said: str,
            timeout: int = 900) -> None:
    """Run one command, showing its last lines and any link it prints."""
    set_work("running", action, said)
    note(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            env=agent.Claude()._base_env(),
        )
    except OSError as exc:
        set_work("failed", action, f"Could not start: {exc}")
        return
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        for raw in proc.stdout:
            line = ANSI.sub("", raw).rstrip()
            if not line.strip():
                continue
            note(line)
            found = URL_IN.search(line)
            if found and action == "connect":
                set_work("running", action,
                         "Approve the connection in your browser, then come back here.",
                         found.group(0).rstrip(".,)"))
    finally:
        timer.cancel()
    code = proc.wait()
    if code == 0:
        set_work("done", action, done_said)
    else:
        set_work("failed", action, f"Stopped with code {code}. The lines above say why.")
    recheck()


def _terminal(action: str, title: str, body: str, said: str) -> None:
    """Open a Terminal window running `body`, for the steps that ask questions."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="DANDORI-setup-"))
    script = tmp_dir / f"{action}.command"
    done_file = tmp_dir / f"{action}.done"
    script.write_text(
        "#!/bin/zsh\n"
        f'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
        f"cd {shlex.quote(str(ROOT))}\n"
        f"print -P {shlex.quote(f'%B{title}%b')}\n"
        "print ''\n"
        f"{body}\n"
        f"touch {shlex.quote(str(done_file))}\n"
        "print ''\n"
        "print 'Finished! The setup window in your browser will update automatically.'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    try:
        subprocess.run(["open", str(script)], check=True, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        set_work("failed", action, f"Could not open Terminal: {exc}")
        return
    set_work("running", action, said)

    def watch_done() -> None:
        deadline = time.time() + 1800
        while time.time() < deadline:
            if done_file.exists():
                set_work("done", action, "Finished in Terminal. Checking your setup now.")
                recheck()
                return
            time.sleep(2)

    threading.Thread(target=watch_done, daemon=True).start()


def _detached(action: str, cmd: list[str], said: str) -> None:
    """Run something that restarts this server, so it must not be our child.

    `./install.sh` and `launchd.py on serve` both stop the page server and start
    a new one. A child writing into a pipe this process holds would die of
    SIGPIPE the moment the server went, half-way through, so it gets its own
    session and writes to the log instead. The page notices the new server by
    its boot id and reloads.
    """
    set_work("running", action, said)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%H:%M:%S} $ {' '.join(cmd)}\n")
        fh.flush()
        try:
            subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=fh,
                             stderr=subprocess.STDOUT, start_new_session=True,
                             env=dict(os.environ, DANDORI_NO_OPEN="1"))
        except OSError as exc:
            set_work("failed", action, f"Could not start: {exc}")


GATEWAY = """\
if ! command -v kraken >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    print 'Installing the Kraken command line first.'
    brew install octoenergy/tap/kraken-cli || exit 1
  else
    print 'The kraken command is not installed, and neither is Homebrew.'
    print 'Ask in #claudecode-help how to install kraken-cli, then try again.'
    exit 1
  fi
fi
kraken ext install ai-harness
kraken ai-harness setup-claude-code
print ''
print 'Checking it works:'
claude --print 'Reply with the single word: connected' || print 'No answer. Is 1Password unlocked?'"""

INTERVIEW = """\
command -v claude >/dev/null 2>&1 || { print 'Claude Code is not installed yet.'; exit 1 }
print 'Claude will look up your Asana and Slack ids and your meetings, then ask'
print 'about how you write to the client. Answer in your own words.'
print 'Type /exit when it says it is done.'
print ''
claude 'follow core/setup/INTERVIEW.md'"""


ACTIONS = {
    "recheck": "Check again",
    "install_claude": "Install Claude Code",
    "gateway": "Connect the model gateway",
    "connect": "Connect Asana, Slack, Google and Notion",
    "interview": "The profile interview",
    "install_app": "Install the app",
    "restart": "Restart the page",
}


def run(action: str) -> tuple[int, dict]:
    if action not in ACTIONS:
        return 400, {"error": f"no such step: {action}"}
    if action == "recheck":
        recheck()
        return 202, {"state": "checking"}
    with work_lock:
        if work["state"] == "running" and work["action"] in ("install_claude", "connect"):
            return 409, {"error": f"{ACTIONS[work['action']]} is still running."}
    if action == "install_claude":
        target = lambda: _stream(  # noqa: E731
            action, ["/bin/bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
            "Installing Claude Code. About a minute.", "Claude Code is installed.")
    elif action == "connect":
        target = lambda: _stream(  # noqa: E731
            action, ["/bin/zsh", str(settings.SCRIPTS / "setup-mcp.sh")],
            "Adding the connections.", "Connections done. Checking them now.")
    elif action == "gateway":
        target = lambda: _terminal(  # noqa: E731
            action, "Connecting Claude Code to Kraken's model gateway", GATEWAY,
            "Terminal is open. Answer its questions, then press Check again here.")
    elif action == "interview":
        target = lambda: _terminal(  # noqa: E731
            action, "Your DANDORI profile", INTERVIEW,
            "The interview is open in Terminal. Press Check again when it is done.")
    elif action == "install_app":
        target = lambda: _detached(  # noqa: E731
            action, ["/bin/zsh", str(ROOT / "install.sh")],
            "Installing. The page restarts itself; this window reconnects.")
    else:
        # Only this checkout's own launchd job is restarted, rewritten for the
        # new port and timezone. A server started some other way is left alone:
        # killing by pattern would also stop any other DANDORI on this Mac.
        if not ours("serve"):
            set_work("done", action, "Saved. Restart the page to use the new region, "
                                     "port or timezone: Install the app does it.")
            return 200, {"state": "done"}
        target = lambda: _detached(  # noqa: E731
            action, [shutil.which("python3") or "/usr/bin/python3",
                     str(settings.CORE / "launchd.py"), "on", "serve"],
            "Restarting the page with your new settings.")
    threading.Thread(target=target, daemon=True).start()
    return 202, {"state": "running"}


# ---------------------------------------------------------------- the answers

# Every plain field on the form: its key in config.json, how it is checked, and
# whether it is needed. The form posts them flat ("owner.name") and they are put
# back in place here, so the page and ./setup.py write the same file.
FIELDS = (
    ("owner.name", "text", True), ("owner.email", "text", True),
    ("owner.role", "text", False), ("owner.workstream", "text", False),
    ("owner.asana_user_gid", "digits", False), ("owner.slack_user_id", "text", False),
    ("client.name", "text", True), ("client.short", "text", True),
    ("client.domains", "list", False), ("client.language", "text", True),
    ("client.support_url", "text", False),
    ("timezone", "tz", True), ("models.region", "region", True),
    ("asana.workspace_gid", "digits", False),
    ("meeting.name", "text", True), ("meeting.time_local", "hhmm", True),
    ("meeting.minutes", "int", True), ("meeting.days", "days", True),
    ("meeting.notes_title", "text", False),
    ("slack.channel_hints", "list", False),
    ("desk.name", "text", True), ("desk.command", "command", True),
    ("desk.port", "int", True), ("desk.morning_sweep", "hhmm", False),
)
LANGUAGE_FIELDS = (("language.level", "text", False), ("language.register", "text", False),
                   ("language.readings", "bool", False))
DATABASE_FIELDS = (("database.mcp", "text", True), ("database.command", "text", False),
                   ("database.conn", "text", False))
ROUND_FIELDS = (
    ("prioritization.who", "text", True), ("prioritization.slack_user_id", "text", False),
    ("prioritization.channel", "text", False), ("prioritization.channel_id", "text", False),
    ("prioritization.anchor", "date", True), ("prioritization.every_days", "int", True),
    ("prioritization.thread_at", "hhmm", False),
)
# Only the port requires rebinding the listening socket. Region, database MCP
# and timezone are read dynamically on each call.
RESTART_KEYS = ("desk.port",)


def get(cfg: dict, path: str):
    node = cfg
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def put(cfg: dict, path: str, value) -> None:
    *parents, last = path.split(".")
    node = cfg
    for part in parents:
        node = node.setdefault(part, {})
    node[last] = value


def _clean(kind: str, raw):
    """(value, why it is wrong or "")."""
    if kind == "bool":
        return bool(raw), ""
    if kind in ("list", "days"):
        items = raw if isinstance(raw, list) else re.split(r"[,\n]+", str(raw or ""))
        items = [str(x).strip() for x in items if str(x).strip()]
        if kind == "days":
            items = [d[:3].title() for d in items]
            bad = setup.days(" ".join(items))
            return items, bad or ("Pick at least one day." if not items else "")
        return items, ""
    text = str(raw if raw is not None else "").strip()
    if kind == "int":
        return (int(text), "") if text.isdigit() else (text, "A whole number.")
    if not text:
        return "", ""
    why = {
        "digits": setup.digits, "hhmm": setup.hhmm, "tz": setup.tzcheck,
        "region": lambda r: "" if r in setup.REGIONS else "One of eu, us, apac.",
        "command": lambda c: "" if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", c) else
                   "Letters, digits and dashes.",
        "date": lambda d: "" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else "As YYYY-MM-DD.",
    }.get(kind, lambda _: "")(text)
    return text, why


def save_answers(form: dict) -> tuple[int, dict]:
    """Merge what the form posted into config.json, keeping every other key.

    Nothing is written while any answer is wrong, so a half-saved config never
    reaches a sweep. Returns the field errors for the page to mark.
    """
    cfg = setup.load()
    before = {k: get(cfg, k) for k in RESTART_KEYS}
    errors: dict[str, str] = {}

    def take(fields) -> None:
        for path, kind, needed in fields:
            if path not in form:
                continue
            value, why = _clean(kind, form[path])
            if not why and needed and value in ("", [], None):
                why = "This one is needed."
            if why:
                errors[path] = why
            else:
                put(cfg, path, value)

    take(FIELDS)

    projects = []
    for row in form.get("asana.projects") or []:
        gid = setup.gid_from(str(row.get("url") or ""))
        if gid:
            if not gid.isdigit():
                errors["asana.projects"] = f"No project number in {gid[:60]}."
                continue
            projects.append({"gid": gid, "name": str(row.get("name") or "").strip()})
    put(cfg, "asana.projects", projects)
    cfg["asana"].setdefault("exclude_projects", [])

    rooms = []
    for row in form.get("meeting.also") or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        at, why = _clean("hhmm", row.get("time_local") or "")
        on, why2 = _clean("days", row.get("days") or [])
        if why or why2 or not at:
            errors["meeting.also"] = f"{name}: {why or why2 or 'needs a start time.'}"
            continue
        kind = str(row.get("kind") or "weekly")
        prev = next((r for r in get(cfg, "meeting.also") or [] if r.get("name") == name), {})
        old = {k: v for k, v in prev.items() if k not in ("kind", "record")}
        room = {"name": name, "time_local": at, "days": on}
        if kind != "other":
            room["kind"] = kind
        if kind in ("weekly", "huddle"):
            room["record"] = prev.get("record", "")
        # Keys the page does not show (tab, what, record) survive a save.
        rooms.append({**old, **room})
    put(cfg, "meeting.also", rooms)

    lang = cfg.setdefault("language", {})
    lang["client"] = get(cfg, "client.language") or "en"
    if lang["client"] != "en":
        take(LANGUAGE_FIELDS)
        lang.setdefault("reading_terms", [])
        lang.setdefault("plain_runs", [])

    if form.get("database.on"):
        take(DATABASE_FIELDS)
    elif "database.on" in form:
        cfg["database"] = {"mcp": "", "command": "", "conn": ""}

    if form.get("prioritization.on"):
        take(ROUND_FIELDS)
        p = cfg["prioritization"]
        for key in ("ce_projects", "moved", "holidays"):
            p.setdefault(key, [])
        p.setdefault("cdl_owner_field", "")
    elif "prioritization.on" in form:
        cfg.pop("prioritization", None)

    if errors:
        return 400, {"errors": errors}

    cfg.setdefault("slack", {}).setdefault("search_scope", "all")
    for job in ("ask", "refresh", "prep"):
        cfg.setdefault(job, {"effort": "medium"})
    cfg.setdefault("post", {})
    cfg.setdefault("calendar", {"work_words": []})
    cfg.setdefault("_readme", "Your answers to the setup questions. "
                              "Every key is explained in setup/QUESTIONS.md.")
    if setup.CONFIG.exists():
        shutil.copy(setup.CONFIG, setup.CONFIG.with_suffix(".json.bak"))
    setup.save(cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        setup.profile(cfg)
    tz_val = str(get(cfg, "timezone") or "").strip()
    if tz_val:
        os.environ["TZ"] = tz_val
        if hasattr(time, "tzset"):
            time.tzset()
    db_mcp = str(get(cfg, "database.mcp") or "").strip()
    db_cmd = str(get(cfg, "database.command") or "").strip()
    db_conn = str(get(cfg, "database.conn") or "").strip()
    if db_mcp and db_cmd:
        cmd_path = Path(db_cmd).expanduser()
        if cmd_path.exists() and shutil.which("claude"):
            try:
                cjson = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cjson = {}
            if db_mcp not in (cjson.get("mcpServers") or {}):
                args = ["claude", "mcp", "add", "--scope", "user", db_mcp,
                        "-e", "PGSSLMODE=require", "--", str(cmd_path)]
                if db_conn:
                    args.append(db_conn)
                subprocess.run(args, capture_output=True, text=True, timeout=15)
                recheck()
    # The standup fold-in runs as the meeting ends, so it follows the answers,
    # but only for an agent this checkout already owns. Another copy of DANDORI
    # on the same Mac (a test clone, an old checkout) must never take over the
    # agents of the one that is installed; `./install.sh` is what claims them.
    if ours("post") or ours("serve"):
        try:
            launchd.on("post")
        except (OSError, subprocess.SubprocessError):
            pass
    if ours("brief"):
        try:
            launchd.on("brief")
        except (OSError, subprocess.SubprocessError):
            pass
    if ours("serve"):
        try:
            import plistlib

            with launchd.plist_path("serve").open("wb") as fh:
                plistlib.dump(launchd.build("serve"), fh)
        except OSError:
            pass
        cmd = str(get(cfg, "desk.command") or "").strip()
        if cmd and "/" not in cmd:
            bin_dir = Path.home() / ".local" / "bin"
            try:
                bin_dir.mkdir(parents=True, exist_ok=True)
                target = bin_dir / cmd
                target.unlink(missing_ok=True)
                target.symlink_to(ROOT / "bin" / "DANDORI")
            except OSError:
                pass
    restart = any(get(cfg, k) != v for k, v in before.items())
    return 200, {"saved": True, "restart": restart,
                 "port": get(cfg, "desk.port") or settings.port()}
