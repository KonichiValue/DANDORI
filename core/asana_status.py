#!/usr/bin/env python3
"""Set one ticket's Status in Asana, because the owner pressed the button.

A sweep reads what happened on a ticket and writes `asana.status_should` when
Asana's Status has fallen behind it (a fix released while the ticket still says
it is waiting for an engineer). The card shows both, in Asana's colours, and a
Set in Asana button. This is what that button runs.

It is the one place the desk writes to Asana, and it is shaped so that it can
only ever do this: the owner presses it on the ticket they mean, the run is
handed exactly one tool, `update_tasks`, and the prompt names one task, one
field and one option, all three read from `config.json` `asana.status_field`
and the board rather than from anything the model wrote. Every other job on
the desk still has `mcp__asana__update*` taken away (`agent.NEVER`).

    ./asana_status.py <ticket ref or gid>     set it to status_should now
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import board as B  # noqa: E402
import settings  # noqa: E402

TOOL = "mcp__asana__update_tasks"


def model() -> str:
    """One tool call and the word DONE: Haiku, unless overridden."""
    return settings.env("ASANA_MODEL") or settings.model_id("haiku")


def find(b: dict, ref: str) -> dict | None:
    for t in b.get("tickets", []):
        if ref in (str(t.get("id")), str(t.get("ref"))):
            return t
    return None


def succeeded(stream: str, gid: str) -> tuple[bool, str]:
    """Whether Asana said the update went through, and what it said if not."""
    said = ""
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
                body = "".join(str(x.get("text", "")) for x in body if isinstance(x, dict))
            body = str(body or "")
            said = body[:300]
            try:
                got = json.loads(body)
            except ValueError:
                continue
            if not isinstance(got, dict):
                continue
            # The hub answers {"data": [...], "failed": [...]}; Asana's own
            # wording is "succeeded". Either way the task has to be in the one
            # list and not the other.
            ok = got.get("succeeded") or got.get("data")
            failed = got.get("failed") or []
            if (isinstance(ok, list) and any(gid in json.dumps(x) for x in ok)
                    and not any(gid in json.dumps(x) for x in failed)):
                return True, ""
    return False, said


def set_status(ref: str, timeout: int = 120) -> dict:
    """Set the ticket's Status to its status_should. Returns what happened."""
    import agent  # noqa: E402  (stdlib-only repo; agent.py sits beside this)

    b = B.load()
    t = find(b, str(ref))
    if t is None:
        return {"error": "that ticket is not on the board"}
    should = B.status_behind(t)
    if not should:
        return {"error": "Asana already says what the board does"}
    field = settings.asana_status_field()
    option = (field.get("options", {}).get(should) or {}).get("gid", "")
    gid = str(t.get("id") or "")
    if not (field.get("gid") and option and re.fullmatch(r"\d+", gid)):
        return {"error": "the Status field is not set up in config.json (asana.status_field)"}

    kind = agent.pick("claude")
    if kind is None or kind.name != "claude":
        return {"error": "Claude Code is not available to reach Asana"}
    cmd = [
        kind.binary, "--print", "--model", model(),
        "--tools", "ToolSearch", "--allowedTools", TOOL,
        "--setting-sources", "user",
        "--settings", json.dumps({"apiKeyHelper": "printenv ANTHROPIC_API_KEY"}),
        "--output-format", "stream-json", "--verbose",
    ]
    db = settings.database_mcp()
    for gone in ("mcp__slack__*", "mcp__notion__*", "mcp__google__*", "mcp__glean*",
                 *((f"mcp__{db}__*",) if db else ()),
                 "mcp__asana__get*", "mcp__asana__search*", "mcp__asana__add*",
                 "mcp__asana__create*", "mcp__asana__delete*", "mcp__asana__update_project*"):
        cmd += ["--disallowedTools", gone]
    payload = json.dumps([{"task": gid, "custom_fields": {field["gid"]: option}}])
    prompt = (
        f"Call {TOOL} exactly once with tasks = {payload} and nothing else. "
        "Change no other field and call no other tool. Then reply with only the word DONE."
    )
    try:
        ran = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                             cwd="/tmp", env=kind.env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"Asana did not answer in {timeout}s"}
    ok, said = succeeded(ran.stdout, gid)
    if not ok:
        tail = said or (ran.stderr or ran.stdout)[-300:].strip()
        return {"error": "Asana did not take it" + (f": {tail}" if tail else "")}

    # What Asana now says goes on the board, with the move in the timeline, so
    # the next sweep and the page agree without reading Asana again.
    was = str((t.get("asana") or {}).get("status") or "")
    now = settings.now()
    t.setdefault("asana", {})["status"] = should
    t["asana"].pop("status_should", None)
    t["asana"].pop("status_why", None)
    t.setdefault("events", []).append({
        "on": now.date().isoformat(), "at": now.strftime("%H:%M"),
        "who": settings.owner_name() or "owner", "where": "Asana",
        "what": f"Status set from {was or 'nothing'} to {should} from the desk",
    })
    B.save(b)
    return {"ref": t.get("ref"), "was": was, "now": should}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    got = set_status(sys.argv[1])
    if got.get("error"):
        print(got["error"])
        return 1
    print(f"{got['ref']}: Status {got['was']} -> {got['now']} in Asana")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
