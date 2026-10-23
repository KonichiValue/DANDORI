#!/usr/bin/env python3
"""The client plugin layer: how `clients/<client>/` extends `core/`.

Modeled on Kraken Core's `octoenergy/plugins/clients/` layer. Everything universal
to DANDORI lives in `core/`; everything specific to a client or an owner's
workflow lives in `clients/<client>/`:

- `clients/<client>/config.json` and `profile.md` (data & prose)
- `clients/<client>/plugin.py` (optional Python hooks: custom checks, audit rules,
  account-number patterns)
- `clients/<client>/scripts/` (client workflow scripts, runnable via
  `<cmd> scripts` and `<cmd> run <script> [args...]`)
- `clients/<client>/docs/` (working documents, handovers, sheets and PDFs)

`core/` depends on this module's hook functions, never on a specific client's
code, so `core/` stays generic and clean for every CDL.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

import settings

DEFAULT_ACCOUNT_RE = re.compile(r"\bA-[0-9A-F]{8}\b")
_cache: dict = {"stamp": None, "mod": None}


def load() -> ModuleType | None:
    """Load `clients/<active>/plugin.py` if present, re-loading when edited."""
    path = settings.PLUGIN_FILE
    try:
        st = path.stat()
        stamp = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    if _cache["stamp"] != stamp:
        mod = None
        try:
            spec = importlib.util.spec_from_file_location("dandori_client_plugin", path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
        except Exception:
            mod = None
        _cache.update(stamp=stamp, mod=mod)
    return _cache["mod"]


def account_pattern() -> re.Pattern:
    """Regex matching a client account number in steps or notes."""
    mod = load()
    pat = getattr(mod, "ACCOUNT_RE", None) if mod else None
    return pat if isinstance(pat, re.Pattern) else DEFAULT_ACCOUNT_RE


def audit_checks() -> tuple[Callable, ...]:
    """Client-specific audit checks (`fn(board, report)`) from `plugin.py`."""
    mod = load()
    checks = getattr(mod, "AUDIT_CHECKS", ()) if mod else ()
    return tuple(checks) if isinstance(checks, (list, tuple)) else ()


def board_checks() -> tuple[tuple[str, Callable], ...]:
    """Client-specific gate checks (`(label, fn(board) -> list[str])`) from `plugin.py`."""
    mod = load()
    checks = getattr(mod, "CHECKS", ()) if mod else ()
    return tuple(checks) if isinstance(checks, (list, tuple)) else ()


def _script_summary(path: Path) -> str:
    """Extract the one-line summary from a client script's docstring or header comment."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        tree = ast.parse(text)
        doc = ast.get_docstring(tree)
        if doc:
            return doc.strip().splitlines()[0].strip()
    except SyntaxError:
        pass
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#!") or s.startswith("# -*-"):
            continue
        if s.startswith("#"):
            return s.lstrip("# -").strip()
        break
    return ""


def scripts() -> list[dict]:
    """Every workflow script in `clients/<active>/scripts/`."""
    folder = settings.CLIENT_SCRIPTS_DIR
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        out.append({
            "name": path.stem,
            "file": path.name,
            "path": path,
            "rel": settings.client_rel(path),
            "summary": _script_summary(path),
        })
    return out


def find_script(name: str) -> Path | None:
    """Resolve a script name (`verify-elec2027` or `verify-elec2027.py`) in `clients/<active>/scripts/`."""
    folder = settings.CLIENT_SCRIPTS_DIR
    if not folder.is_dir():
        return None
    for cand in (folder / name, folder / f"{name}.py"):
        if cand.is_file():
            return cand
    matches = [p for p in sorted(folder.glob("*.py")) if name.lower() in p.stem.lower()]
    return matches[0] if len(matches) == 1 else None


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("list", "-l", "--list"):
        rows = scripts()
        c_rel = settings.client_rel(settings.CLIENT_SCRIPTS_DIR)
        if not rows:
            print(f"No client scripts in {c_rel}/")
            return 0
        print(f"{settings.client_short()} workflow scripts in {c_rel}/:\n")
        width = max(len(r["name"]) for r in rows)
        for r in rows:
            summary = f"  {r['summary']}" if r["summary"] else ""
            print(f"  {r['name']:<{width}}{summary}")
        cmd = settings.command()
        print(f"\nRun one with: {cmd} run <script> [args...]")
        return 0
    if argv[0] == "run":
        argv = argv[1:]
    if not argv:
        print(f"usage: {settings.command()} run <script> [args...]", file=sys.stderr)
        return 2
    target = find_script(argv[0])
    if target is None:
        print(f"no client script matching {argv[0]!r} in {settings.client_rel(settings.CLIENT_SCRIPTS_DIR)}/",
              file=sys.stderr)
        return 1
    done = subprocess.run([sys.executable, str(target), *argv[1:]], cwd=settings.REPO)
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
