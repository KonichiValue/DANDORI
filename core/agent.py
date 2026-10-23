#!/usr/bin/env python3
"""How Claude Code runs a job, and how to read what it says while it runs.

The buttons on the page and the verbs in `bin/DANDORI` all end up here. Keeping
the CLI details in one file lets the rest of the repo ask for "a sweep" without
having to know how Claude Code streams events or reports a missing credential.

Claude Code owns the MCP servers this desk needs: Asana and Slack through the
Kraken AI Hub, and a database replica over stdio for anyone who has one
(`config.json` `database.mcp`). If Claude Code or its credential is unavailable,
the job stops rather than falling through to a CLI without the same tool guards.

Nothing here writes to the board. It starts a process, turns its output into a
progress line and a log, and reports how it ended.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import settings

ROOT = settings.REPO

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Opus 5.5 on the Kraken gateway, in the owner's region (`config.json`
# `models.region`: eu, us or apac). Named in full rather than as `opus`, because
# the alias follows whatever the account calls newest and a sweep that silently
# changes model between one morning and the next is a difference nobody asked
# for. `config.json` overrides per job, `DANDORI_MODEL` for a single run.
def ask_models() -> tuple[tuple[str, str, str], ...]:
    """The models a question may be asked on, in the order the picker lists them,
    built for the current `models.region` in `config.json`.

    Only ones this gateway serves to everyone: Haiku 5.5 and Fable 5.1 come back
    403 ("user can only access kraken-default-models"), so they are not offered.
    """
    return (
        (settings.model_id("opus", "5-5"), "Opus 5.5", "best writing, the slowest"),
        (settings.model_id("sonnet", "5-5"), "Sonnet 5.5", "faster, nearly as good"),
        (settings.model_id("opus"), "Opus 4.8", "the long-context one"),
        (settings.model_id("sonnet"), "Sonnet 4.6", "quick and steady"),
        (settings.model_id("haiku"), "Haiku 4.5", "quickest, for a lookup"),
    )


DEFAULT_MODEL = settings.model_id("opus", "5-5")
ASK_MODELS = ask_models()

# Names an older answer may carry, so every bubble can say who wrote it.
MODEL_NAMES = {
    "claude-opus-4-8": "Opus 4.8",
    "auto": "Default model",
}


def model_label(model: str | None) -> str:
    """A model id as a person would say it: "Opus 5.5", not the gateway's id."""
    if not model:
        return ""
    for m, name, _ in ask_models():
        if model == m:
            return name
    if model in MODEL_NAMES:
        return MODEL_NAMES[model]
    m = re.search(r"(opus|sonnet|haiku|fable)[-_]?(\d)?[-_.]?(\d)?", model)
    if not m:
        return model
    ver = ".".join(x for x in m.groups()[1:] if x)
    return m.group(1).title() + (f" {ver}" if ver else "")


# The MCP servers a sweep cannot do its job without. Asana is where the tickets
# are and Slack is where the threads are; an agent missing either does not stop,
# it improvises, which is the failure this list exists to prevent. The database
# is not here: reading a replica is something some jobs do and no job requires.
NEEDED_MCPS = ("asana", "slack")

# The servers a run is allowed to call without being asked. Wider than
# NEEDED_MCPS on purpose: a sweep that reaches for the calendar to date a room,
# or the replica to see how many accounts a hold covers, should get an answer
# rather than a denial nobody is at the keyboard to clear.
DB = settings.database_mcp()
MCP_ALLOW = ("asana", "slack", "google", "notion") + ((DB,) if DB else ())


def _mcp_allow() -> tuple[str, ...]:
    db = settings.database_mcp()
    return ("asana", "slack", "google", "notion") + ((db,) if db else ())

# The built-ins a sweep uses: read the repo, write the board, run ./tick.py.
# They are only listed because naming an MCP tool turns the allow list
# exhaustive, and then a tool nobody named is a tool nobody has. Task is absent
# on purpose: prompts/refresh.md forbids handing a sweep to a subagent, because
# the subagent comes back with a summary and the board loses the detail.
BUILTIN_TOOLS = (
    "Bash", "Read", "Write", "Edit", "Glob", "Grep", "TodoWrite", "NotebookEdit",
)

# What each job is actually given, which is less than what it is allowed. An
# allow rule grants a tool without removing anything else, and on 1 Oct a run
# that only answered "hi" was 192k tokens of context on every turn: Notion's
# tool definitions alone were 80k, Asana 39k, Slack 20k, Google 15k. None of
# that is the question. `--disallowedTools` is what takes a definition out of
# the context, and it takes mid-name wildcards, so a family goes in one line.
#
# Every job loses the tools that write or send outside this folder. Nothing on
# this desk ever sends, and a tool that is not there cannot be called by
# mistake, which is a stronger guarantee than a sentence in a prompt.
NEVER = (
    "mcp__google__gmail_*", "mcp__google__sheets_*", "mcp__google__slides_*",
    "mcp__google__calendar_create*", "mcp__google__calendar_delete*",
    "mcp__google__calendar_update*", "mcp__google__calendar_respond*",
    "mcp__google__calendar_suggest*", "mcp__google__drive_copy*",
    "mcp__google__drive_create*",
    "mcp__asana__add_*", "mcp__asana__create*", "mcp__asana__update*",
    "mcp__asana__delete*", "mcp__asana__*portfolio*", "mcp__asana__*agent*",
    "mcp__asana__*preview*", "mcp__asana__get_teams", "mcp__asana__get_workspace*",
    "mcp__asana__get_status_overview",
    "mcp__slack__slack_send*", "mcp__slack__slack_schedule*",
    "mcp__slack__slack_create*", "mcp__slack__slack_update*",
    "mcp__slack__slack_add*",
    # Notion is the heaviest server and a sweep uses four of its tools: fetch,
    # search, the meeting notes and the user list. Everything else is writing,
    # Notion AI sessions or skills.
    "mcp__notion__notion-create*", "mcp__notion__notion-update*",
    "mcp__notion__notion-move*", "mcp__notion__notion-duplicate*",
    "mcp__notion__notion-upload*", "mcp__notion__notion-download*",
    "mcp__notion__notion-convert*", "mcp__notion__*session*",
    "mcp__notion__*skill*", "mcp__notion__*agents*", "mcp__notion__*next-steps*",
    "mcp__notion__notion-get-async*", "mcp__notion__notion-list-*",
    "mcp__notion__notion-get-teams", "mcp__notion__notion-get-comments",
    "mcp__notion__notion-query-data-sources",
    "mcp__notion__notion-query-multiple*", "mcp__notion__notion-ai-search",
    # ktdb puts production rows in the model's context. These only catch a
    # command that starts with the name; guard.py refuses the rest.
    "Bash(ktdb:*)", "Bash(uvx:*)", "Bash(uv:*)",
) + ((f"mcp__{DB}__analyze*", f"mcp__{DB}__get_top*", f"mcp__{DB}__explain*") if DB else ())

# An ask reads one ticket's threads and sometimes the replica. It has never once
# opened the calendar, Drive or Notion, and those two servers are 94k tokens of
# a context the question is a few thousand of.
ASK_NEVER = ("mcp__google__*", "mcp__notion__*")

# The detours the 7 Oct sessions took, made impossible rather than discouraged.
# A sweep that reads state/board.json whole is 300KB of context it re-reads on
# every turn after; one that writes it skips the lock and the merge; `git` and
# `python3 -c` were how runs went looking for a field name the digest already
# gave them. The board is read with ./digest.py and written with ./apply.py.
STRUCTURAL_NEVER = (
    "Read(state/board.json)", "Read(./state/board.json)",
    "Read(clients/*/state/board.json)", "Read(./clients/*/state/board.json)",
    "Write(state/**)", "Edit(state/**)", "Write(./state/**)", "Edit(./state/**)",
    "Write(clients/*/state/**)", "Edit(clients/*/state/**)",
    "Write(./clients/*/state/**)", "Edit(./clients/*/state/**)",
    "Edit(*.py)", "Write(*.py)", "Edit(core/**)", "Write(core/**)",
    "Edit(static/**)", "Write(static/**)", "Edit(core/static/**)", "Write(core/static/**)",
    "Bash(git:*)", "Bash(python3 -c:*)", "Bash(cat state/board.json:*)",
    "Bash(cat clients/*/state/board.json:*)",
    # desk.md is the board again, as prose: the 7 Oct 15:12 sweep read it five
    # times for a record `./core/digest.py --item` prints in one call.
    "Read(output/**)", "Read(./output/**)",
    "Read(clients/*/output/**)", "Read(./clients/*/output/**)",
)
# Prep does the refresh half from its own short brief and never needs the 40KB
# refresh prompt; reading it was 12k tokens on every turn of every prep.
PREP_NEVER = (
    "Read(prompts/refresh.md)", "Read(./prompts/refresh.md)",
    "Read(core/prompts/refresh.md)", "Read(./core/prompts/refresh.md)",
)

# The built-ins a run is given at all, as distinct from the ones it may use
# without asking. The default set carries Cron, worktrees, Workflow, WebFetch
# and a dozen others nobody headless has a use for, at about 10k tokens a turn.
# ToolSearch stays, because it is how a run loads a tool the CLI chose to defer.
GIVEN_BUILTINS = ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "ToolSearch")


class Kind:
    """One agent CLI, and the vocabulary it happens to use."""

    name = ""
    binary = ""

    def available(self) -> bool:
        return subprocess.run(
            ["which", self.binary], capture_output=True, text=True
        ).returncode == 0


class Claude(Kind):
    """Claude Code, headless. The one with the MCP servers this desk needs."""

    name = "claude"
    binary = "claude"

    def command(self, model: str, prompt_is_stdin: bool = False,
                effort: str = "", job: str = "") -> list[str]:
        # The run is unattended, so a permission prompt is a hang nobody is
        # watching, and `--add-dir` is what keeps the blast radius to this folder.
        #
        # `acceptEdits` rather than `bypassPermissions`, which is the opposite of
        # what it sounds like. On this machine bypassPermissions is refused and
        # quietly downgraded, so a sweep under it could not write a single file:
        # the one that ran on 24 Sep read every ticket and then lost all nine
        # findings. acceptEdits grants the file tools outright, and it is measured
        # rather than assumed, so re-measure before changing it.
        cmd = [
            self.binary,
            "--print",
            "--permission-mode", "acceptEdits",
            "--add-dir", str(ROOT),
        ]
        # No permission mode carries the MCP servers, which cost a day to learn:
        # every Asana call came back "you haven't granted it yet" under both
        # bypassPermissions and --dangerously-skip-permissions. An allow rule is
        # what settles it, and the anchored `mcp__<server>__*` form is the only one
        # that works. A bare `mcp__*` is skipped with a warning, and the
        # server-only `mcp__asana` clears the denial without granting the tool,
        # which reads like success until nothing comes back.
        #
        # Naming any tool makes the list exhaustive, so the built-ins have to be
        # named back in beside them. Leaving them out is what broke that same
        # sweep: Bash survived on remembered rules in settings.local.json, Write
        # had none, and `./tick.py` never ran.
        for tool in (*(f"mcp__{s}__*" for s in _mcp_allow()), *BUILTIN_TOOLS):
            cmd += ["--allowedTools", tool]
        cmd += self.profile(job)
        cmd += ["--settings", json.dumps(self.settings(job))]
        if model != "auto":
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        return cmd

    @staticmethod
    def profile(job: str) -> list[str]:
        """The tools a job is handed, as flags. See NEVER above for the why.

        A sweep keeps TodoWrite, which it uses to pace forty tickets. An ask
        also drops the project settings, which is this repo's CLAUDE.md, 6k
        tokens of instructions for a chat that prompts/ask.md already restates
        for the one card it is about.
        """
        given = GIVEN_BUILTINS + (("TodoWrite",) if job != "ask" else ())
        flags = ["--tools", ",".join(given)]
        db = settings.database_mcp()
        db_never = (f"mcp__{db}__analyze*", f"mcp__{db}__get_top*", f"mcp__{db}__explain*") if db else ()
        for tool in dict.fromkeys(NEVER + db_never + (ASK_NEVER if job == "ask" else ())):
            flags += ["--disallowedTools", tool]
        if job in ("refresh", "prep"):
            for tool in STRUCTURAL_NEVER + (PREP_NEVER if job == "prep" else ()):
                flags += ["--disallowedTools", tool]
        if job == "ask":
            flags += ["--setting-sources", "user"]
        return flags

    @staticmethod
    def settings(job: str) -> dict:
        """Settings layered over the user's for this one run.

        `apiKeyHelper` is what stopped the prompts that kept coming back. The
        user's helper shells out to 1Password, and Claude Code re-runs it every
        few minutes, so a long sweep asked for Touch ID again and again and sat
        still until somebody answered: the 195s and 331s gaps in the 7 Oct
        sessions. The key is already resolved once and pinned in `env()`, so
        the helper this run sees just reads it back, and the vault is asked
        exactly once, before the run starts.

        The PreToolUse hook is `guard.py`, on every job, which refuses any shell
        command that mentions ktdb. The PostToolUse hook is `compact.py`, which
        shrinks every Asana, Slack and Notion answer before the model reads it.
        An ask reads one card's threads and keeps them whole.
        """
        conf: dict = {"apiKeyHelper": "printenv ANTHROPIC_API_KEY"}
        conf["hooks"] = {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command",
                       "command": f"python3 {settings.CORE / 'guard.py'}",
                       "timeout": 10}],
        }]}
        if job in ("refresh", "prep", "post"):
            conf["hooks"]["PostToolUse"] = [{
                "matcher": "mcp__.*",
                "hooks": [{"type": "command",
                           "command": f"python3 {settings.CORE / 'compact.py'}",
                           "timeout": 20}],
            }]
        return conf

    # stream-json refuses to run without --verbose, and the pair is what makes a
    # tool call visible the moment it starts rather than at the end.
    #
    # --include-partial-messages is what keeps the stall watchdog honest. Without
    # it the CLI says nothing at all while the model writes a turn, so a long
    # board-writing script on a 300k-token context is two and a half minutes of
    # silence that looks exactly like a dead session. That is how the 30 Sep prep
    # was killed: 43 tool calls in, every server connected, stopped mid-sentence.
    # With it, every chunk of text and tool input is a line, and a line is a beat.
    stream = ["--output-format", "stream-json", "--verbose",
              "--include-partial-messages"]

    def env(self) -> dict[str, str]:
        """The environment a headless run needs, with the credential pinned in.

        `_base_env()` does the stripping. This adds the one thing that keeps a
        long sweep alive: the gateway key, resolved once and passed in as
        `ANTHROPIC_API_KEY`.

        Claude Code re-runs `apiKeyHelper` periodically rather than once at
        startup, so a sweep that began with an unlocked vault still dies when
        1Password idle-locks ten minutes later. That is exactly how the 28 Sep
        prep was lost: Asana and Slack had already answered, and the run was
        part-way through writing the board. Handing the key over in the
        environment means the helper is never needed again mid-run, so the vault
        can lock without taking the sweep with it.
        """
        env = self._base_env()
        key = self.credential()
        if key:
            env["ANTHROPIC_API_KEY"] = key
        return env

    def _base_env(self) -> dict[str, str]:
        """The run's environment with the attending host's variables stripped.

        When this server is started from inside a Claude Code session, that
        session exports variables saying an app is managing the provider and
        holding the credentials. A child `claude` that reads them looks for an
        auth handshake that is not there and reports itself signed out. Dropping
        them sends it to `~/.claude/settings.json` and the gateway, which is
        where its real credentials are.
        """
        env = dict(os.environ)
        for var in (
            "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
            "CLAUDE_CODE_HOST_AUTH_ENV_VAR",
            "CLAUDE_CODE_HOST_SESSION_ID",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_ENTRYPOINT",
            "CLAUDE_CODE_MESSAGING_SOCKET",
            "CLAUDE_CODE_MESSAGING_TOKEN",
            "CLAUDE_AGENT_SDK_VERSION",
            "CLAUDECODE",
            "CLAUDE_CODE_SSE_PORT",
            "ANTHROPIC_AUTH_TOKEN",
            # A child of an attended session is told so, and then routes its
            # permission decisions back to a parent that is not listening for
            # them. Every MCP call comes back "you haven't granted it yet".
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_CODE_SESSION_ATTENDED",
            "CLAUDE_CODE_EXECPATH",
            "CLAUDE_CODE_DIAGNOSTICS_FILE",
            "CLAUDE_PID",
            "CLAUDE_EFFORT",
        ):
            env.pop(var, None)
        env.update(self.mcp_env)
        pypath = str(settings.CORE)
        if env.get("PYTHONPATH"):
            if pypath not in env["PYTHONPATH"].split(os.pathsep):
                env["PYTHONPATH"] = f"{pypath}{os.pathsep}{env['PYTHONPATH']}"
        else:
            env["PYTHONPATH"] = pypath
        return env

    # The one thing that makes a sweep possible, and it took a day of failed runs
    # to find. By default a headless run does not wait for its MCP servers: the
    # init event lists the three HTTP ones as `pending`, the first turn starts
    # with none of their tools in context, and every Asana call comes back as a
    # permission denial that no `--permission-mode` affects, because the tool was
    # never registered to be permitted. `claude mcp list` says "Connected"
    # throughout, which is what makes it so misleading.
    #
    # NONBLOCKING=0 makes startup wait for the handshake instead, and 5s is not
    # enough for four OAuth servers behind the hub. With both set, asana reports
    # 31 tools and slack 16 rather than nought.
    #
    # Claude Code 2.1.274 adds CLAUDE_CODE_MCP_STARTUP_WAIT_MS, which is the
    # purpose-built version of this; 2.1.207 is what is installed, so this is the
    # pair that works here. Setting it is harmless on a newer build.
    mcp_env = {
        "MCP_CONNECTION_NONBLOCKING": "0",
        "MCP_CONNECT_TIMEOUT_MS": "20000",
    }

    def signed_in(self) -> bool:
        """Whether a run would reach a model at all.

        `claude auth status` reports the first-party account, which says nothing
        here: this account authenticates to the Kraken gateway through
        `apiKeyHelper`, so a working setup reports `loggedIn: false`. What
        actually matters is whether the credential can actually be *fetched*, so
        that is what is tested.

        Reading settings.json is not enough, and 28 Sep is what proved it. The
        helper was configured, this returned True, and the prep died six minutes
        in with "authorization timeout" because the 1Password vault was locked.
        A configured helper that cannot answer is worse than no helper: it passes
        the preflight and fails after the money is spent. So the helper is run.
        """
        return bool(self.credential())

    def credential(self) -> str:
        """The gateway key, resolved now, or "" when it cannot be had.

        Three places it can come from, cheapest first: an API key already in the
        environment, one written into settings.json, or `apiKeyHelper` run as a
        subprocess. The helper is the normal case here and the only one that can
        fail, because it shells out to 1Password.

        A key is memoised for the life of the process so a preflight and the run
        it guards cannot disagree, and so pressing Prep does not open the vault
        twice. A miss is not: on 21 Oct one locked-vault moment at noon was
        remembered by the page server all afternoon, so every ask fell through to
        Cursor and died in three seconds long after the vault was open again.
        """
        if not Claude._credential:
            Claude._credential = self._resolve_credential()
        return Claude._credential

    # Set by credential() once a key has been had; "" means none yet.
    _credential = ""

    def _resolve_credential(self) -> str:
        # ANTHROPIC_API_KEY only. Not ANTHROPIC_AUTH_TOKEN: that is the attending
        # host's bearer token, it is a different auth mechanism from an API key,
        # and `_base_env()` strips it on purpose. Passing it back in under the
        # key's name would send the run at the gateway with a credential the
        # gateway does not accept.
        if os.environ.get("ANTHROPIC_API_KEY"):
            return os.environ["ANTHROPIC_API_KEY"]
        user_settings = Path.home() / ".claude" / "settings.json"
        try:
            conf = json.loads(user_settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        if (conf.get("env") or {}).get("ANTHROPIC_API_KEY"):
            return conf["env"]["ANTHROPIC_API_KEY"]
        helper = conf.get("apiKeyHelper")
        if not helper:
            return ""
        # `_base_env()` rather than os.environ, because a helper run from inside
        # an attended session inherits variables that send it looking for a host
        # handshake. Not `env()`: that asks for the credential, which is what we
        # are in the middle of working out.
        #
        # 60s because `op read` on a locked vault blocks on a Touch ID prompt
        # nobody is there to answer, and failing is better than hanging a sweep.
        try:
            out = subprocess.run(
                helper, shell=True, capture_output=True, text=True, check=False,
                timeout=60, stdin=subprocess.DEVNULL, env=self._base_env(),
                cwd=ROOT,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if out.returncode != 0:
            return ""
        return out.stdout.strip()

    def mcp_states(self) -> dict[str, str]:
        """Every configured MCP server, and whether it is usable right now.

        Parsed from `claude mcp list`, which health-checks as it goes. The line
        ends in a tick for connected and says so in words when it needs
        authorising, so the test is on the words rather than on the glyph.

        **This answers a different question from the one a sweep needs.** It is an
        interactive health check: it reported all four Connected for a whole day
        while every headless run saw them `pending` and had no Asana tool at all.
        It is the right test for "has the owner approved this grant", which is what the
        Log in button and `setup-mcp.sh` ask. For "can the run about to spend
        money actually call Asana", use `tools_ready()`.
        """
        out = subprocess.run(
            [self.binary, "mcp", "list"],
            capture_output=True, text=True, check=False, timeout=90,
            stdin=subprocess.DEVNULL, env=self.env(), cwd=ROOT,
        )
        states: dict[str, str] = {}
        for raw in ANSI.sub("", out.stdout + out.stderr).splitlines():
            name, sep, rest = raw.partition(":")
            if not sep or not name.strip() or name.strip().startswith("Checking"):
                continue
            low = rest.lower()
            if "needs authentication" in low:
                states[name.strip()] = "needs-auth"
            elif "connected" in low:
                states[name.strip()] = "connected"
            elif "failed" in low or "error" in low:
                states[name.strip()] = "failed"
        return states

    def login_args(self, what: str) -> list[str]:
        # No account step: the gateway credential comes from apiKeyHelper, so
        # every login here is one MCP server's OAuth grant.
        return ["mcp", "login", what]

    def event(self, raw: dict) -> tuple[str, str]:
        """One event, as (what kind of thing happened, the thing).

        Three kinds matter to a page with a spinner on it: a tool call starting,
        prose arriving, and the final result. Everything else is noise the log
        does not need.

        Two more matter to the watchdog: "model" when a request to the model is
        in flight and "idle" when it has finished. Thinking does not stream on
        this gateway even with partial messages, it arrives whole when it is
        done, so silence during a request is the model working rather than the
        session dying, and it is given longer.
        """
        kind = raw.get("type")
        if kind == "system" and raw.get("subtype") == "status":
            return ("model", "") if raw.get("status") == "requesting" else ("", "")
        if kind == "stream_event":
            step = (raw.get("event") or {}).get("type")
            if step == "message_start":
                return "model", ""
            if step == "message_stop":
                return "idle", ""
            return "", ""
        if kind == "assistant":
            for part in (raw.get("message") or {}).get("content") or []:
                if part.get("type") == "tool_use":
                    return "call", phrase_for(part.get("name") or "", part.get("input") or {})
                if part.get("type") == "text" and part.get("text"):
                    return "said", part["text"]
            return "", ""
        if kind == "result":
            whole = raw.get("result")
            if isinstance(whole, str) and whole.strip():
                return "result", whole
        return "", ""


# The one CLI that carries this desk's tool allow- and deny-lists.
KINDS: tuple[Kind, ...] = (Claude(),)

# What each tool is called, mapped to a verb. The lookup is lowercased.
VERBS = {
    "bash": "Running", "shell": "Running",
    "read": "Reading", "write": "Writing", "edit": "Editing",
    "notebookedit": "Editing", "delete": "Deleting", "ls": "Listing",
    "grep": "Searching", "glob": "Looking for",
    "websearch": "Searching the web", "webfetch": "Fetching",
    "fetch": "Fetching", "readlints": "Checking",
    "todowrite": "Planning", "task": "Handing off to a subagent",
    "skill": "Following its instructions",
}
HINTS = ("command", "path", "file_path", "target_file", "relative_workspace_path",
         "pattern", "glob_pattern", "query", "search_term", "url", "toolName",
         "prompt", "name")


def phrase_for(tool: str, args: dict) -> str:
    """One line naming what the agent has just gone off to do.

    An MCP tool carries its server in its name, which is the useful half: what
    a sweep is waiting on is almost always Asana or Slack answering.
    """
    if tool.startswith("mcp__"):
        bits = tool.split("__")
        server = bits[1] if len(bits) > 1 else "Tool"
        call = bits[2] if len(bits) > 2 else "call"
        return f"{server}: {call}"[:120]
    hint = ""
    for name in HINTS:
        value = args.get(name)
        if isinstance(value, str) and value.strip():
            hint = value.strip()
            break
    hint = hint.replace(f"{ROOT}/", "").replace(str(ROOT), "the repo")
    verb = VERBS.get(tool.lower()) or tool or "Working"
    return (f"{verb} {hint}" if hint else verb)[:120]


def pick(prefer: str = "") -> Kind | None:
    """Return Claude Code when installed, or None.

    A missing credential is deliberately not availability: callers receive the
    installed Claude kind and `blockers()` tells the owner how to unlock it.
    An obsolete `DANDORI_AGENT` value fails closed instead of selecting another
    CLI or silently overriding the owner's explicit setting.
    """
    wanted = prefer or settings.env("AGENT")
    if wanted and wanted != "claude":
        return None
    kind = KINDS[0]
    return kind if kind.available() else None


def model_for(job: str) -> str:
    """Which model a job runs on.

    Long-context sweeps use Sonnet 5.5; the shorter client-facing jobs use Opus
    5.5. Any per-job or environment choice still wins.
    """
    chosen = settings.env(f"{job.upper()}_MODEL") or settings.env("MODEL")
    default = "sonnet" if job in ("refresh", "prep") else "opus"
    return chosen or settings.section(job).get("model") or settings.model_id(default, "5-5")


EFFORTS = ("low", "medium", "high", "xhigh", "max")


def effort_for(job: str) -> str:
    """How hard the model thinks on a job, or "" for the CLI's own default.

    This is where a sweep's time goes, not the tools: on 30 Sep a prep spent
    1965 of its 2064 seconds in the model, most turns thinking 40,000 characters
    before a single Slack search. `config.json` sets it per job; DANDORI_<JOB>_EFFORT
    or DANDORI_EFFORT overrides for one run. A value the CLI would refuse is dropped
    rather than passed, so a typo costs thinking time, not the run.
    """
    value = (settings.env(f"{job.upper()}_EFFORT") or settings.env("EFFORT")
             or settings.section(job).get("effort") or "")
    return value if value in EFFORTS else ""


def tools_ready(kind: Kind, timeout: int = 180) -> tuple[bool, str]:
    """Whether a run starting now would actually have the tools, not just grants.

    The difference is the whole lesson of this file. `claude mcp list` health-checks
    interactively and said "Connected" for four servers while every headless run
    began with nought Asana tools, so a sweep spent real money reporting gaps that
    were only its own missing tools. This starts a run the way a sweep starts one,
    reads the `init` event it prints before the first turn, and counts the tools
    actually registered.

    It is cheap on purpose: the process is killed the moment `init` arrives, so no
    prompt is ever sent and no model is billed.
    """
    cmd = kind.command(model_for("refresh"), job="refresh") + kind.stream
    proc = subprocess.Popen(
        cmd, cwd=ROOT, env=kind.env(), text=True, bufsize=1,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") != "system" or event.get("subtype") != "init":
                continue
            tools = event.get("tools") or []
            short = [
                name for name in NEEDED_MCPS
                if not any(t.startswith(f"mcp__{name}__") for t in tools)
            ]
            if not short:
                return True, ""
            pending = {
                s.get("name"): s.get("status")
                for s in event.get("mcp_servers") or []
                if s.get("name") in short
            }
            return False, (
                f"{human(short)} answered the health check but brought no tools into "
                f"the run ({pending}). A sweep would read nothing and report gaps that "
                "are not there, so it has not been started."
            )
        return True, ""        # said nothing useful; let the run report for itself
    finally:
        proc.kill()
        proc.wait(timeout=10)


def human(names: list[str]) -> str:
    """"asana" and "slack" as "Asana and Slack", for a sentence on the page."""
    pretty = ["the database" if DB and n == DB else n.title() for n in names]
    if len(pretty) <= 1:
        return "".join(pretty)
    return ", ".join(pretty[:-1]) + " and " + pretty[-1]


def missing_mcps(kind: Kind) -> list[str]:
    """Which of the servers a sweep needs are not usable.

    A server that is configured but unauthorised and a server nobody configured
    are the same problem from the page's point of view: the sweep cannot read
    that system. They differ in the fix, which is why the message says which.
    """
    try:
        states = kind.mcp_states()
    except (OSError, subprocess.SubprocessError):
        return []          # could not ask; let the run report its own failure
    return [n for n in NEEDED_MCPS if states.get(n) != "connected"]


# A readiness answer is good for a few minutes, and the check costs about 16
# seconds of the owner watching a spinner. Nothing it looks at changes on its own: a
# grant lapses in hours, not between two presses of Ask. So the good answer is
# remembered and the bad one is not, because a bad one is what they are actively
# fixing, and a cached "Slack needs authorising" would survive the login that
# fixed it.
_ready_cache: dict[str, float] = {}
READY_FOR_SECS = 240


def forget_readiness() -> None:
    """Drop the cached answer, after a login or a `./setup-mcp.sh`."""
    _ready_cache.clear()


def blockers(kind: Kind | None) -> tuple[list[str], str]:
    """Everything standing between the button and a real run, in one look."""
    if kind is None:
        wanted = settings.env("AGENT")
        if wanted and wanted != "claude":
            return ["agent"], (
                f"DANDORI_AGENT={wanted} is not supported. This desk only runs "
                "Claude Code; remove the override and try again."
            )
        return ["install"], (
            "Claude Code is not installed. Install it, or type 'refresh' in a chat "
            "on this folder."
        )
    if not kind.signed_in():
        if kind.name == "claude":
            return ["account"], (
                "Claude Code could not fetch its model credential. apiKeyHelper "
                "in ~/.claude/settings.json ran and gave nothing back, which is "
                "usually a locked 1Password vault: unlock it, or run "
                "`op signin`, then try again."
            )
        return ["account"], f"{kind.name} is signed out."
    fresh = _ready_cache.get(kind.name, 0)
    if time.time() - fresh < READY_FOR_SECS:
        return [], ""
    # `tools_ready` first, because it is both stricter and cheaper. It asks the
    # only question that decides whether a sweep works, and a run that has the
    # tools cannot have a lapsed grant, so the health check below is only ever
    # reached to explain a failure. Asking it first cost 8s on every press.
    ready, why = tools_ready(kind)
    if ready:
        _ready_cache[kind.name] = time.time()
        return [], ""
    missing = missing_mcps(kind) or ["tools"]
    if missing:
        try:
            states = kind.mcp_states()
        except (OSError, subprocess.SubprocessError):
            states = {}
        if missing == ["tools"]:
            return ["tools"], why
        absent = [n for n in missing if n not in states]
        names = " and ".join(n.title() for n in missing)
        verb = "needs" if len(missing) == 1 else "need"
        if absent:
            return missing, (
                f"{names} {verb} connecting before a refresh can read anything. "
                f"{' and '.join(n.title() for n in absent)} "
                f"{'is' if len(absent) == 1 else 'are'} not configured yet: run "
                f"./setup-mcp.sh"
            )
        return missing, f"{names} {verb} authorising before a refresh can read anything."
    return ["tools"], why
