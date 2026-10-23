#!/usr/bin/env python3
"""Shrink what Asana, Slack and Notion hand a sweep, before the model reads it.

A PostToolUse hook, wired in by `agent.Claude.settings()` for refresh, prep and
post. Claude Code pipes it one JSON object per MCP call and shows the model
whatever `updatedMCPToolOutput` says instead of the raw response, so every byte
removed here is a byte that is not re-read on every later turn of the run.

That is where a sweep's time went. One refresh spent 37 seconds in tools
and 1036 in the model, and the model's context grew from 26k to 174k tokens,
most of it payload nobody needed: Asana's thirty custom fields with twenty-five
of them null, reaction and follower stories, profile URLs at the head of every
comment, Notion's list of 25 attendee ids and a citation footnote after every
bullet, and non-ASCII text (Japanese, for one) escaped as \\uXXXX, six
characters a letter.

None of that is information, so nothing a sweep reasons from is lost:

- JSON is re-serialised with real Unicode and no padding.
- Nulls, empty strings and empty lists are dropped.
- Asana custom fields become one `{name: value}` map of the ones that are set.
- Asana system stories that carry no decision (reactions, followers, templates,
  dependency dates) are dropped; status, section, assignee and completion
  changes stay.
- Profile and user links become a short `@id`, Slack's `<url|label>` its label.
- Notion attendee lists become a count, citation footnotes go.

The hook never fails a run. Anything it cannot parse is passed through
untouched, and so is any rewrite that would not be smaller.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import settings

ROOT = settings.REPO
LOG = settings.LOGS_DIR / "compact.log"

# Asana system stories worth a line: they are how a status, section or owner
# moved. Everything else of type "system" is noise to a sweep.
KEEP_SYSTEM = {
    "marked_complete", "marked_incomplete", "assigned", "unassigned",
    "section_changed", "enum_custom_field_changed", "multi_enum_custom_field_changed",
    "text_custom_field_changed", "date_custom_field_changed", "due_date_changed",
    "name_changed", "notes_changed", "added_to_project", "removed_from_project",
    "attachment_added", "comment_added", "mentioned",
}

ASANA_PROFILE = re.compile(r"https://app\.asana\.com/\d+/\d+/profile/(\d+)")
NOTION_CITE = re.compile(r"\[\^https?://[^\]]+\]")
USER_LIST = re.compile(r'"?attendees"?\s*=\s*"[^"]*"', re.I)
SLACK_LINK = re.compile(r"<(https?:[^|>]+)\|([^>]+)>")
SLACK_EMAIL = re.compile(r" <[\w.+-]+@[\w.-]+>")
SLACK_REACTIONS = re.compile(r"^Reactions: .*$\n?", re.M)
EMOJI = re.compile(r"(?<![\w/]):[a-z][a-z0-9_+-]*:(?![\w/])")
BLANKS = re.compile(r"\n{3,}")


def text_clean(s: str) -> str:
    s = s.replace("\\/", "/")
    s = ASANA_PROFILE.sub(lambda m: "@" + m.group(1)[-6:], s)
    s = NOTION_CITE.sub("", s)
    s = USER_LIST.sub("attendees=\"(list)\"", s)
    s = SLACK_LINK.sub(lambda m: m.group(2) if "…" not in m.group(2) else m.group(1), s)
    s = SLACK_EMAIL.sub("", s)
    s = SLACK_REACTIONS.sub("", s)
    s = EMOJI.sub("", s)
    return BLANKS.sub("\n\n", s)


def custom_fields(fields: list) -> dict:
    out = {}
    for f in fields:
        if not isinstance(f, dict):
            continue
        value = f.get("display_value")
        if value not in (None, "", []):
            out[f.get("name") or f.get("gid")] = value
    return out


def clean(node, key: str = ""):
    if isinstance(node, dict):
        if node.get("type") == "system" and node.get("resource_subtype") not in KEEP_SYSTEM:
            return None
        out = {}
        for k, v in node.items():
            if k == "custom_fields" and isinstance(v, list):
                v = custom_fields(v)
            elif k in ("Attendees", "attendees") and isinstance(v, (list, str)):
                n = len(v) if isinstance(v, list) else v.count("user://")
                v = f"{n} people" if n else v
            # A nested person or project with a name needs no gid beside it.
            elif isinstance(v, dict) and "name" in v and set(v) <= {"gid", "name", "resource_type"}:
                v = v["name"]
            else:
                v = clean(v, k)
            if v in (None, "", [], {}) or k in ("resource_type", "Last visited time", "Source"):
                continue
            out[k] = v
        return out
    if isinstance(node, list):
        rows = [clean(x, key) for x in node]
        return [r for r in rows if r not in (None, "", [], {})]
    if isinstance(node, str):
        # Some servers return JSON inside a string; clean that too.
        stripped = node.strip()
        if len(stripped) > 40 and stripped[0] in "{[":
            try:
                return compact_json(json.loads(stripped))
            except ValueError:
                pass
        return text_clean(node)
    return node


def compact_json(obj) -> str:
    return json.dumps(clean(obj), ensure_ascii=False, separators=(",", ":"))


def compact_text(raw: str) -> str:
    try:
        return compact_json(json.loads(raw))
    except ValueError:
        return text_clean(raw)


def response_text(resp) -> str:
    """The tool's answer as one string, whatever envelope the server used."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        return "\n".join(
            b.get("text", "") for b in resp if isinstance(b, dict) and b.get("type") == "text"
        )
    if isinstance(resp, dict):
        if isinstance(resp.get("content"), list):
            return response_text(resp["content"])
        return json.dumps(resp, ensure_ascii=False)
    return ""


def log(tool: str, before: int, after: int) -> None:
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{tool}\t{before}\t{after}\n")
    except OSError:
        pass


def main() -> int:
    try:
        event = json.loads(sys.stdin.read())
        tool = str(event.get("tool_name", ""))
        if not tool.startswith("mcp__"):
            return 0
        raw = response_text(event.get("tool_response"))
        if not raw:
            return 0
        small = compact_text(raw)
        log(tool, len(raw), len(small))
        if len(small) >= len(raw):
            return 0
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedMCPToolOutput": small,
        }}, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - a hook that raises would cost the sweep
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
