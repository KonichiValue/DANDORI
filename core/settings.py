#!/usr/bin/env python3
"""Whose desk this is: the one place that reads the active client workspace in `clients/`.

DANDORI separates its universal engine (`core/`) from each client workspace
(`clients/<client>/`, such as `clients/ee/`). Every CDL runs the same `core/`,
while everything specific to their client and workflow lives in `clients/<client>/`:

    clients/<client>/
        config.json     machine-readable settings (IDs, rooms, timezone, models)
        profile.md      the owner's playbook in prose (house style, people, rules)
        plugin.py       optional client plugin hooks (audit rules, account regex, etc.)
        scripts/        client-specific workflow scripts and tools
        docs/           client working documents, handovers, sheets and PDFs
        state/          board.json, asks.json, calendar.json, prefs.json, history/
        output/         rendered desk.html and desk.md
        logs/           run logs
        tmp/            scratch space for patches and data dumps

Everything in `core/` asks this module for paths and settings rather than
hard-coding them, so a missing or half-written file falls back to neutral
defaults and the setup page still draws on a fresh Mac.

    python3 core/settings.py          what the desk knows about your workspace
    python3 core/settings.py --prompt core/prompts/refresh.md
                                      the prompt exactly as a headless run gets it
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

CORE = Path(__file__).resolve().parent
REPO = CORE.parent
ROOT = REPO
CLIENTS = REPO / "clients"
PACKAGING = REPO / "packaging"
PROMPTS = CORE / "prompts"
STATIC = CORE / "static"
SETUP_DIR = CORE / "setup"
SCRIPTS = CORE / "scripts"

VERSION = "1.1.0"
VERSION_NAME = "Kinkaku"

_cache: dict = {"stamp": None, "data": {}}


def resolve_client_dir() -> Path:
    """Find the active client directory in `clients/<slug>/`."""
    override = os.environ.get("DANDORI_CLIENT", "").strip().lower()
    if override:
        return CLIENTS / override
    active = CLIENTS / "active"
    if active.is_symlink():
        try:
            target = (CLIENTS / os.readlink(active)).resolve()
            if target.parent == CLIENTS.resolve():
                return target
        except OSError:
            pass
    elif active.is_file():
        try:
            slug = active.read_text(encoding="utf-8").strip().lower()
            if slug:
                return CLIENTS / slug
        except OSError:
            pass
    found = sorted(
        p.parent
        for p in CLIENTS.glob("*/config.json")
        if p.parent.name not in ("active", "default")
    )
    if found:
        return found[0]
    if (CLIENTS / "default" / "config.json").exists():
        return CLIENTS / "default"
    if (REPO / "config.json").exists():
        return REPO
    return CLIENTS / "default"


CLIENT_DIR = resolve_client_dir()
CONFIG = CLIENT_DIR / "config.json"
PROFILE = (REPO / "me" / "profile.md") if CLIENT_DIR == REPO else (CLIENT_DIR / "profile.md")
STATE_DIR = CLIENT_DIR / "state"
OUTPUT_DIR = CLIENT_DIR / "output"
DOCS_DIR = CLIENT_DIR / "docs"
LOGS_DIR = CLIENT_DIR / "logs"
TMP_DIR = CLIENT_DIR / "tmp"
CLIENT_SCRIPTS_DIR = CLIENT_DIR / "scripts"
PLUGIN_FILE = CLIENT_DIR / "plugin.py"


def activate_client(slug: str) -> Path:
    """Point `clients/active` at `clients/<slug>` and refresh module path globals."""
    global CLIENT_DIR, CONFIG, PROFILE, STATE_DIR, OUTPUT_DIR, DOCS_DIR, LOGS_DIR, TMP_DIR, CLIENT_SCRIPTS_DIR, PLUGIN_FILE
    prev_dir = CLIENT_DIR
    clean = "".join(c for c in slug.strip().lower() if c.isalnum() or c in "-_") or "default"
    target = CLIENTS / clean
    target.mkdir(parents=True, exist_ok=True)
    if prev_dir == CLIENTS / "default" and prev_dir != target and prev_dir.exists():
        for sub in prev_dir.iterdir():
            dest = target / sub.name
            if sub.is_dir():
                shutil.copytree(sub, dest, dirs_exist_ok=True)
            elif not dest.exists():
                shutil.copy2(sub, dest)
        shutil.rmtree(prev_dir, ignore_errors=True)
    active = CLIENTS / "active"
    try:
        active.unlink(missing_ok=True)
        active.symlink_to(clean)
    except OSError:
        pass
    CLIENT_DIR = target
    CONFIG = CLIENT_DIR / "config.json"
    PROFILE = CLIENT_DIR / "profile.md"
    STATE_DIR = CLIENT_DIR / "state"
    OUTPUT_DIR = CLIENT_DIR / "output"
    DOCS_DIR = CLIENT_DIR / "docs"
    LOGS_DIR = CLIENT_DIR / "logs"
    TMP_DIR = CLIENT_DIR / "tmp"
    CLIENT_SCRIPTS_DIR = CLIENT_DIR / "scripts"
    PLUGIN_FILE = CLIENT_DIR / "plugin.py"
    _cache.update(stamp=None, data={})
    for mod_name, attrs in (
        ("board", {"BOARD": STATE_DIR / "board.json", "LOCKFILE": STATE_DIR / "board.lock"}),
        ("serve", {"BOARD": STATE_DIR / "board.json", "ASKS": STATE_DIR / "asks.json", "LOCK": STATE_DIR / "refresh.lock"}),
        ("setup", {"CONFIG": CONFIG, "PROFILE": PROFILE}),
        ("setup_page", {"BOARD": STATE_DIR / "board.json", "LOG": LOGS_DIR / "setup.log"}),
        ("keep", {"BOARD": STATE_DIR / "board.json", "HISTORY": STATE_DIR / "history"}),
        ("calendar_sync", {"STORE": STATE_DIR / "calendar.json", "SEEN": STATE_DIR / "calendar-seen.json", "LOCK": STATE_DIR / "calendar.lock"}),
        ("render", {"PREFS": STATE_DIR / "prefs.json"}),
        ("make_doc", {"DOCS": DOCS_DIR}),
        ("compact", {"LOG": LOGS_DIR / "compact.log"}),
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            for attr, val in attrs.items():
                setattr(mod, attr, val)
    return target


def client_rel(path: Path) -> str:
    """A path relative to the repo root, for readable messages and prompts."""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def conf() -> dict:
    """config.json as a dict, re-read only when the file changes.

    The server lives for weeks and the setup is edited while it runs, so a
    module-level read at import would serve last month's answers.
    """
    try:
        st = CONFIG.stat()
        stamp = (str(CONFIG), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _cache["stamp"] != stamp:
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _cache.update(stamp=stamp, data=data if isinstance(data, dict) else {})
    return _cache["data"]


def section(name: str) -> dict:
    value = conf().get(name)
    return value if isinstance(value, dict) else {}


def env(name: str, default: str = "") -> str:
    """An override from the environment: DANDORI_<name>."""
    return os.environ.get(f"DANDORI_{name}") or default


# ---------------------------------------------------------------- who and whom

def owner_name() -> str:
    return str(section("owner").get("name") or "")


def first_name() -> str:
    return owner_name().split(" ")[0] if owner_name() else ""


def owner_email() -> str:
    return str(section("owner").get("email") or "").lower()


def workstream() -> str:
    return str(section("owner").get("workstream") or "")


def owner_aliases() -> set[str]:
    """Every way a board string can name the owner: "you", "me", the full name, the first."""
    names = {"you", "me"}
    if owner_name():
        names |= {owner_name().lower(), first_name().lower()}
    return names


def client_name() -> str:
    return str(section("client").get("name") or "the client")


def client_short() -> str:
    """The short name the page puts on its tabs: "EE my work", "EE what I say"."""
    c = section("client")
    return str(c.get("short") or c.get("name") or "Client")


def client_code() -> str:
    """The lowercase folder slug for the client (`ee`, `acme`)."""
    if CLIENT_DIR != REPO and CLIENT_DIR.name != "default":
        return CLIENT_DIR.name
    return client_short().lower()


def client_domains() -> tuple[str, ...]:
    return tuple(str(d).lower().lstrip("@") for d in section("client").get("domains") or [] if d)


def is_client(text: str) -> bool:
    """Whether a holder or a speaker string names the client, not Kraken."""
    t = (text or "").strip().lower()
    names = {client_short().lower(), client_name().lower(), "client"}
    return any(t == n or t.startswith(n + " ") or t.startswith(n + ",") for n in names if n)


def desk_name() -> str:
    return str(section("desk").get("name") or "DANDORI")


def version() -> str:
    return VERSION


def version_name() -> str:
    return VERSION_NAME


def version_label() -> str:
    """The release number and its name: `1.1.0 (Kinkaku)`."""
    return f"{VERSION} ({VERSION_NAME})" if VERSION_NAME else VERSION


def command() -> str:
    """What the owner types in a terminal: `DANDORI 4`, or whatever they chose."""
    return str(section("desk").get("command") or "DANDORI")


def port() -> int:
    try:
        return int(os.environ.get("DANDORI_PORT")
                   or section("desk").get("port") or 8787)
    except ValueError:
        return 8787


# ---------------------------------------------------------------- time

def tz_name() -> str:
    return str(conf().get("timezone") or section("meeting").get("timezone") or "")


def tz() -> tzinfo:
    """The owner's working timezone, or this machine's when none is set."""
    name = tz_name()
    if name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def now() -> datetime:
    return datetime.now(tz())


def offset(day: date | None = None) -> str:
    """The UTC offset on a day as +09:00, for a time written into a query."""
    day = day or now().date()
    stamp = datetime(day.year, day.month, day.day, 12, tzinfo=tz()).strftime("%z")
    return f"{stamp[:3]}:{stamp[3:]}" if stamp else "+00:00"


# ---------------------------------------------------------------- the rooms

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def meeting() -> dict:
    """The main standup: name, time, length and days, with defaults filled in."""
    m = dict(section("meeting"))
    m.setdefault("name", "Standup")
    m.setdefault("time_local", "10:30")
    m.setdefault("minutes", 30)
    m["days"] = [d for d in m.get("days") or ["Mon", "Wed"] if d in DAYS]
    return m


def meeting_ends() -> tuple[int, int]:
    """When the main standup finishes, as (hour, minute): the fold-in starts then."""
    m = meeting()
    try:
        h, mi = (int(x) for x in str(m["time_local"]).split(":")[:2])
    except ValueError:
        h, mi = 10, 30
    end = datetime(2000, 1, 1, h, mi) + timedelta(minutes=int(m.get("minutes") or 30))
    return end.hour, end.minute


def notes_title() -> str:
    m = meeting()
    return str(m.get("notes_title") or m.get("name") or "")


def morning_sweep() -> str:
    """When the morning refresh runs on a working day, as HH:MM, or "" for never."""
    value = section("desk").get("morning_sweep", "08:15")
    return str(value or "")


def prioritization() -> dict:
    """The optional build-order round. Empty when the owner has none: skip it all."""
    p = section("prioritization")
    return p if p.get("anchor") or p.get("channel") or p.get("who") else {}


def language() -> dict:
    lang = dict(section("language"))
    lang.setdefault("client", section("client").get("language") or "en")
    return lang


def readings() -> bool:
    """Whether client-language lines carry {word|reading} markup the page draws as ruby."""
    return bool(language().get("readings"))


def work_words() -> tuple[str, ...]:
    """Words that make a calendar meeting work rather than personal."""
    words = [workstream(), client_short(), client_name()] + list(section("calendar").get("work_words") or [])
    return tuple(w for w in (str(x).strip() for x in words) if w and w.lower() not in ("client", "the client"))


# ---------------------------------------------------------------- tools

def database_mcp() -> str:
    """The MCP server for a read-only production replica, or "" for none."""
    return str(section("database").get("mcp") or "")


REGIONS = ("eu", "us", "apac")


def _mac_tz() -> str:
    try:
        target = os.readlink("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return ""


def region() -> str:
    r = str(section("models").get("region") or "").lower()
    if r in REGIONS:
        return r
    try:
        claude_cfg = json.loads(
            (Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        model = str((claude_cfg.get("env") or {}).get("ANTHROPIC_DEFAULT_OPUS_MODEL") or "")
        for cand in REGIONS:
            if f"-{cand}-" in model:
                return cand
    except (OSError, ValueError):
        pass
    zone = tz_name() or _mac_tz()
    if zone.startswith(("Asia/", "Australia/", "Pacific/")):
        return "apac"
    if zone.startswith("America/"):
        return "us"
    return "eu"


def model_id(family: str, version: str = "") -> str:
    """A LiteLLM model id on the Kraken gateway for this owner's region."""
    tail = f"-{version}" if version else ""
    return f"claude-low-carbon-{region()}-{family}{tail}"


# ---------------------------------------------------------------- prompts

PRIVATE_KEYS = ("ask", "refresh", "prep", "post", "models", "_readme")


def context() -> str:
    """The block every headless run reads before its prompt.

    Headless runs do not load AGENTS.md (an ask drops project settings on
    purpose, and a sweep should not depend on them), so who the work is for has
    to travel with the prompt. The prompts say the mechanics; this says whose.
    """
    cfg = {k: v for k, v in conf().items() if k not in PRIVATE_KEYS}
    try:
        profile = PROFILE.read_text(encoding="utf-8").strip()
    except OSError:
        profile = ""
    c_rel = client_rel(CLIENT_DIR)
    if not cfg and not profile:
        return (
            "# Who you are working for\n\n"
            "Nobody has run `./core/setup.py` on this desk yet, so there is no "
            f"`{c_rel}/config.json` and no `{c_rel}/profile.md`. Say so in your answer "
            "and do only what needs neither.\n\n---\n\n"
        )
    parts = [
        "# Who you are working for",
        "",
        f"Active client workspace: `{c_rel}/`",
        f"- Settings: `{client_rel(CONFIG)}`",
        f"- Profile: `{client_rel(PROFILE)}`",
        f"- Board state: `{client_rel(STATE_DIR / 'board.json')}`",
        f"- Rendered desk: `{client_rel(OUTPUT_DIR / 'desk.html')}` and `{client_rel(OUTPUT_DIR / 'desk.md')}`",
        f"- Client documents: `{client_rel(DOCS_DIR)}/`",
        f"- Client scripts: `{client_rel(CLIENT_SCRIPTS_DIR)}/`",
        f"- Scratch / patches: `{client_rel(TMP_DIR)}/`",
        "- Core tools: `./core/digest.py`, `./core/apply.py`, `./core/tick.py`, `./core/check.py`, `./core/audit.py`",
        "",
        "They name the owner, the client, the rooms, the people, the house style",
        "and the standing rules. Where they and the prompt below disagree about any",
        "of those, they win. The mechanics in the prompt do not change.",
        "",
        "```json",
        json.dumps(cfg, ensure_ascii=False, indent=1),
        "```",
        "",
    ]
    if profile:
        parts += [f"## {client_rel(PROFILE)}", "", profile, ""]
    else:
        parts += [
            f"There is no `{client_rel(PROFILE)}` yet, so write plainly and ask the owner",
            "rather than guessing a house style.",
            "",
        ]
    return "\n".join(parts) + "\n---\n\n"


def resolve_prompt(path: Path | str) -> Path:
    """Resolve a prompt path against REPO or CORE."""
    p = Path(path)
    if p.is_absolute():
        return p
    if (REPO / p).exists():
        return REPO / p
    if (CORE / p).exists():
        return CORE / p
    if (PROMPTS / p.name).exists():
        return PROMPTS / p.name
    return REPO / p


def prompt(path: Path | str) -> str:
    """A prompt file as a headless run should receive it: whose, then what."""
    return context() + resolve_prompt(path).read_text(encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "--prompt":
        sys.stdout.write(prompt(argv[1]))
        return 0
    if len(argv) >= 1 and argv[0] in ("--version", "-v"):
        print(f"{desk_name()} {version_label()}")
        return 0
    if not CONFIG.exists():
        print("No client config.json yet. Run ./core/setup.py or open `/setup` to answer the setup questions.")
        return 1
    m = meeting()
    scripts = sorted(CLIENT_SCRIPTS_DIR.glob("*.py")) if CLIENT_SCRIPTS_DIR.exists() else []
    print(f"{desk_name()} {version_label()} for {owner_name() or '(no name)'}, {workstream() or 'no workstream'}"
          f" with {client_name()} ({client_short()})")
    print(f"Client workspace: {client_rel(CLIENT_DIR)}/"
          + (f" (plugin: {client_rel(PLUGIN_FILE)}, {len(scripts)} scripts)" if PLUGIN_FILE.exists() else ""))
    print(f"Timezone {tz_name() or 'this machine'}, main room {m['name']}"
          f" {m['time_local']} {'/'.join(m['days'])}")
    print(f"Terminal command `{command()}`, page on port {port()}, models {region()}")
    print(f"Database MCP: {database_mcp() or 'none'}")
    print(f"Profile: {client_rel(PROFILE) if PROFILE.exists() else 'missing, run ./core/setup.py'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
