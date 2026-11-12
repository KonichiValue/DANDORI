#!/usr/bin/env python3
"""Keep Asana Status in sync on tickets owned by the owner.

A sweep reads what happened on a ticket and writes `asana.status_should` when
Asana's Status has fallen behind it (a fix released while the ticket still says
it is waiting for an engineer, or requirements begun while it says untouched).
When `settings.auto_asana_status()` is enabled, DANDORI updates the Status
custom field automatically at the end of a sweep (or when a draft is marked
posted) — strictly on incomplete tickets assigned to the owner (`owner_aliases`).
Cards also retain the manual `Set in Asana` button when auto-status is off.

It is the only place the desk writes to Asana, and it is shaped so that it can
only ever do this: the run is handed exactly one tool, `update_tasks`, and the
prompt names only numeric task GIDs, one Status custom field GID, and valid
option GIDs from `config.json` `asana.status_field`. Zero comments, zero
messages, and zero other fields are ever touched.

    ./core/asana_status.py <ticket ref or gid>   set one ticket to status_should
    ./core/asana_status.py --sync-owned          sync all behind tickets owned by me
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


def is_owned_by_me(ticket: dict) -> bool:
    """True only when Asana's assignee on `ticket` matches the desk owner."""
    asana = ticket.get("asana") or {}
    if asana.get("completed"):
        return False
    who = str(asana.get("assignee") or "").strip().lower()
    return bool(who and who in settings.owner_aliases())


def desired_owned_status(ticket: dict) -> str:
    """Target Status option name for an owned ticket that is behind, or ''."""
    if not is_owned_by_me(ticket):
        return ""
    should = B.status_behind(ticket) or B.status_from_build(ticket)
    if not should:
        return ""
    cur = str((ticket.get("asana") or {}).get("status") or "").strip()
    if should == cur:
        return ""
    opts = settings.asana_status_field().get("options") or {}
    if not (opts.get(should) or {}).get("gid"):
        return ""
    return should


def succeeded_gids(stream: str, wanted_gids: list[str]) -> tuple[set[str], str]:
    """Return the subset of `wanted_gids` that Asana confirmed updated."""
    said = ""
    landed: set[str] = set()
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
            ok = got.get("succeeded") or got.get("data")
            failed = got.get("failed") or []
            if isinstance(ok, list):
                ok_blob = json.dumps(ok)
                fail_blob = json.dumps(failed)
                for gid in wanted_gids:
                    if gid in ok_blob and gid not in fail_blob:
                        landed.add(gid)
    return landed, said


def _run_update_tasks(tasks_payload: list[dict], wanted_gids: list[str], timeout: int = 120) -> tuple[set[str], str]:
    import agent  # noqa: E402  (stdlib-only repo; agent.py sits beside this)

    kind = agent.pick("claude")
    if kind is None or kind.name != "claude":
        return set(), "Claude Code is not available to reach Asana"
    cmd = [
        kind.binary, "--print", "--model", model(),
        "--tools", "ToolSearch", "--allowedTools", TOOL,
        "--setting-sources", "user",
        "--settings", json.dumps({
            "apiKeyHelper": "printenv ANTHROPIC_API_KEY",
            "env": {"CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "0"},
        }),
        "--output-format", "stream-json", "--verbose",
    ]
    db = settings.database_mcp()
    for gone in ("mcp__slack__*", "mcp__notion__*", "mcp__google__*", "mcp__glean*",
                 *((f"mcp__{db}__*",) if db else ()),
                 "mcp__asana__get*", "mcp__asana__search*", "mcp__asana__add*",
                 "mcp__asana__create*", "mcp__asana__delete*", "mcp__asana__update_project*"):
        cmd += ["--disallowedTools", gone]
    payload = json.dumps(tasks_payload)
    prompt = (
        f"Call {TOOL} exactly once with tasks = {payload} and nothing else. "
        "Change no other field and call no other tool. Then reply with only the word DONE."
    )
    try:
        ran = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                             cwd="/tmp", env=kind.env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        return set(), f"Asana did not answer in {timeout}s"
    landed, said = succeeded_gids(ran.stdout, wanted_gids)
    if not landed:
        tail = said or (ran.stderr or ran.stdout)[-300:].strip()
        return set(), "Asana did not take it" + (f": {tail}" if tail else "")
    return landed, ""


def set_status(ref: str, timeout: int = 120) -> dict:
    """Set one ticket's Status to its status_should (owner-assigned tickets only)."""
    b = B.load()
    t = find(b, str(ref))
    if t is None:
        return {"error": "that ticket is not on the board"}
    if not is_owned_by_me(t):
        return {"error": "only tickets assigned to you in Asana are updated"}
    should = B.status_behind(t) or B.status_from_build(t)
    if not should:
        return {"error": "Asana already says what the board does"}
    field = settings.asana_status_field()
    option = (field.get("options", {}).get(should) or {}).get("gid", "")
    gid = str(t.get("id") or "")
    if not (field.get("gid") and option and re.fullmatch(r"\d+", gid)):
        return {"error": "the Status field is not set up in config.json (asana.status_field)"}

    landed, err = _run_update_tasks(
        [{"task": gid, "custom_fields": {field["gid"]: option}}],
        [gid],
        timeout=timeout,
    )
    if gid not in landed:
        return {"error": err or "Asana did not take it"}

    b_fresh = B.load()
    t_fresh = find(b_fresh, gid) or find(b_fresh, str(ref))
    was = str(((t_fresh or t).get("asana") or {}).get("status") or "")
    if t_fresh is not None:
        now = settings.now()
        t_fresh.setdefault("asana", {})["status"] = should
        t_fresh["asana"].pop("status_should", None)
        t_fresh["asana"].pop("status_why", None)
        t_fresh.setdefault("events", []).append({
            "on": now.date().isoformat(), "at": now.strftime("%H:%M"),
            "who": settings.owner_name() or "owner", "where": "Asana",
            "what": f"Status set from {was or 'nothing'} to {should} from the desk",
        })
        B.save(b_fresh)
    return {"ref": t.get("ref"), "was": was, "now": should}


def sync_owned_statuses(timeout: int = 120) -> list[dict]:
    """Batch-update Asana Status on all incomplete tickets assigned to the owner
    whose Status has fallen behind `status_should` or `by_stage`."""
    field = settings.asana_status_field()
    field_gid = str(field.get("gid") or "")
    options = field.get("options") or {}
    if not field_gid or not options:
        return []

    b = B.load()
    targets: list[tuple[str, str, str, str]] = []  # (gid, ref, was, should)
    payload: list[dict] = []
    for t in b.get("tickets", []):
        gid = str(t.get("id") or "")
        if not re.fullmatch(r"\d+", gid):
            continue
        should = desired_owned_status(t)
        if not should:
            continue
        opt_gid = str((options.get(should) or {}).get("gid") or "")
        if not opt_gid:
            continue
        was = str((t.get("asana") or {}).get("status") or "")
        targets.append((gid, str(t.get("ref") or gid), was, should))
        payload.append({"task": gid, "custom_fields": {field_gid: opt_gid}})

    if not payload:
        return []

    wanted_gids = [g for g, _, _, _ in targets]
    landed, _ = _run_update_tasks(payload, wanted_gids, timeout=timeout)
    if not landed:
        return []

    b_fresh = B.load()
    now = settings.now()
    moved: list[dict] = []
    for gid, ref, was, should in targets:
        if gid not in landed:
            continue
        t_fresh = find(b_fresh, gid)
        if t_fresh is None:
            continue
        t_fresh.setdefault("asana", {})["status"] = should
        t_fresh["asana"].pop("status_should", None)
        t_fresh["asana"].pop("status_why", None)
        t_fresh.setdefault("events", []).append({
            "on": now.date().isoformat(), "at": now.strftime("%H:%M"),
            "who": settings.owner_name() or "owner", "where": "Asana",
            "what": f"Status auto-updated from {was or 'nothing'} to {should} in Asana",
        })
        moved.append({"ref": ref, "was": was, "now": should})
    if moved:
        B.save(b_fresh)
    return moved


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--sync-owned":
        moved = sync_owned_statuses()
        for m in moved:
            print(f"{m['ref']}: Status {m['was']} -> {m['now']} in Asana")
        if not moved:
            print("No owned tickets needed an Asana Status update.")
        return 0
    if len(sys.argv) != 2:
        print("Usage: ./core/asana_status.py <ticket ref or gid | --sync-owned>")
        return 2
    got = set_status(sys.argv[1])
    if got.get("error"):
        print(got["error"])
        return 1
    print(f"{got['ref']}: Status {got['was']} -> {got['now']} in Asana")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
