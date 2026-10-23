#!/usr/bin/env python3
"""The page server answers this Mac only, and there is no way to open it wider.

The desk key drives real agent runs, and several reads are unkeyed because they
show what the page already shows. Both are only safe on loopback. This binds the
real server on a spare port and checks the address, and fails if the LAN mode
or the phone command that once opened it to the Wi-Fi comes back.

Loopback alone is not enough: a website that rebinds its own name to 127.0.0.1
reaches the server from loopback. So this also checks that a foreign Host is
refused, that a foreign Origin cannot POST, and that /health never carries the
key. Prints `FAIL: ...` lines for check.py and exits 1 on any.
"""

from __future__ import annotations

import http.client
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
sys.path.insert(0, str(CORE))

import serve  # noqa: E402


def bound_to_loopback() -> list[str]:
    server = serve.local_server(0)
    try:
        host = server.server_address[0]
    finally:
        server.server_close()
    return [] if host == "127.0.0.1" else [f"local_server() bound {host}, not 127.0.0.1"]


def no_way_wider() -> list[str]:
    bad = []
    if "0.0.0.0" in (CORE / "serve.py").read_text(encoding="utf-8"):
        bad.append("core/serve.py mentions 0.0.0.0 again")
    usage = subprocess.run(
        [sys.executable, str(CORE / "serve.py"), "--help"],
        capture_output=True, text=True, check=False, cwd=ROOT,
    ).stdout
    if "--lan" in usage:
        bad.append("core/serve.py takes --lan again")
    cli = (ROOT / "bin" / "DANDORI").read_text(encoding="utf-8")
    if "phone)" in cli or "--lan" in cli:
        bad.append("bin/DANDORI has a phone or --lan path again")
    return bad


def refuses_foreign_host() -> list[str]:
    server = serve.local_server(0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(method: str, path: str, headers: dict) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request(method, path, body=b"" if method == "POST" else None,
                         headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    bad = []
    try:
        status, _ = call("GET", "/health", {"Host": f"evil.example:{port}"})
        if status != 421:
            bad.append(f"GET /health with a foreign Host returned {status}, not 421")
        status, body = call("GET", "/health", {"Host": f"127.0.0.1:{port}"})
        if status != 200:
            bad.append(f"GET /health on 127.0.0.1 returned {status}, not 200")
        if serve.KEY.encode() in body or b'"key"' in body:
            bad.append("GET /health hands out the desk key again")
        status, _ = call("POST", f"/api/refresh?k={serve.KEY}",
                         {"Host": f"evil.example:{port}"})
        if status != 421:
            bad.append(f"POST with a foreign Host returned {status}, not 421")
        status, _ = call("POST", f"/api/refresh?k={serve.KEY}",
                         {"Host": f"127.0.0.1:{port}", "Origin": "http://evil.example"})
        if status != 403:
            bad.append(f"POST with a foreign Origin returned {status}, not 403")
    finally:
        server.shutdown()
        server.server_close()
    return bad


def main() -> int:
    problems = bound_to_loopback() + no_way_wider() + refuses_foreign_host()
    for p in problems:
        print(f"FAIL: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
