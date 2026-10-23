#!/usr/bin/env python3
"""Everything that can be checked without a person looking at the page.

Most of what goes wrong here is not a bug in the logic. It is a shape: a field
holding a dict where every reader expects a string, a promise chain with no
`.catch()`, a renderer that throws halfway down a card. Those are cheap to catch
and expensive to find by reloading the page and squinting.

This is the gate to run after editing anything in this repo, and it is fast
enough that there is no reason not to. It never writes.

    ./check.py            everything
    ./check.py --quiet     one line per section, for a hook

What it does not check is whether the words are right. That still needs reading.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE))

import audit  # noqa: E402
import board as B  # noqa: E402
import plugins  # noqa: E402
import settings  # noqa: E402

ROOT = settings.REPO


class Result:
    def __init__(self) -> None:
        self.sections: list[tuple[str, list[str]]] = []

    def add(self, name: str, problems: list[str]) -> None:
        self.sections.append((name, problems))

    @property
    def total(self) -> int:
        return sum(len(p) for _, p in self.sections)


EMPTY_BOARD = {"version": 1, "checked_at": "", "next_id": 1, "tickets": []}
_BOARD: dict = {}


def board() -> dict:
    """The board every check reads, loaded once.

    A fresh clone has no `state/board.json`, and a board created but never
    filled may be an empty file. Both are an empty board here, and the first
    section says so as a note: a desk nobody has swept yet is not a broken one.
    A board that holds text that will not parse is a real fault, and
    `board_shape` reports it.
    """
    if "data" not in _BOARD:
        error = ""
        try:
            text = B.BOARD.read_text(encoding="utf-8") if B.BOARD.exists() else ""
        except OSError as exc:
            text, error = "", f"state/board.json could not be read: {exc}"
        data = dict(EMPTY_BOARD, tickets=[])
        if text.strip():
            try:
                data = B.load()
            except ValueError as exc:
                error = f"state/board.json does not parse: {exc}"
        _BOARD.update(data=data, error=error, empty=not text.strip())
    return _BOARD["data"]


def setup_state() -> list[str]:
    """What is missing because this desk is new, rather than broken.

    Every check below still runs: against neutral defaults with no
    `config.json`, and against an empty board with no `state/board.json`. These
    lines only say which of those it was, so a pass on a fresh clone is not
    read as a pass on a real board.
    """
    notes = []
    if not settings.CONFIG.exists():
        notes.append("no config.json yet, so every check ran on neutral defaults. "
                     f"Open the setup page (`{settings.command()} setup`) to answer the setup questions.")
    elif not settings.conf():
        notes.append("config.json is empty or does not parse, so every check ran on neutral defaults.")
    if not settings.PROFILE.exists():
        notes.append("no me/profile.md yet, so a headless run gets no house style. The setup page starts it.")
    board()
    if _BOARD.get("empty"):
        state = "is empty" if B.BOARD.exists() else "does not exist yet"
        notes.append(f"state/board.json {state}, so the pages were rendered from an empty "
                     f"board. `{settings.command()} refresh` fills it.")
    try:
        import calendar_sync

        if not calendar_sync.STORE.exists():
            notes.append("state/calendar.json has not been read yet, so the week strip "
                         "shows no meetings. The server reads it in working hours.")
    except Exception as exc:  # the renderers report a broken import loudly
        notes.append(f"could not ask calendar_sync where the calendar is: {exc}")
    return notes


def python_parses() -> list[str]:
    """Every module still parses. The cheapest possible check, and it has caught
    a truncated edit more than once."""
    bad = []
    paths = (
        sorted(settings.CORE.glob("*.py"))
        + sorted(settings.PACKAGING.glob("*.py"))
        + (sorted(settings.CLIENT_DIR.glob("*.py")) if settings.CLIENT_DIR.exists() else [])
        + (sorted(settings.CLIENT_SCRIPTS_DIR.glob("*.py")) if settings.CLIENT_SCRIPTS_DIR.exists() else [])
    )
    for path in paths:
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            bad.append(f"{settings.client_rel(path)}:{exc.lineno}: {exc.msg}")
    return bad


def javascript_parses() -> list[str]:
    """The scripts, through a real parser.

    This is the check that could not exist while the JavaScript lived inside
    Python string literals, and it is the one that would have caught the polling
    bug that left a card spinning with its answer already written to disk.
    """
    node = shutil.which("node")
    if not node:
        return ["node is not installed, so the JavaScript went unchecked"]
    bad = []
    for path in sorted(settings.STATIC.glob("*.js")):
        done = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True, check=False
        )
        if done.returncode != 0:
            first = (done.stderr or "").strip().splitlines()
            bad.append(f"{settings.client_rel(path)}: {first[0] if first else 'failed'}")
    return bad


def chain_end(text: str, start: int) -> int:
    """Where the call chain beginning at `start` finishes.

    Walked rather than pattern-matched, because a `.then()` body is full of its
    own semicolons and parentheses, and the first version of this check stopped at
    the first `;\\n` inside a callback and reported two chains as uncaught that
    both ended in `.catch()`. A check that cries wolf is worse than no check: the
    owner stops reading it, and the real one goes past them with the rest.
    """
    depth, at, size = 0, text.index("(", start), len(text)
    quote = ""
    while at < size:
        char = text[at]
        if quote:
            if char == "\\":
                at += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                # One call closed. If another link follows, keep going.
                nxt = at + 1
                while nxt < size and text[nxt].isspace():
                    nxt += 1
                word = re.match(r"\.(then|catch|finally)\s*\(", text[nxt:])
                if word:
                    at = nxt + word.end() - 1
                    depth = 0
                    continue
                return at + 1
        at += 1
    return size


def promises_are_caught() -> list[str]:
    """A `fetch` whose chain never handles a rejection.

    One dropped request used to kill the status loop for good, and the card spun
    until the owner reloaded the page with the answer already written to disk. No parser
    sees that, and it is the single most likely thing to go wrong in this file, so
    it gets its own check.
    """
    bad = []
    for path in sorted(settings.STATIC.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\bfetch\(", text):
            chain = text[match.start() : chain_end(text, match.start())]
            if ".catch(" in chain:
                continue
            bad.append(
                f"{settings.client_rel(path)}:{text[: match.start()].count(chr(10)) + 1}: "
                f"fetch with no .catch(), so a dropped request fails silently."
            )
    return bad


def font_shorthands_hold() -> list[str]:
    """A `font:` shorthand the browser will throw away.

    `font:700 11px/1 inherit` reads as if it keeps the family, but `inherit` is
    not allowed inside the shorthand, so the whole declaration is dropped and the
    label quietly renders as 15.5px body text. Twenty-six of them did, which is
    why the milestone numbers sat off-centre. Use `var(--font)` for the family.
    """
    bad = []
    for path in sorted(settings.STATIC.glob("*.css")):
        # Comments are blanked rather than removed so line numbers still hold.
        text = re.sub(
            r"/\*.*?\*/",
            lambda m: re.sub(r"[^\n]", " ", m.group(0)),
            path.read_text(encoding="utf-8"),
            flags=re.S,
        )
        for match in re.finditer(r"(?<![\w-])font:([^;}]*)", text):
            value = match.group(1).strip()
            if value != "inherit" and re.search(r"\b(inherit|initial|unset)\b", value):
                line = text[: match.start()].count(chr(10)) + 1
                bad.append(f"{settings.client_rel(path)}:{line}: font:{value}")
    return bad


def reload_guard_holds() -> list[str]:
    """The page reloads itself when the board moves, and never over the owner's typing.

    `tests/reload.js` drives the real `static/desk.js` against a stub DOM. It is
    here rather than left as something to remember, because the failure it guards
    against is silent: a reload that eats a half-written question looks exactly
    like a page that was working.
    """
    node = shutil.which("node")
    if not node:
        return []
    done = subprocess.run(
        [node, str(ROOT / "tests" / "reload.js")],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    if done.returncode == 0:
        return []
    return [
        line.strip()
        for line in (done.stdout + done.stderr).splitlines()
        if "FAIL" in line
    ] or ["tests/reload.js failed"]


def local_server_stays_local() -> list[str]:
    """The page server cannot be opened to another device on the network."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "local_only.py")],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    if done.returncode == 0:
        return []
    return [
        line.removeprefix("FAIL: ").strip()
        for line in (done.stdout + done.stderr).splitlines()
        if line.startswith("FAIL:")
    ] or ["tests/local_only.py failed"]


def pages_render() -> list[str]:
    """Both pages come out of the real board without throwing.

    A renderer that dies on one field takes the whole page with it, so this is
    the difference between finding out here and finding out in front of the owner.
    On a fresh clone that is the empty board, which must still draw a page.
    """
    try:
        import render_desk
        import render_desk_md
    except Exception as exc:  # an import error is a real failure here
        return [f"could not import the renderers: {type(exc).__name__}: {exc}"]
    data = board()
    bad = []
    for name, fn in (("output/desk.html", render_desk.render),
                     ("output/desk.md", render_desk_md.render)):
        try:
            text = fn(data)
            # An empty board legitimately draws a short desk.md: a heading and
            # nothing to list. The floor only means something with tickets in it.
            if len(text) < 1000 and (data.get("tickets") or name.endswith(".html")):
                bad.append(f"{name} rendered only {len(text)} bytes, which is empty")
        except Exception as exc:
            bad.append(f"{name} threw {type(exc).__name__}: {exc}")
    # The setup page is the first thing a new owner sees, and it draws from
    # config.json rather than the board, so an empty one and this one both.
    try:
        import render_setup
        for cfg in ({}, None):
            text = render_setup.render(cfg)
            if "__DESK_KEY__" not in text or 'id="answers"' not in text:
                bad.append("/setup rendered without its form or its key slot")
    except Exception as exc:
        bad.append(f"/setup threw {type(exc).__name__}: {exc}")
    return bad


def week_shows_the_work() -> list[str]:
    """Every job the owner could start today is drawn on the week strip.

    The strip is what the owner plans against, and its first version read only
    `waits_on.chase_on`, which is the field for work sitting with *somebody
    else*. The owner's own jobs carry no date at all, so a calendar meant to
    answer "what am I doing this week" showed five of other people's chases and
    none of the owner's four jobs.

    So this asserts the thing that was wrong: anything the desk labels "Do now"
    has a column on the strip. It also catches the subtler version, where the
    owner's work is filed correctly but folded into "and 2 more" behind somebody
    else's chases.
    """
    try:
        import render_desk
        import render_week
        from render import item_state, next_live, when_tag
    except Exception as exc:
        return [f"could not import the renderers: {exc}"]
    data = board()
    tickets = data.get("tickets", [])
    soon = next_live(data)
    mine = []
    for ticket in tickets:
        for item in ticket.get("items", []):
            state = item_state(item)
            if state["closed"]:
                continue
            if when_tag(item, state, soon)[0] == "Do now":
                mine.append((str(item.get("id")), item.get("title", "")))
    if not mine:
        return []
    refs = {t.get("ref", ""): render_desk.anchor(t.get("ref", "")) for t in tickets}
    drawn = set(
        re.findall(r'class="wd-n">(\d+)<', render_week.render(data, tickets, refs))
    )
    return [
        f'job {ident} ("{title[:40]}") is Do now but has no place on the week '
        f"strip, so the calendar does not show work the owner could start today"
        for ident, title in mine
        if ident not in drawn
    ]


def themes_keep_the_text() -> list[str]:
    """No theme may move the text ramp, and every theme must move a surface.

    Both halves have already broken once. The first themes tinted only a wash
    behind the cards and left `--card` white, so picking one changed almost
    nothing you could see. The obvious overcorrection is a theme that recolours
    the type, which on a page read an hour before the owner speaks to the client
    is the worse bug of the two.

    So: a theme block may not set `--ink`, `--mut` or `--soft`, and must set at
    least one of `--page` or `--card`. `yoru` is the exception on the first half
    and says so, because a dark page needs its own ink to keep the contrast.
    """
    # Every stylesheet, not just base.css: the Kraken theme lives in its own
    # file because it carries the logo as data.
    css = "".join(
        p.read_text(encoding="utf-8") for p in sorted(settings.STATIC.glob("*.css"))
    )
    bad = []
    # Every declaration block belonging to each theme, gathered by name, because
    # a theme is allowed more than one (the standup view gets its own).
    blocks: dict[str, str] = {}
    for match in re.finditer(r'body\[data-theme="(\w+)"\][^{:]*\{([^}]*)\}', css):
        blocks[match.group(1)] = blocks.get(match.group(1), "") + match.group(2)
    for name, body in sorted(blocks.items()):
        if name != "yoru":
            for var in ("--ink", "--mut", "--soft"):
                if f"{var}:" in body:
                    bad.append(
                        f"theme {name} sets {var}, which changes how readable "
                        "the page is. A theme may only move surfaces."
                    )
        if "--page:" not in body and "--card:" not in body:
            bad.append(
                f"theme {name} sets neither --page nor --card, so choosing it "
                "would not visibly change anything"
            )
    seen = set(blocks)
    # The picker and the stylesheet have to agree, or a theme is offered that
    # does nothing, or exists and cannot be chosen.
    try:
        import render_dialogs

        offered = {k for k, _, _ in render_dialogs.THEMES} - {"plain"}
    except Exception as exc:
        return bad + [f"could not read the theme list: {exc}"]
    for name in sorted(offered - seen):
        bad.append(f"Settings offers {name}, but no stylesheet styles it")
    for name in sorted(seen - offered):
        bad.append(f"static/ styles {name}, but Settings never offers it")
    return bad


RUBY = re.compile(r"\{([^|{}]+)\|([^|{}]+)\}")
# A run of CJK ideographs: the characters a reading is written over.
KANJI_RUN = re.compile(r"[一-鿿]+")


def plain_runs() -> set[str]:
    """Runs of three or more ideographs the owner reads without a reading.

    From `language.plain_runs` in config.json. Kept deliberately tiny: almost
    everything that long in a client's working vocabulary is a compound that
    wants a reading, and a spurious flag only ever pushes the writer towards
    adding one, which is the direction a house style with readings already leans.
    """
    return {str(r) for r in settings.language().get("plain_runs") or [] if r}


def reading_terms() -> tuple[str, ...]:
    """Short domain terms the owner reads out and stumbles on.

    From `language.reading_terms` in config.json. The run check only catches
    three ideographs or more, so a bald two-character term slips straight
    through and reaches a spoken line with no reading, which is the exact bug
    this is meant to stop. Nothing everyday belongs in the list, so it never
    fires on common words, and a properly wrapped `{word|reading}` is stripped
    before the scan, so only a genuinely bald term is flagged.
    """
    return tuple(str(t) for t in settings.language().get("reading_terms") or [] if t)


def furigana_present() -> list[str]:
    """Every compound in a line the owner reads out has its reading over it.

    Only when the profile asks for readings (`language.readings` in
    config.json). The speaking view renders `{word|reading}` as ruby and leaves
    bald text bald on purpose (`render.furi`), so a script line written without
    the markup reaches the owner with no reading, which is exactly the word they
    stumble on out loud. The prep step drops it most reliably on the quiet
    one-line status blocks, where the model copies bald examples from the prompt.

    This flags a run of three or more un-annotated ideographs, which in a
    working vocabulary is almost always a compound that wants a reading, while
    leaving everyday two-character words alone; `plain_runs()` and
    `reading_terms()` tune both halves. It reads only the spoken fields, the
    ones that become "what I say": the script lines, the pushback answers and
    any open question for the client. Drafts are messages, not speech, and are
    left to the eye.
    """
    if not settings.readings():
        return []
    data = board()
    bad: list[str] = []
    seen: set[tuple[str, str]] = set()
    plain, terms = plain_runs(), reading_terms()

    def scan(ref: str, where: str, text: str) -> None:
        bald = RUBY.sub("", text or "")
        for run in KANJI_RUN.findall(bald):
            if len(run) >= 3 and run not in plain and (ref, run) not in seen:
                seen.add((ref, run))
                bad.append(f"{ref} ({where}): 「{run}」 has no reading")
        for term in terms:
            if term in bald and (ref, term) not in seen:
                seen.add((ref, term))
                bad.append(f"{ref} ({where}): 「{term}」 has no reading")

    for t in data.get("tickets", []):
        ref = str(t.get("ref") or t.get("id") or "?")
        prep = t.get("prep") or {}
        for block in (prep.get("script") or []):
            head = block.get("heading", "status")
            for line in block.get("lines", []):
                scan(ref, f"script {head}", line.get("ja_ruby", ""))
        for q in (prep.get("open_questions") or []):
            scan(ref, "question", q.get("ja_ruby", ""))
        for p in (prep.get("pushback") or []):
            scan(ref, "pushback", p.get("say_ja", ""))
    # The programme-wide rollup (`xws`, a historical name) is read out to the
    # same room, so it is held to the same rule as the standup script.
    for r in (data.get("xws") or {}).get("raised", []):
        ref = str(r.get("ref") or "?")
        for line in r.get("say", []):
            scan(ref, "rollup", line.get("ja_ruby", ""))
    return bad


def board_shape() -> list[str]:
    data = board()
    if _BOARD.get("error"):
        return [_BOARD["error"]]
    return B.check(data)


def board_freshness() -> list[str]:
    data = board()
    if not data.get("tickets"):
        return []
    rep = audit.Report()
    for one in audit.CHECKS + plugins.audit_checks():
        one(data, rep)
    return [line.strip() for lines in rep.groups.values() for line in lines]


def tracked_files_are_generic() -> list[str]:
    """Nothing in config.json that names a real person, client or workspace
    has leaked into a git-tracked file.

    Every CDL runs the same git-tracked core with their own local config.json
    and me/profile.md. A quick edit tested locally can easily leave a real
    client name, Asana project GID or Slack channel in a prompt or module
    before a pull request is opened. Checking git ls-files against the local
    config.json catches that before commit.
    """
    cfg = settings.conf()
    if not cfg:
        return []
    needles: list[tuple[str, str]] = []
    example_values = {
        "owner.name": {"Alex Example"},
        "owner.email": {"alex.example@kraken.tech"},
        "client.name": {"Example Energy"},
        "client.short": {"EE"},
        "client.domains": {"example-energy.co.jp"},
        "asana.project.name": {
            "The client-shared project your tickets live in"
        },
    }

    def add(label: str, val: object, min_len: int = 4) -> None:
        s = str(val or "").strip()
        if len(s) < min_len or s in example_values.get(label, set()):
            return
        needles.append((label, s))

    owner = cfg.get("owner") or {}
    add("owner.name", owner.get("name"))
    add("owner.email", owner.get("email"))
    add("owner.asana_user_gid", owner.get("asana_user_gid"))
    add("owner.slack_user_id", owner.get("slack_user_id"))

    client = cfg.get("client") or {}
    add("client.name", client.get("name"))
    add("client.short", client.get("short"), min_len=1)
    add("client.support_url", client.get("support_url"))
    for dom in client.get("domains") or []:
        add("client.domains", dom)

    asana = cfg.get("asana") or {}
    add("asana.workspace_gid", asana.get("workspace_gid"))
    for proj in (asana.get("projects") or []) + (asana.get("exclude_projects") or []):
        if isinstance(proj, dict):
            add("asana.project.gid", proj.get("gid"))
            add("asana.project.name", proj.get("name"))

    slack = cfg.get("slack") or {}
    for ch in slack.get("channel_hints") or []:
        add("slack.channel_hints", ch)
    for fc in slack.get("form_channels") or []:
        if isinstance(fc, dict):
            add("slack.form_channels.channel", fc.get("channel"))
            add("slack.form_channels.name", fc.get("name"))

    prio = cfg.get("prioritization") or {}
    add("prioritization.who", prio.get("who"))
    add("prioritization.slack_user_id", prio.get("slack_user_id"))
    add("prioritization.channel", prio.get("channel"))
    add("prioritization.channel_id", prio.get("channel_id"))
    for gid in prio.get("ce_projects") or []:
        add("prioritization.ce_projects", gid)
    add("prioritization.cdl_owner_field", prio.get("cdl_owner_field"))

    db = cfg.get("database") or {}
    add("database.mcp", db.get("mcp"))
    add("database.conn", db.get("conn"))

    if not needles:
        return []
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        return [f"git ls-files failed: {exc}"]

    # A short value such as client.short ("EE") counts only as a whole word.
    # Matched as a substring, two letters turn up by chance in every embedded
    # base64 image, and a check that always fails is one nobody reads.
    def pattern(val: str) -> re.Pattern[str]:
        if len(val) >= 4:
            return re.compile(re.escape(val))
        return re.compile(rf"(?<![A-Za-z0-9+/]){re.escape(val)}(?![A-Za-z0-9+/])")

    found = [(label, val, pattern(val)) for label, val in needles]
    bad: list[str] = []
    for rel in out:
        path = ROOT / rel
        if path.suffix.lower() in {".png", ".icns", ".ico", ".jpg", ".jpeg", ".gif", ".webp"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, val, pat in found:
            if pat.search(text):
                bad.append(f"{rel} contains {label} from config.json ({val!r})")
    return bad


CHECKS = (
    ("Whose DANDORI this is", setup_state),
    ("Python parses", python_parses),
    ("JavaScript parses", javascript_parses),
    ("Tracked files carry no personal config", tracked_files_are_generic),
    ("Every fetch handles a failure", promises_are_caught),
    ("Every font shorthand is one the browser keeps", font_shorthands_hold),
    ("The page reloads itself, but never over the owner's typing", reload_guard_holds),
    ("The page server stays on this Mac", local_server_stays_local),
    ("The board is the shape the renderers expect", board_shape),
    ("Spoken lines carry their readings", furigana_present),
    ("Every page renders", pages_render),
    ("A theme moves the paper, never the ink", themes_keep_the_text),
    ("The week shows the work the owner could start", week_shows_the_work),
    ("The board is up to date", board_freshness),
)

# The board being stale is a thing for a refresh to fix, not a broken repo, so it
# is reported and does not fail the run. A desk nobody has set up or swept yet
# is the same: a fresh clone is not a broken one.
ADVISORY = {"The board is up to date", "Whose DANDORI this is"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quiet", action="store_true", help="one line per section")
    args = ap.parse_args()

    result = Result()
    for name, fn in CHECKS:
        result.add(name, fn())
    for name, fn in plugins.board_checks():
        try:
            problems = fn(board())
        except TypeError:
            problems = fn()
        result.add(name, problems)

    failed = 0
    for name, problems in result.sections:
        mark = "ok  " if not problems else ("note" if name in ADVISORY else "FAIL")
        if problems and name not in ADVISORY:
            failed += len(problems)
        print(f"[{mark}] {name}" + (f", {len(problems)}" if problems else ""))
        if problems and not args.quiet:
            for line in problems[:12]:
                print(f"         {line}")
            if len(problems) > 12:
                print(f"         and {len(problems) - 12} more")

    print()
    if failed:
        print(f"{failed} things to fix before this is worth loading.")
        return 1
    stale = sum(len(p) for n, p in result.sections if n in ADVISORY and n != "Whose DANDORI this is")
    new = sum(len(p) for n, p in result.sections if n == "Whose DANDORI this is")
    if stale:
        print(f"Nothing broken. {stale} things are out of date, which a refresh fixes.")
    elif new:
        print(f"Nothing broken. {new} things are not set up yet, which is expected on a fresh clone.")
    else:
        print("Nothing broken, nothing out of date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
