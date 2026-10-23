#!/usr/bin/env python3
"""Write a whole sweep's findings to the board in one call.

A sweep used to write the board in a dozen hand-written python scripts, each one
a 50 to 110 second model turn spent re-typing `import board as B`, a loop to find
the ticket, and the mutation, on a context that grew with every one. This takes
the findings as one JSON file and makes every edit through `board.save()`, so the
lock, the merge, the shape check and `prepared.earlier` all still hold.

    ./apply.py tmp/patch.json        write the file with the Write tool first
    ./apply.py tmp/patch.json --dry  say what it would do, write nothing

The patch, every key optional:

    {
      "events":   [{"ticket": "HOLD-RELEASE", "on": "2026-10-07", "at": "12:08",
                    "who": "...", "what": "...", "so_what": "...",
                    "where": "Asana", "source_url": "https://..."}],
      "threads":  [{"ticket": "HOLD-RELEASE", "url": "https://...", "last_at": "...",
                    "last_from": "...", "gist": "...", "label": "..."}],
                  upserted by url, and stamped checked = now
      "checked":  {"HOLD-RELEASE": "*", "INVOICE-REPRINT": ["slack.com/archives/C09"]},
                  stamp threads looked at (all, or those whose url contains one)
      "tickets":  {"HOLD-RELEASE": {"where_it_stands": "...", "asana.status": "...",
                                   "internal_ticket.build.stage": "released",
                                   "closes_when": [...], "terms+": [{...}]}},
      "items":    {"42": {"title": "...", "draft.body_ruby": "...",
                          "state": "waiting", "who": "Alex", "note": "why",
                          "chase_on": "2026-10-09"}},
      "new_items":   [{"ticket": "HOLD-RELEASE", "title": "...", "steps": [...], ...}],
      "new_tickets": [{"id": "<gid>", "ref": "...", "title_ja": "...", ...}],
      "board":    {"news": [...], "brief": {...}, "alert": null,
                   "other_notes+": [{...}], "milestones.price-update.status": "on-track"},
      "glossary": {"<client term>": "open the day after close"},
      "swept":    true
    }

A key is a dotted path into the record. `key+` appends to a list rather than
replacing it, and `null` removes the field. A milestone is addressed by its id
inside `milestones`, the same way. Tickets are named by `ref` or gid.

`state` on an item moves it the way `./tick.py` does, history and all: `todo`,
`waiting` (with `who`), `hold`, `done`, `sent`, `dropped`. A sweep moves an item
only on an event it can quote; see prompts/refresh.md.

Everything is applied in memory first and saved once, so a patch that names a
ticket the board does not have writes nothing at all. This never sends anything
and never rebuilds the pages: the page redraws itself, and a chat runs
`./tick.py --rebuild`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import board as B
import keep
import settings

STATES = B.OPEN_STATES + B.CLOSED_STATES
_GONE = object()


class PatchError(ValueError):
    pass


def find_ticket(b: dict, key: str) -> dict:
    for t in b.get("tickets", []):
        if t.get("ref") == key or str(t.get("id")) == str(key):
            return t
    refs = ", ".join(t.get("ref", "?") for t in b.get("tickets", []))
    raise PatchError(f"no ticket {key!r} on the board. Known: {refs}")


def _container(node, part: str, create: bool):
    """One step down a dotted path. A list of records is indexed by id."""
    if isinstance(node, list):
        for row in node:
            if isinstance(row, dict) and str(row.get("id")) == part:
                return row
        # A list of records without ids (a milestone's phases) is indexed by position.
        if part.isdigit() and int(part) < len(node):
            return node[int(part)]
        raise PatchError(f"no record with id or position {part!r} in that list")
    if not isinstance(node, dict):
        raise PatchError(f"cannot go into {part!r}: not an object")
    if part not in node or node[part] is None:
        if not create:
            raise PatchError(f"no field {part!r}")
        node[part] = {}
    return node[part]


def set_path(record: dict, path: str, value) -> None:
    append = path.endswith("+")
    parts = path.rstrip("+").split(".")
    node = record
    for part in parts[:-1]:
        node = _container(node, part, create=True)
    last = parts[-1]
    if isinstance(node, list):
        raise PatchError(f"{path}: the last step is a list; name a field on a record in it")
    if append:
        have = node.get(last)
        if have is None:
            have = node[last] = []
        if not isinstance(have, list):
            raise PatchError(f"{path}: {last} is not a list, so it cannot be appended to")
        have.extend(value if isinstance(value, list) else [value])
    elif value is None:
        node.pop(last, None)
    else:
        node[last] = value


def apply(b: dict, patch: dict) -> list[str]:
    said: list[str] = []
    stamp = B.now()

    for t in patch.get("new_tickets") or []:
        if not t.get("id"):
            gid = t.pop("gid", "") or (re.findall(r"\d{5,}", str(t.get("asana_url") or "")) or [""])[-1]
            if gid:
                t["id"] = str(gid)
        if not t.get("id") or not t.get("ref"):
            raise PatchError("a new ticket needs an id (the Asana gid) and a ref")
        if any(str(x.get("id")) == str(t["id"]) or x.get("ref") == t["ref"] for x in b["tickets"]):
            raise PatchError(f"ticket {t['ref']} is already on the board; edit it under tickets")
        t.setdefault("items", [])
        t.setdefault("events", [])
        t.setdefault("threads", [])
        b["tickets"].append(t)
        said.append(f"new ticket {t['ref']}")

    for path, value in (patch.get("board") or {}).items():
        set_path(b, path, value)
        said.append(f"board.{path.rstrip('+')}")

    for ref, fields in (patch.get("tickets") or {}).items():
        t = find_ticket(b, ref)
        for path, value in fields.items():
            if path.rstrip("+") in ("items", "id"):
                raise PatchError(f"{ref}: {path} is not set this way; use items / new_items")
            set_path(t, path, value)
        said.append(f"{t['ref']}: {', '.join(k.rstrip('+') for k in fields)}")

    for ev in patch.get("events") or []:
        ev = dict(ev)
        t = find_ticket(b, ev.pop("ticket", ""))
        if not ev.get("what"):
            raise PatchError(f"an event on {t['ref']} has no 'what'")
        ev.setdefault("on", stamp[:10])
        if "url" in ev and "source_url" not in ev:
            ev["source_url"] = ev.pop("url")
        if ev not in t.setdefault("events", []):
            t["events"].append(ev)
    if patch.get("events"):
        said.append(f"{len(patch['events'])} event(s)")

    for th in patch.get("threads") or []:
        th = dict(th)
        t = find_ticket(b, th.pop("ticket", ""))
        url = th.get("url")
        if not url:
            raise PatchError(f"a thread on {t['ref']} has no url")
        rows = t.setdefault("threads", [])
        row = next((r for r in rows if r.get("url") == url), None)
        if row is None:
            row = {"url": url}
            rows.append(row)
        row.update({k: v for k, v in th.items() if v is not None})
        row["checked"] = stamp
    if patch.get("threads"):
        said.append(f"{len(patch['threads'])} thread(s)")

    for ref, which in (patch.get("checked") or {}).items():
        t = find_ticket(b, ref)
        rows = t.get("threads") or []
        if which not in ("*", [], None, ""):
            subs = [which] if isinstance(which, str) else which
            rows = [r for r in rows if any(s in (r.get("url") or "") for s in subs)]
        for r in rows:
            r["checked"] = stamp
    if patch.get("checked"):
        said.append(f"checked threads on {len(patch['checked'])} ticket(s)")

    for ident, fields in (patch.get("items") or {}).items():
        _, item = B.by_id(b, ident)
        if item is None:
            raise PatchError(f"no item numbered {ident}")
        fields = dict(fields)
        state = fields.pop("state", None)
        who = fields.pop("who", "")
        note = fields.pop("note", "")
        chase = fields.pop("chase_on", None)
        if "id" in fields:
            raise PatchError(f"item {ident}: numbers never change")
        for path, value in fields.items():
            set_path(item, path, value)
        if state:
            if state not in STATES:
                raise PatchError(f"item {ident}: state {state!r} is not one of {', '.join(STATES)}")
            if state == "todo":
                item.pop("waits_on", None)
            B.set_state(item, state, note, who=who)
        if chase:
            if not isinstance(item.get("waits_on"), dict):
                item["waits_on"] = {}
            item["waits_on"]["chase_on"] = chase
        said.append(f"item {ident}" + (f" -> {state}" + (f" ({who})" if who else "") if state else ""))

    for new in patch.get("new_items") or []:
        new = dict(new)
        t = find_ticket(b, new.pop("ticket", ""))
        if not new.get("title"):
            raise PatchError(f"a new item on {t['ref']} has no title")
        new.pop("id", None)
        new["id"] = B.next_id(b)
        new.setdefault("state", "todo")
        new.setdefault("opened", stamp[:10])
        new.setdefault("history", [{"at": stamp, "state": new["state"], "note": "opened"}])
        t.setdefault("items", []).append(new)
        said.append(f"new item {new['id']} on {t['ref']}: {new['title'][:60]}")

    if patch.get("glossary"):
        b.setdefault("glossary", {}).update(patch["glossary"])
        said.append(f"{len(patch['glossary'])} glossary term(s)")

    if patch.get("swept"):
        b["checked_at"] = stamp
        said.append("checked_at = now")

    unknown = set(patch) - {"events", "threads", "checked", "tickets", "items",
                            "new_items", "new_tickets", "board", "glossary", "swept"}
    if unknown:
        raise PatchError(f"unknown top-level key(s): {', '.join(sorted(unknown))}")
    return said


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("patch", help="the JSON patch file")
    ap.add_argument("--dry", action="store_true", help="say what would change, write nothing")
    args = ap.parse_args()

    patch_path = Path(args.patch)
    if not patch_path.exists() and (settings.TMP_DIR / patch_path.name).exists():
        patch_path = settings.TMP_DIR / patch_path.name
    try:
        patch = json.loads(patch_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"could not read the patch: {e}", file=sys.stderr)
        return 1
    b = B.load()
    if not b.get("tickets") and not any(k in patch for k in ("new_tickets", "board", "swept", "glossary")):
        print("no board yet", file=sys.stderr)
        return 1
    try:
        said = apply(b, patch)
        problems = B.check(b)
        if problems:
            raise PatchError("the board would not render:\n  " + "\n  ".join(problems))
        if not args.dry:
            keep.keep("apply")
            B.save(b)
            # Gone once it is on the board, so the next patch is a new file. The
            # Write tool refuses to overwrite a file the run has not read, and a
            # leftover patch once cost a prep six turns working round it.
            patch_path.unlink(missing_ok=True)
    except (PatchError, ValueError) as e:
        print(f"nothing written: {e}", file=sys.stderr)
        return 1
    print(("would apply: " if args.dry else "applied: ") + "; ".join(said))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
