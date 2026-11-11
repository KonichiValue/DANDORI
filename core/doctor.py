#!/usr/bin/env python3
"""Pre-flight health check for the desk, connections, and next session.

Runs in under three seconds and checks the five things that silently break a
morning before a client meeting:
1. Local DANDORI page server health and key.
2. Network and TLS state (catches Zscaler certificate re-authentication).
3. Read-only database replica session (`kraken cf check-token`, when configured).
4. Claude Code and configured MCP servers (`asana`, `slack`, `google`, `notion`).
5. Board freshness, speaking script readiness for today's room, unread meeting
   notes, and upcoming prioritization deadlines.

    ./core/doctor.py            human-readable pre-flight report
    ./core/doctor.py --json     structured JSON for Shortcuts and scripts
    ./core/doctor.py --quiet    one-line summary (exit 0 if healthy, 1 if warnings)
    ./core/doctor.py --deep     also run `claude mcp list` to verify live OAuth grants
"""

from __future__ import annotations

import argparse
import json
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CORE = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE))

import board as B  # noqa: E402
import calendar_sync  # noqa: E402
import prioritize  # noqa: E402
import settings  # noqa: E402


def check_server() -> dict:
    port = settings.port()
    serve_file = settings.STATE_DIR / "serve.json"
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            ok = resp.status == 200
    except (OSError, urllib.error.URLError):
        ok = False
    has_key = False
    if serve_file.exists():
        try:
            has_key = bool(json.loads(serve_file.read_text(encoding="utf-8")).get("key"))
        except (OSError, ValueError):
            has_key = False
    if ok and has_key:
        return {"name": "Desk server", "status": "ok", "detail": f"running on 127.0.0.1:{port}"}
    return {
        "name": "Desk server",
        "status": "warn",
        "detail": f"not answering on port {port} (run: {settings.command()} open)",
    }


def check_network() -> dict:
    req = urllib.request.Request("https://app.asana.com", method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=2.5):
            return {"name": "Network & TLS", "status": "ok", "detail": "HTTPS certificate chain valid"}
    except urllib.error.HTTPError:
        # An HTTP status means TLS handshake succeeded.
        return {"name": "Network & TLS", "status": "ok", "detail": "HTTPS certificate chain valid"}
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        if isinstance(exc.reason, ssl.SSLError) or "CERTIFICATE" in reason.upper():
            return {
                "name": "Network & TLS",
                "status": "warn",
                "detail": "TLS certificate error (check that Zscaler is connected and authenticated)",
            }
        return {"name": "Network & TLS", "status": "warn", "detail": f"network unreachable: {reason[:80]}"}
    except OSError as exc:
        return {"name": "Network & TLS", "status": "warn", "detail": f"network error: {str(exc)[:80]}"}


def check_database() -> dict:
    db_mcp = settings.database_mcp()
    conn = str(settings.section("database").get("conn") or "")
    if not db_mcp and not conn:
        return {"name": "Database session", "status": "ok", "detail": "no replica configured"}
    kraken = shutil.which("kraken")
    if not kraken:
        return {
            "name": "Database session",
            "status": "warn",
            "detail": "kraken CLI not found on PATH",
        }
    try:
        proc = subprocess.run(
            [kraken, "cf", "check-token"],
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "name": "Database session",
            "status": "warn",
            "detail": "kraken cf check-token timed out",
        }
    if proc.returncode == 0:
        return {
            "name": "Database session",
            "status": "ok",
            "detail": f"Cloudfarer token valid ({conn or db_mcp})",
        }
    return {
        "name": "Database session",
        "status": "warn",
        "detail": "Cloudfarer token expired (run: kraken cloudfarer login)",
    }


def check_mcp(deep: bool = False) -> dict:
    claude = shutil.which("claude")
    if not claude:
        return {"name": "Claude & MCP", "status": "warn", "detail": "claude CLI not installed"}
    cfg_path = Path.home() / ".claude.json"
    configured: set[str] = set()
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            configured = set((data.get("mcpServers") or {}).keys())
        except (OSError, ValueError):
            pass
    wanted = ["asana", "slack", "google", "notion"]
    db_mcp = settings.database_mcp()
    if db_mcp:
        wanted.append(db_mcp)
    missing = [s for s in wanted if s not in configured]
    if missing:
        return {
            "name": "Claude & MCP",
            "status": "warn",
            "detail": f"missing MCP config for {', '.join(missing)} (run: {settings.command()} mcp)",
        }
    if deep:
        try:
            proc = subprocess.run(
                [claude, "mcp", "list"],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            needs = [
                ln.split(":", 1)[0].strip()
                for ln in (proc.stdout + proc.stderr).splitlines()
                if "Needs authentication" in ln and ln.split(":", 1)[0].strip() in wanted
            ]
            if needs:
                return {
                    "name": "Claude & MCP",
                    "status": "warn",
                    "detail": f"login needed for {', '.join(needs)} (run: {settings.command()} mcp)",
                }
        except subprocess.TimeoutExpired:
            pass
    return {"name": "Claude & MCP", "status": "ok", "detail": f"configured ({', '.join(wanted)})"}


def check_board_state() -> list[dict]:
    out: list[dict] = []
    if not B.BOARD.exists():
        out.append({
            "name": "Board freshness",
            "status": "warn",
            "detail": f"no board yet (run: {settings.command()} refresh)",
        })
        return out

    b = B.load()
    stamp = str(b.get("checked_at") or "")
    age_hours = 999.0
    if stamp:
        try:
            age_hours = (datetime.now().astimezone() - datetime.fromisoformat(stamp)).total_seconds() / 3600.0
        except ValueError:
            pass
    if age_hours > 6:
        out.append({
            "name": "Board freshness",
            "status": "warn",
            "detail": f"last swept {int(age_hours)}h ago ({stamp[:16]}); run `{settings.command()} refresh`",
        })
    else:
        mins = int(age_hours * 60)
        label = f"{mins}m ago" if mins < 60 else f"{age_hours:.1f}h ago"
        out.append({"name": "Board freshness", "status": "ok", "detail": f"swept {label}"})

    today = settings.now().date().isoformat()
    sessions = [s for s in (b.get("sessions") or []) if not s.get("skipped")]
    today_sessions = [s for s in sessions if s.get("date") == today]
    for_date = str((b.get("script") or {}).get("for_date") or "")
    if today_sessions:
        room = today_sessions[0]
        r_label = f"{room.get('title') or room.get('label') or room.get('kind')} at {room.get('at', '?')}"
        if for_date != today:
            out.append({
                "name": "Speaking script",
                "status": "warn",
                "detail": f"today has {r_label}, but script is for {for_date or 'none'} (run: {settings.command()} prep)",
            })
        else:
            out.append({"name": "Speaking script", "status": "ok", "detail": f"ready for {r_label}"})
    elif sessions:
        nxt = sessions[0]
        out.append({
            "name": "Speaking script",
            "status": "ok",
            "detail": f"next room {nxt.get('date')} {nxt.get('at', '')} ({for_date or 'no script yet'})",
        })

    unread = calendar_sync.unread_notes(b)
    st_notes = calendar_sync.standup_notes(b)
    unread_count = len(unread) + (1 if st_notes and st_notes.get("ready") and st_notes.get("docs") else 0)
    if unread_count:
        out.append({
            "name": "Meeting notes",
            "status": "warn",
            "detail": f"{unread_count} meeting note doc(s) waiting to fold in (run: {settings.command()} refresh)",
        })
    else:
        out.append({"name": "Meeting notes", "status": "ok", "detail": "all recent Gemini notes folded in"})

    if settings.prioritization():
        ps = prioritize.summary(b)
        nxt_p = ps.get("next") or {}
        n_raise = len(ps.get("raise") or [])
        n_fixes = len(ps.get("fixes") or [])
        if nxt_p and nxt_p.get("ask_days", 99) <= 1 and (n_raise or n_fixes):
            out.append({
                "name": "Prioritization",
                "status": "warn",
                "detail": f"{n_raise} build(s) to raise and {n_fixes} to fix in Asana by {nxt_p.get('ask_by')}",
            })
        elif nxt_p:
            out.append({
                "name": "Prioritization",
                "status": "ok",
                "detail": f"next round {nxt_p.get('date')} (raise by {nxt_p.get('ask_by')})",
            })

    return out


def run_all(deep: bool = False) -> dict:
    checks = [
        check_server(),
        check_network(),
        check_database(),
        check_mcp(deep=deep),
        *check_board_state(),
    ]
    warnings = [c for c in checks if c["status"] != "ok"]
    return {
        "checked_at": settings.now().isoformat(timespec="seconds"),
        "ok": len(warnings) == 0,
        "warnings": len(warnings),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="output JSON")
    ap.add_argument("--quiet", action="store_true", help="one-line summary")
    ap.add_argument("--deep", action="store_true", help="also verify live MCP OAuth grants")
    args = ap.parse_args(argv)

    rep = run_all(deep=args.deep)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep["ok"] else 1

    if args.quiet:
        if rep["ok"]:
            print("doctor: all checks ok")
            return 0
        bad = "; ".join(f"{c['name']}: {c['detail']}" for c in rep["checks"] if c["status"] != "ok")
        print(f"doctor: {rep['warnings']} warning(s) - {bad}")
        return 1

    for c in rep["checks"]:
        mark = "ok  " if c["status"] == "ok" else "WARN"
        print(f"[{mark}] {c['name']:<18} {c['detail']}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
