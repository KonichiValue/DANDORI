#!/usr/bin/env python3
"""Turn a document in `docs/` into the PDF that gets handed to the client.

Some work leaves this desk as a document rather than as a message: a handover
sheet, a procedure, anything the client keep open on a second screen while they
do the thing. Those live in `docs/` as HTML, because HTML is what this repo can write
and diff, and they go out as PDF, because that is what gets attached to a
ticket and read on a phone.

Chrome does the printing. Standard-library PDF writing cannot embed a CJK (or
any non-Latin) font, so a hand-rolled PDF comes out as boxes, and every third-party library
that solves that is a dependency this repo does not take.

    python3 make_doc.py                  every document in docs/
    python3 make_doc.py handover-x.html  just that one
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import settings

ROOT = settings.REPO
DOCS = settings.DOCS_DIR
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def to_pdf(html: Path) -> bool:
    """Print one document, and wait for the file rather than for Chrome.

    Headless Chrome writes the PDF and then sometimes sits there, so waiting on
    the process can hang a build that has already finished. The artefact is the
    thing worth waiting for.
    """
    pdf = html.with_suffix(".pdf")
    before = pdf.stat().st_mtime if pdf.exists() else 0
    profile = tempfile.mkdtemp(prefix="desk-doc-")
    proc = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--virtual-time-budget=4000",
            f"--user-data-dir={profile}",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf}",
            html.as_uri(),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(40):
            time.sleep(0.5)
            if pdf.exists() and pdf.stat().st_mtime > before and pdf.stat().st_size:
                return True
        return False
    finally:
        # Stop Chrome and clear the throwaway profile, or each run leaves a
        # temp tree and a zombie behind.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main(argv: list[str]) -> int:
    if not CHROME.exists():
        print(f"no Chrome at {CHROME}: open the HTML and print it by hand")
        return 1
    names = argv or [p.name for p in sorted(DOCS.glob("*.html"))]
    if not names:
        print("nothing in docs/ to print")
        return 1
    failed = 0
    for name in names:
        html = DOCS / Path(name).name
        if not html.is_file():
            print(f"missing {html}")
            failed += 1
            continue
        ok = to_pdf(html)
        print(f"{'wrote' if ok else 'FAILED'} {html.with_suffix('.pdf').name}")
        failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
