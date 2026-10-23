#!/usr/bin/env python3
"""The setup questions, asked once, answered into `config.json` and `me/profile.md`.

    ./setup.py            ask every question, keeping any answer already given
    ./setup.py --check    say what is missing, ask nothing

Each question shows the current answer in brackets: Enter keeps it. Nothing
here talks to Asana or Slack; the ids it asks for are explained on screen, and
`setup/INTERVIEW.md` is the longer route, a Claude chat that looks them up and
writes the profile with you.

Standard library only, like the rest of the desk. It never deletes a key it was
not asked about, so running it again after an upgrade only fills the gaps.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import settings

ROOT = settings.REPO
CONFIG = settings.CONFIG
EXAMPLE = settings.SETUP_DIR / "config.example.json"
PROFILE = settings.PROFILE
PROFILE_EXAMPLE = settings.SETUP_DIR / "profile.example.md"

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
REGIONS = {"eu": "Europe", "us": "Americas", "apac": "Asia-Pacific"}


def guess_owner() -> tuple[str, str]:
    """Guess the owner's full name and Kraken email from git or macOS account."""
    name = ""
    email = ""
    for cmd in (["git", "config", "--global", "user.name"], ["id", "-F"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
            if out and " " in out and not out.startswith("System"):
                name = out
                break
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        git_email = subprocess.run(
            ["git", "config", "--global", "user.email"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip().lower()
        if "@" in git_email and "noreply" not in git_email:
            email = git_email
    except (OSError, subprocess.SubprocessError):
        pass
    if not email and name:
        parts = [re.sub(r"[^a-z0-9]", "", p.lower()) for p in name.split()]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            email = f"{parts[0]}.{parts[-1]}@kraken.tech"
    return name, email


def load() -> dict:
    for path in (CONFIG, EXAMPLE):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if path == EXAMPLE:
            # The example's sample answers are not answers. Keep its shape only,
            # pre-filling the owner's name and email from this Mac when available.
            g_name, g_email = guess_owner()
            data["owner"] = {k: "" for k in data.get("owner", {})}
            data["owner"]["name"] = g_name
            data["owner"]["email"] = g_email
            data["owner"]["role"] = "Client Delivery Lead"
            data["client"] = {"name": "", "short": "", "domains": [], "language": "", "support_url": ""}
            data["timezone"] = ""
            data["asana"] = {"workspace_gid": "", "projects": [], "exclude_projects": []}
            data["meeting"]["name"] = ""
            data["meeting"]["also"] = []
            data["models"] = {"region": ""}
        return data
    return {}


def save(cfg: dict) -> None:
    global CONFIG, PROFILE
    short = str((cfg.get("client") or {}).get("short") or "").strip().lower()
    if short and settings.CLIENT_DIR == settings.CLIENTS / "default":
        settings.activate_client(short)
        CONFIG = settings.CONFIG
        PROFILE = settings.PROFILE
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG)


# ---------------------------------------------------------------- asking

def say(text: str = "") -> None:
    print(text)


def ask(question: str, current: str = "", help: str = "", check=None) -> str:
    if help:
        for line in help.strip().splitlines():
            say(f"   {line}")
    while True:
        hint = f" [{current}]" if current else ""
        try:
            answer = input(f"{question}{hint}: ").strip()
        except EOFError:
            say()
            raise SystemExit("Stopped. Nothing was written for this question.")
        answer = answer or current
        if check is None:
            return answer
        why = check(answer)
        if not why:
            return answer
        say(f"   {why}")


def ask_list(question: str, current: list, help: str = "") -> list:
    shown = ", ".join(str(x) for x in current)
    raw = ask(question + " (comma separated, - for none)", shown, help)
    if raw.strip() == "-":
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def yes(question: str, default: bool) -> bool:
    raw = ask(question + " (y/n)", "y" if default else "n").lower()
    return raw.startswith("y")


def need(text: str) -> str:
    return "" if text else "This one is needed."


def digits(text: str) -> str:
    return "" if not text or text.isdigit() else "Only digits: the number in the Asana URL."


def hhmm(text: str) -> str:
    return "" if re.fullmatch(r"\d{1,2}:\d{2}", text or "") else "As HH:MM, e.g. 10:30."


def tzcheck(text: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(text)
        return ""
    except Exception:
        return "A tz database name, e.g. Asia/Tokyo, Europe/London, America/New_York."


def days(text: str) -> str:
    bad = [d for d in re.split(r"[,\s/]+", text) if d and d[:3].title() not in DAYS]
    return f"Not a day: {', '.join(bad)}. Use Mon, Tue, Wed..." if bad else ""


def as_days(text: str) -> list:
    return [d[:3].title() for d in re.split(r"[,\s/]+", text) if d]


def gid_from(text: str) -> str:
    """An Asana project gid from a pasted URL or the bare number."""
    found = re.findall(r"\d{10,}", text)
    return found[0] if found else text.strip()


# ---------------------------------------------------------------- the questions

def you(cfg: dict) -> None:
    o = cfg.setdefault("owner", {})
    say("\n1. You")
    o["name"] = ask("Your full name, as Asana and Slack show it", o.get("name", ""), check=need)
    guess = o.get("email") or ".".join(o["name"].lower().split()) + "@kraken.tech"
    o["email"] = ask("Your Kraken email", guess, check=need)
    o["role"] = ask("Your role", o.get("role") or "Client Delivery Lead")
    o["workstream"] = ask(
        "Your workstream, in a word or two", o.get("workstream", ""),
        "What your tickets are about: billing, payments, onboarding, migration...")
    o["asana_user_gid"] = ask(
        "Your Asana user id (optional)", o.get("asana_user_gid", ""),
        "Open Asana, click your photo, Profile. The long number in the URL.\n"
        "Leave it blank and the first refresh looks it up.", check=digits)
    o["slack_user_id"] = ask(
        "Your Slack member id (optional)", o.get("slack_user_id", ""),
        "In Slack: your photo, Profile, the three dots, Copy member ID (starts with U).")


def client(cfg: dict) -> None:
    c = cfg.setdefault("client", {})
    say("\n2. The client")
    c["name"] = ask("The client's name", c.get("name", ""), check=need)
    initials = "".join(w[0] for w in c["name"].split() if w[:1].isalpha()).upper()[:4]
    c["short"] = ask("A short name for the page tabs", c.get("short") or initials,
                     "Two to four letters: it shows as \"<short> my work\".", check=need)
    c["domains"] = ask_list("Their email domains", c.get("domains") or [],
                            "So DANDORI can tell a client meeting from a Kraken one.")
    c["language"] = ask("The language you write to them in (en, ja, de, ...)",
                        c.get("language") or "en")
    c["support_url"] = ask(
        "Their Kraken support site account URL (optional)", c.get("support_url", ""),
        "With <account> where the account number goes, e.g.\n"
        "https://support.example-kraken.energy/accounts/<account>")


def machine_tz() -> str:
    try:
        link = Path("/etc/localtime").resolve()
        return str(link).split("zoneinfo/", 1)[1]
    except Exception:
        return "Europe/London"


def place(cfg: dict) -> None:
    say("\n3. Where and when")
    import time

    local = time.strftime("%Z")
    cfg["timezone"] = ask("Your working timezone", cfg.get("timezone") or machine_tz(),
                          f"This machine says {local}.", check=tzcheck)
    m = cfg.get("models") or {}
    cfg["models"] = m
    region = cfg["timezone"].split("/")[0]
    guess = {"Asia": "apac", "Australia": "apac", "America": "us"}.get(region, "eu")
    m["region"] = ask("Which LiteLLM region your models run in (eu, us, apac)",
                      m.get("region") or guess,
                      "The gateway lists them at hub.ai.ktl.net/models.",
                      check=lambda r: "" if r in REGIONS else "One of eu, us, apac.")


def asana(cfg: dict) -> None:
    a = cfg.setdefault("asana", {})
    say("\n4. Asana")
    a["workspace_gid"] = ask(
        "The Asana workspace id (optional)", a.get("workspace_gid", ""),
        "Blank is fine: the first refresh finds it.", check=digits)
    say("   The projects your tickets live in. Paste each project's URL or id;")
    say("   an empty line when you are done.")
    projects = list(a.get("projects") or [])
    projects = [p for p in projects if p.get("gid")]
    for p in projects:
        say(f"   have: {p.get('name') or '(no name)'} {p['gid']}")
    if projects and not yes("Keep these", True):
        projects = []
    while True:
        raw = ask("Project URL or id", "")
        if not raw:
            break
        gid = gid_from(raw)
        name = ask("  what is it called", "")
        projects.append({"gid": gid, "name": name})
    a["projects"] = projects
    a.setdefault("exclude_projects", [])
    if not projects:
        say("   No projects yet. The first refresh will search everything assigned to you.")


def rooms(cfg: dict) -> None:
    m = cfg.setdefault("meeting", {})
    say("\n5. Your main standup with the client")
    m["name"] = ask("What it is called in your calendar", m.get("name", ""), check=need)
    m["time_local"] = ask("When it starts, your time", m.get("time_local") or "10:30", check=hhmm)
    m["minutes"] = int(ask("How long, in minutes", str(m.get("minutes") or 30),
                           check=lambda t: "" if t.isdigit() else "A number."))
    m["days"] = as_days(ask("Which days", "/".join(m.get("days") or ["Mon", "Wed"]), check=days))
    m["notes_title"] = ask(
        "The title its notes are saved under, if different", m.get("notes_title", ""),
        "Notion meeting notes or the Gemini doc name. Blank means the same as the meeting.")
    also = m.get("also") or []
    say("   Other recurring meetings you speak in (a weekly review, a programme daily).")
    for x in also:
        say(f"   have: {x.get('name')} {x.get('time_local', '')} {'/'.join(x.get('days') or [])}")
    if also and not yes("Keep these", True):
        also = []
    while True:
        name = ask("Another meeting's name (empty line to stop)", "")
        if not name:
            break
        at = ask("  starts at", "13:00", check=hhmm)
        on = as_days(ask("  on which days", "Wed", check=days))
        kind = ask("  what kind: weekly, rollup, huddle or other", "weekly",
                   "weekly: a review with the client. rollup: a programme-wide daily.\n"
                   "huddle: the Kraken-side team. setup/QUESTIONS.md says more.").lower()
        room = {"name": name, "time_local": at, "days": on}
        if kind != "other":
            room["kind"] = kind
        if kind in ("weekly", "huddle"):
            room["record"] = ""
        also.append(room)
    m["also"] = also


def slack(cfg: dict) -> None:
    s = cfg.setdefault("slack", {})
    say("\n6. Slack")
    s.setdefault("search_scope", "all")
    s["channel_hints"] = ask_list(
        "Channels your tickets are discussed in", s.get("channel_hints") or [],
        "E.g. #ext-proj-client-billing. The sweep reads every thread on a ticket\n"
        "wherever it is; these are where it searches for new ones.")


def language(cfg: dict) -> None:
    lang = cfg.setdefault("language", {})
    lang["client"] = cfg.get("client", {}).get("language") or lang.get("client") or "en"
    say("\n7. Writing to the client")
    if lang["client"] == "en":
        say("   English: nothing to set here. Put any house style in me/profile.md.")
        return
    lang["level"] = ask(f"Your level in {lang['client']}", lang.get("level", ""),
                        "E.g. N2 spoken, B2. DANDORI pitches what it writes for you to read out.")
    lang["register"] = ask("The register you write in", lang.get("register", ""),
                           "E.g. ですます, plain not stiff keigo.")
    lang["readings"] = yes("Add readings over hard words, {漢字|かんじ} style", bool(lang.get("readings")))
    lang.setdefault("reading_terms", [])
    lang.setdefault("plain_runs", [])


def database(cfg: dict) -> None:
    d = cfg.setdefault("database", {})
    say("\n8. Production data (optional)")
    say("   A read-only replica MCP lets DANDORI count accounts or check a charge.")
    if not yes("Do you have one", bool(d.get("mcp"))):
        cfg["database"] = {"mcp": "", "command": "", "conn": ""}
        return
    d["mcp"] = ask("Its MCP server name", d.get("mcp", ""), check=need)
    d["command"] = ask("The command that serves it", d.get("command") or "~/.local/bin/ktdb-mcp")
    d["conn"] = ask("Its connection name (KTDB_CONN), if any", d.get("conn", ""))


def desk(cfg: dict) -> None:
    k = cfg.setdefault("desk", {})
    say("\n9. The app itself")
    k["name"] = ask("What to call the app", k.get("name") or "DANDORI")
    k["command"] = ask("The terminal command", k.get("command") or "DANDORI",
                       "`DANDORI` always works too.",
                       check=lambda c: "" if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", c) else "Letters, digits and dashes.")
    k["port"] = int(ask("The port the page runs on", str(k.get("port") or 8787),
                        check=lambda t: "" if t.isdigit() else "A number."))
    k["morning_sweep"] = ask("Morning sweep time on weekdays, if you turn it on", k.get("morning_sweep") or "08:15",
                             check=hhmm)
    for job in ("ask", "refresh", "prep"):
        cfg.setdefault(job, {"effort": "medium"})
    cfg.setdefault("post", {})
    cfg.setdefault("calendar", {"work_words": []})


def prioritization(cfg: dict) -> None:
    p = cfg.get("prioritization") or {}
    say("\n10. A recurring build prioritisation round (optional)")
    say("   Some programmes have a lead who posts the build order every couple of weeks.")
    if not yes("Is there one you raise builds into", bool(p)):
        cfg.pop("prioritization", None)
        return
    p["who"] = ask("Who runs it", p.get("who", ""), check=need)
    p["slack_user_id"] = ask("Their Slack member id", p.get("slack_user_id", ""))
    p["channel"] = ask("The channel it is posted in", p.get("channel", ""))
    p["channel_id"] = ask("That channel's id (C...)", p.get("channel_id", ""))
    p["anchor"] = ask("The date of one round, YYYY-MM-DD", p.get("anchor", ""),
                      check=lambda t: "" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t) else "As YYYY-MM-DD.")
    p["every_days"] = int(ask("Every how many days", str(p.get("every_days") or 14)))
    p["thread_at"] = ask("What time it is posted", p.get("thread_at") or "08:30", check=hhmm)
    p.setdefault("ce_projects", [])
    p.setdefault("cdl_owner_field", "")
    p.setdefault("moved", [])
    p.setdefault("holidays", [])
    cfg["prioritization"] = p


SECTIONS = (you, client, place, asana, rooms, slack, language, database, desk, prioritization)


# ---------------------------------------------------------------- the profile

def profile(cfg: dict) -> None:
    say("\n11. Your profile")
    if PROFILE.exists():
        say(f"   {PROFILE.relative_to(ROOT)} is already there. Edit it any time.")
        return
    PROFILE.parent.mkdir(exist_ok=True)
    text = PROFILE_EXAMPLE.read_text(encoding="utf-8")
    o, c, m = cfg.get("owner", {}), cfg.get("client", {}), cfg.get("meeting", {})
    filled = {
        "## Me": f"- {o.get('name')}, {o.get('role')} for {o.get('workstream') or 'my workstream'}.",
        "## The client": f"- {c.get('name')} ({c.get('short')}). Email domains: {', '.join(c.get('domains') or []) or 'not set'}.",
        "## My rooms": f"- The main standup is {m.get('name')}, {'/'.join(m.get('days') or [])} at {m.get('time_local')}.",
    }
    for heading, line in filled.items():
        text = text.replace(heading + "\n", heading + "\n\n" + line + "\n", 1)
    PROFILE.write_text(text, encoding="utf-8")
    say(f"   Wrote {PROFILE.relative_to(ROOT)} from the template, with your answers in it.")
    say("   The rest of it is prose only you can write: house style, people, lessons.")
    say("   The quickest way is to let Claude interview you: open a chat on this folder")
    say("   and say \"follow setup/INTERVIEW.md\".")


def missing(cfg: dict) -> list:
    gaps = []
    for path in ("owner.name", "owner.email", "client.name", "client.short", "timezone",
                 "meeting.name", "meeting.time_local", "meeting.days"):
        node = cfg
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if not node:
            gaps.append(path)
    if not (cfg.get("asana") or {}).get("projects"):
        gaps.append("asana.projects (optional: the first refresh can search instead)")
    if not PROFILE.exists():
        gaps.append("me/profile.md")
    return gaps


def main(argv: list) -> int:
    cfg = load()
    if "--check" in argv:
        gaps = missing(cfg) if CONFIG.exists() else ["config.json"]
        for g in gaps:
            print(f"missing: {g}")
        print("setup complete" if not gaps else f"{len(gaps)} to answer: run ./setup.py")
        return 0 if not gaps else 1
    say("Setting up DANDORI. Enter keeps the answer in brackets; Ctrl-C stops.")
    say("Your answers go in config.json and me/profile.md, which git never sees.")
    if CONFIG.exists():
        shutil.copy(CONFIG, CONFIG.with_suffix(".json.bak"))
    try:
        for section in SECTIONS:
            section(cfg)
            save(cfg)
    except KeyboardInterrupt:
        save(cfg)
        say("\nStopped. What you answered so far is saved; run ./setup.py again to carry on.")
        return 1
    cfg.setdefault("_readme", "Your answers to the setup questions. Every key is explained in setup/QUESTIONS.md.")
    save(cfg)
    profile(cfg)
    say("\nDone. Next:")
    say("  ./setup-mcp.sh     connect Asana, Slack, Google and Notion (once)")
    say("  ./install.sh       the Dock app, the terminal command, the page server")
    say("Then open the page and press Refresh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
