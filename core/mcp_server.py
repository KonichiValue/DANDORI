#!/usr/bin/env python3
"""DANDORI MCP server (stdio JSON-RPC 2.0, Python stdlib only).

Gives Cursor and Claude Desktop zero-token-waste, instant access to any job,
ticket, draft, or client account diagnostic on the active DANDORI board, plus
safe board patch / tick actions — so a chat never has to grep `desk.md` or
hand-write partial SQL queries to find what the owner is looking at.

Tools exposed:
- `dandori_context`: Full context bundle for an item number (`42`), ticket ref,
  or `"board"`, automatically running the local client account diagnostic hook
  when an account number is present.
- `dandori_inspect_account`: Run the active client's account diagnostic script
  (`ACCOUNT_CONTEXT` in `clients/<client>/plugin.py`) on one or more accounts.
- `dandori_tick`: Move an item's status, step, or draft via `./core/tick.py`
  when the owner explicitly instructs it.
- `dandori_apply_patch`: Apply a validated JSON patch through `./core/apply.py`
  (`board.save()`) and rebuild `desk.html` + `desk.md`.

Usage:
    python3 core/mcp_server.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE))

import apply as A  # noqa: E402
import board as B  # noqa: E402
import plugins  # noqa: E402
import settings  # noqa: E402
import tick as T  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"


def build_context_bundle(ref: str, draft_num: int = 0, include_db: bool = True) -> str:
    """Build a self-contained Markdown handoff bundle for `item:N`, `ticket:REF`, or `board`."""
    b = B.load()
    raw = str(ref or "board").strip()
    if raw.isdigit():
        raw = f"item:{raw}"
    elif raw not in ("board", "day", "desk") and ":" not in raw:
        raw = f"ticket:{raw}"

    lines = [
        f"# DANDORI Context ({settings.client_short()} workspace: `{settings.client_rel(settings.CLIENT_DIR)}/`)",
        f"- Board checked_at: `{b.get('checked_at', 'never')}`",
    ]

    if raw in ("board", "day", "desk"):
        proc = subprocess.run(
            [sys.executable, str(CORE / "digest.py")],
            cwd=settings.REPO, capture_output=True, text=True, timeout=15, check=False,
        )
        lines += ["", proc.stdout.strip()]
        return "\n".join(lines)

    kind, _, ident = raw.partition(":")
    ticket: dict | None = None
    item: dict | None = None
    if kind == "item":
        ticket, item = B.by_id(b, ident)
    else:
        ticket = next(
            (t for t in b.get("tickets", []) if str(t.get("ref")) == ident or str(t.get("id")) == ident),
            None,
        )

    if not ticket:
        return f"No ticket or item matching `{ref}` on `{settings.client_rel(B.BOARD)}`."

    t_ref = str(ticket.get("ref") or ticket.get("id") or "?")
    lines += [
        f"- Ticket: **{t_ref}** — {ticket.get('title_en') or ticket.get('title_ja') or ''}",
        f"- Japanese title: {ticket.get('title_ja') or '-'}",
        f"- Asana URL: {ticket.get('asana_url') or 'none'}",
        f"- Asana status: `{json.dumps(ticket.get('asana') or {}, ensure_ascii=False)}`",
        f"- Where ticket stands: {ticket.get('where_it_stands') or '-'}",
    ]
    if ticket.get("internal_ticket"):
        lines.append(f"- Internal build: `{json.dumps(ticket['internal_ticket'], ensure_ascii=False)}`")
    if ticket.get("threads"):
        lines.append("- Linked threads:")
        for th in ticket["threads"]:
            lines.append(f"  - [{th.get('where', '?')}] {th.get('label', '')}: {th.get('url', '')} (last: {th.get('last_at', '?')})")
    evs = (ticket.get("events") or [])[-8:]
    if evs:
        lines.append("- Recent timeline events:")
        for ev in evs:
            lines.append(f"  - `{ev.get('on', '')} {ev.get('at', '')}` **{ev.get('who', '')}** ({ev.get('where', '')}): {ev.get('what', '')}")

    scan_parts = [str(ticket.get("title_en") or ""), str(ticket.get("where_it_stands") or "")]
    if item:
        trimmed = {k: v for k, v in item.items() if k != "history"}
        if isinstance(trimmed.get("prepared"), dict) and "earlier" in trimmed["prepared"]:
            trimmed["prepared"] = {k: v for k, v in trimmed["prepared"].items() if k != "earlier"}
        drafts = B.drafts(item)
        if draft_num and 1 <= draft_num <= len(drafts):
            lines.append(f"\n## Focus: Job {item.get('id')} — Draft {draft_num}")
            lines.append("```json\n" + json.dumps(drafts[draft_num - 1], ensure_ascii=False, indent=2) + "\n```")
        lines.append(f"\n## Job {item.get('id')} on {t_ref} (edit via `./core/apply.py`, state via `./core/tick.py`)")
        lines.append("```json\n" + json.dumps(trimmed, ensure_ascii=False, indent=2) + "\n```")
        scan_parts.append(json.dumps(trimmed, ensure_ascii=False))
    else:
        open_items = [
            {k: v for k, v in i.items() if k != "history"}
            for i in ticket.get("items", []) if B.is_open(i)
        ]
        lines.append(f"\n## Open items on {t_ref} ({len(open_items)})")
        lines.append("```json\n" + json.dumps(open_items, ensure_ascii=False, indent=2) + "\n```")
        if ticket.get("prep"):
            lines.append("\n## Current speaking `prep` block")
            lines.append("```json\n" + json.dumps(ticket["prep"], ensure_ascii=False, indent=2) + "\n```")
        scan_parts.append(json.dumps(open_items, ensure_ascii=False))

    if include_db:
        acct_pat = plugins.account_pattern()
        accts = list(dict.fromkeys(m.group(0).upper() for m in acct_pat.finditer(" ".join(scan_parts))))[:3]
        if accts:
            diag = plugins.account_context(accts, test=False)
            if diag:
                lines += ["", "## Automatic Account Diagnostic (local plugin)", "", diag]

    lines += [
        "",
        "---",
        "Note: When updating this item or ticket on the board, apply a JSON patch with `./core/apply.py` (or `dandori_apply_patch`) and rebuild with `./core/tick.py --rebuild`. Never send external messages without explicit approval.",
    ]
    return "\n".join(lines)


TOOLS = [
    {
        "name": "dandori_context",
        "description": (
            "Fetch complete context for a DANDORI job number (e.g. '42' or 'item:42'), "
            "ticket ref (e.g. '保安閉栓'), or 'board', including drafts, timeline, "
            "evidence files, and automatic multi-layer account diagnostics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Job number ('42'), 'item:42', ticket ref, or 'board'"},
                "draft": {"type": "integer", "description": "Optional 1-based draft number to focus on"},
                "include_db": {"type": "boolean", "description": "Include automatic account diagnostic when accounts appear (default true)"},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "dandori_inspect_account",
        "description": (
            "Run the active client's zero-PII multi-layer account diagnostic and live "
            "code-rule lookup on 1 to 3 client account numbers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "accounts": {"type": "array", "items": {"type": "string"}, "description": "Account numbers (e.g. ['A-B3AB38A9'])"},
                "test": {"type": "boolean", "description": "Query TEST replica instead of PROD (default false)"},
            },
            "required": ["accounts"],
        },
    },
    {
        "name": "dandori_tick",
        "description": "Move a DANDORI job's state, tick a step, or mark a draft posted via core/tick.py (only when instructed by the owner).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer", "description": "Permanent job number"},
                "verb": {"type": "string", "enum": ["done", "sent", "waiting", "mine", "dropped", "undo", "draft", "step"]},
                "who": {"type": "string", "description": "Person waiting on (when verb='waiting' or 'draft')"},
                "note": {"type": "string", "description": "Short reason or note"},
                "number": {"type": "integer", "description": "1-based draft or step number (when verb='draft' or 'step')"},
            },
            "required": ["item_id", "verb"],
        },
    },
    {
        "name": "dandori_apply_patch",
        "description": "Apply a DANDORI board patch through board.save() and rebuild desk.html + desk.md.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "patch": {"type": "object", "description": "Patch object matching core/apply.py schema (items, tickets, events, new_items, glossary, etc.)"},
            },
            "required": ["patch"],
        },
    },
]


def _call_tool(name: str, args: dict) -> tuple[str, bool]:
    if name == "dandori_context":
        text = build_context_bundle(
            str(args.get("ref") or "board"),
            draft_num=int(args.get("draft") or 0),
            include_db=bool(args.get("include_db", True)),
        )
        return text, False

    if name == "dandori_inspect_account":
        accts = [str(x) for x in (args.get("accounts") or []) if x]
        out = plugins.account_context(accts, test=bool(args.get("test", False)))
        if not out:
            return "No account diagnostic configured or no matching accounts.", True
        return out, False

    if name == "dandori_tick":
        ident = str(args.get("item_id") or "")
        verb = str(args.get("verb") or "done")
        who = str(args.get("who") or "")
        note = str(args.get("note") or "")
        num = int(args.get("number") or 0)
        cmd = [sys.executable, str(CORE / "tick.py"), ident]
        if verb == "waiting":
            cmd += ["-w", who]
        elif verb == "sent":
            cmd.append("--sent")
        elif verb == "mine":
            cmd.append("--mine")
        elif verb == "dropped":
            cmd.append("--dropped")
        elif verb == "undo":
            cmd.append("--undo")
        elif verb == "draft":
            cmd += ["--draft", str(num or 1)]
            if who:
                cmd += ["-w", who]
        elif verb == "step":
            cmd += ["--step", str(num or 1)]
        if note:
            cmd += ["-n", note]
        proc = subprocess.run(cmd, cwd=settings.REPO, capture_output=True, text=True, timeout=30, check=False)
        out = (proc.stdout or proc.stderr or "").strip()
        return out or "ok", proc.returncode != 0

    if name == "dandori_apply_patch":
        patch = args.get("patch")
        if not isinstance(patch, dict):
            return "patch must be a JSON object", True
        b = B.load()
        try:
            said = A.apply(b, patch)
            problems = B.check(b)
            if problems:
                return "Refused — board shape error:\n" + "\n".join(problems), True
            B.save(b)
            T.rebuild()
            return "Applied & rebuilt: " + "; ".join(said), False
        except Exception as exc:  # noqa: BLE001
            return f"Patch failed: {exc}", True

    return f"Unknown tool: {name}", True


def _reply(req_id, result: dict) -> None:
    msg = {"jsonrpc": "2.0", "id": req_id, "result": result}
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(req_id, code: int, message: str) -> None:
    msg = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method = req.get("method", "")
        req_id = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            _reply(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "dandori", "version": settings.version()},
            })
        elif method in ("notifications/initialized", "cancelled"):
            continue
        elif method == "ping":
            _reply(req_id, {})
        elif method == "tools/list":
            _reply(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            t_name = str(params.get("name") or "")
            t_args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            text, is_err = _call_tool(t_name, t_args)
            _reply(req_id, {
                "content": [{"type": "text", "text": text}],
                "isError": is_err,
            })
        elif req_id is not None:
            _error(req_id, -32601, f"Method not found: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
