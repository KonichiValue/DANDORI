#!/usr/bin/env python3
"""Everything a sweep needs to read off the board, in one call.

A refresh used to open `state/board.json` fifteen times: a one-liner to list the
tickets, another for the threads, another to remember what an item said, a dump
into `/tmp` and a read back out of it. Each of those is a round trip worth
several seconds, and together they were most of an eight minute refresh, none of
it spent talking to Asana or Slack.

So this prints the whole readable half of the board at once: what is on it, what
is open, where each ticket stands, which threads to read and when each was last
read. `--full` unclips the prose of the tickets with open work, which is what a
prep writes from. `--everything` adds closed items, every event and the finished
tickets, for the rare sweep that needs them.

It never writes. The writing half is one patch file and `./apply.py`, and the
runner rebuilds the pages.

The default print keeps itself under BUDGET characters by tightening its clips
as the board grows. Over about 30k the harness persists a tool result to a file
and the model spends a turn reading it back, which is the round trip this file
exists to remove.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import audit
import board as B
import prioritize
import calendar_sync
import settings

WRAP = 160
ROOT = Path(__file__).resolve().parent
BUDGET = 27_000
SCALES = (1.0, 0.8, 0.65, 0.5, 0.4)
_scale = 1.0
_lines: list[str] = []


def _project_gids() -> list[str]:
    """The client-shared Asana project gids the gate query runs against, from config.json."""
    return [p["gid"] for p in settings.section("asana").get("projects") or []
            if isinstance(p, dict) and p.get("gid")]


def _age(stamp: str) -> str:
    """How stale the board is, in the words the owner would use out loud."""
    if not stamp:
        return "never"
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    secs = (datetime.now().astimezone() - then).total_seconds()
    if secs < 0:
        return "in the future"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m ago"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h {mins}m ago"
    return f"{hours // 24}d {hours % 24}h ago"


def _clip(text: object, limit: int, full: bool = False) -> str:
    s = " ".join(str(text or "").split())
    if not full:
        limit = max(30, int(limit * _scale))
    if full or len(s) <= limit:
        return s
    return s[:limit].rstrip() + f"... (+{len(s) - limit} chars)"


def _out(line: str = "") -> None:
    _lines.append(line)


def _ticket_head(t: dict, full: bool, everything: bool = False) -> None:
    ref = t.get("ref") or t.get("id")
    asana = t.get("asana") or {}
    _out(f"=== {ref}   gid {t.get('id', '?')} ===")
    _out(f"    title_ja: {_clip(t.get('title_ja'), 90, full)}")
    if full:
        _out(f"    title_en: {_clip(t.get('title_en'), 90, full)}")
    if t.get("asana_url") and full:
        _out(f"    url: {t['asana_url']}")
    if asana:
        bits = [
            f"{k}={asana[k]}"
            for k in ("status", "section", "priority", "severity", "assignee", "completed")
            if asana.get(k) not in (None, "")
        ]
        if bits:
            _out("    asana: " + "  ".join(bits))
    if t.get("no_ticket_yet"):
        _out(f"    NO TICKET YET: {_clip(t['no_ticket_yet'], 120, full)}")

    it = t.get("internal_ticket") or {}
    if it.get("none_yet"):
        _out(f"    internal: none, because {_clip(it['none_yet'], 110, full)}")
    elif it:
        bld = it.get("build") or {}
        bits = [f"{k}={bld[k]}" for k in ("kt", "stage", "engineer", "size") if bld.get(k)]
        _out(f"    internal: {it.get('name', '?')} {' '.join(bits)}")
        if bld.get("waiting_on"):
            _out(f"      waiting_on: {_clip(bld['waiting_on'], 110 if full else 80, full)}")

    la = t.get("last_activity") or {}
    if la:
        _out(f"    last_activity: {la.get('at', '?')} {la.get('who', '')} ({la.get('where', '')})")
    _out(f"    where_it_stands: {_clip(t.get('where_it_stands'), 400 if full else 180, full)}")

    gates = t.get("closes_when") or []
    # A gate already passed changes nothing a sweep does; only --full prints it.
    shown_gates = gates if full else [g for g in gates if g.get("state") != "done"]
    if shown_gates:
        _out(f"    closes_when ({len(gates)}, {len(gates) - len(shown_gates)} done hidden):"
             if not full else f"    closes_when ({len(gates)}):")
        for g in shown_gates:
            who = f" [{g['who']}]" if g.get("who") else ""
            _out(f"      ({g.get('state', '?'):<7}){who} {_clip(g.get('what'), 100, full)}")
            if full and g.get("note"):
                _out(f"          note: {_clip(g['note'], 200, True)}")

    threads = t.get("threads") or []
    if threads and full:
        _out(f"    threads ({len(threads)}), read the ones with live discussion:")
        for th in threads:
            last = f"{th.get('last_from', '?')} @ {th.get('last_at', '?')}"
            _out(f"      - [{th.get('where', '?')}] {_clip(th.get('label'), 60, full)} | last: {last}")
            _out(f"        {th.get('url', '')}")
            if full and th.get("gist"):
                _out(f"        gist: {_clip(th['gist'], 200, True)}")

    events = t.get("events") or []
    if events:
        # Twelve is two cycles of a busy ticket, which is as far back as a
        # script or a draft ever reaches. The busiest ticket once had 104.
        shown = events if everything else events[-12:] if full else events[-2:]
        _out(f"    events ({len(events)} total, showing {len(shown)}):")
        for e in shown:
            _out(
                f"      - {e.get('on', '?')} {e.get('at', '')} {e.get('who', '?')}: "
                f"{_clip(e.get('what'), 130 if full else 90, full)}"
            )


def _item_line(i: dict, full: bool) -> None:
    state = i.get("state", "todo")
    mark = {"todo": " ", "waiting": "~", "hold": "H", "done": "x", "sent": "x", "dropped": "-"}
    flag = mark.get(state, "?")
    waits = i.get("waits_on") or {}
    tail = f" -> {waits.get('who')}" if waits.get("who") else ""
    _out(
        f"      [{flag}] {str(i.get('id', '?')):>3}  {state:<8} {i.get('urgency', '-'):<10}"
        f" {i.get('est_minutes', '?')}m  {_clip(i.get('title'), 90, full)}{tail}"
    )
    meta = []
    if i.get("after"):
        meta.append(f"after={i['after']}")
    if i.get("at_standup"):
        meta.append("at_standup")
    if i.get("hold"):
        meta.append(f"HOLD: {_clip(i['hold'], 70, full)}")
    d = i.get("draft")
    if d:
        meta.append(f"draft={d.get('language', '?')}->{_clip(d.get('target'), 40, full)}")
    p = i.get("prepared")
    if p:
        meta.append(
            f"prepared({len(p.get('findings', []))} findings,"
            f" {len(p.get('files', []))} files, built {p.get('built_at', '?')})"
        )
    if meta:
        _out("            " + " | ".join(meta))
    if i.get("where") and full:
        _out(f"            where: {_clip(i.get('where'), 90, full)}")
    if i.get("where_it_stands"):
        _out(f"            stands: {_clip(i.get('where_it_stands'), 200 if full else 110, full)}")
    steps = i.get("steps") or []
    for n, s in enumerate(steps if full else steps[:2], 1):
        ticked = " [ticked done by the owner]" if B.step_done(i, s) else ""
        _out(f"            step {n}{ticked}: {_clip(s, 150 if full else 80, full)}")
    if not full and len(steps) > 2:
        _out(f"            (+{len(steps) - 2} more; --item {i.get('id')})")
    if i.get("done_when"):
        _out(f"            done_when: {_clip(i.get('done_when'), 150 if full else 80, full)}")
    if i.get("progress_note"):
        # The newest bracketed entry is the one a sweep needs; the history is
        # what --full is for.
        note = str(i["progress_note"])
        if not full and "[" in note:
            note = note[note.rfind("["):]
        _out(f"            progress: {_clip(note, 200 if full else 130, full)}")
    if i.get("state_note"):
        _out(f"            state_note: {_clip(i.get('state_note'), 120 if full else 70, full)}")


SLACK_LIVE_DAYS = 30


def _slack_ts(url: str) -> tuple[str, str] | None:
    """(channel, parent ts) from a Slack link, the two things read_thread takes.

    A reply's link carries the parent in `thread_ts`; a parent's link carries it
    in the `p` segment, with the decimal point taken out.
    """
    m = re.search(r"slack\.com/archives/([A-Z0-9]+)/p(\d{10})(\d{6})", url or "")
    if not m:
        return None
    parent = re.search(r"thread_ts=(\d{10}\.\d{6})", url)
    return m.group(1), parent.group(1) if parent else f"{m.group(2)}.{m.group(3)}"


def _slack_oldest(last_at: str) -> str:
    """A thread's recorded `last_at` (the owner's wall time) as a Slack ts, a minute early."""
    try:
        when = datetime.fromisoformat(str(last_at).strip().replace(" ", "T"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=settings.tz())
    return f"{when.timestamp() - 60:.6f}"


def _slack_plan(b: dict, checked: str) -> None:
    """Every live Slack thread, read every sweep, from the last sweep on.

    Asana has a gate: a comment moves `modified_at`, so the census and the gate
    find it. Slack has none. A reply in a thread moves nothing on the board, so a
    rule that skipped threads "looked at since the ticket last moved" skipped
    them for good: the ticket only moves when a sweep sees the reply. That is how
    two replies on one job, one of them the owner's own, were once missed. So a Slack thread on a
    ticket with open work is read whenever it has spoken in the last month, with
    `oldest` set to the last sweep so a quiet one costs a few lines.
    """
    me = settings.section("owner").get("slack_user_id", "")
    try:
        day = datetime.fromisoformat(checked).date()
    except ValueError:
        day = None
    _out("  S. Slack, in the same message as 0, 1 and M. No thread here is skippable: Slack has")
    _out("     no gate, so a reply moves nothing until a sweep reads it.")
    after = ((day - timedelta(days=1)) if day else (datetime.now().date() - timedelta(days=14))).isoformat()
    if me:
        _out(f"     search from:<@{me}> after:{after}  = everything the owner sent, so their own sends land too.")
        _out(f"     search keywords=[\"<@{me}>\"] after:{after}  = everything that mentions the owner.")
    cut = (day or datetime.now().date()) - timedelta(days=SLACK_LIVE_DAYS)
    seen: set = set()
    old: set[str] = set()
    for t in b.get("tickets", []):
        if not any(B.is_open(i) for i in t.get("items") or []):
            continue
        links = [th.get("url", "") for th in t.get("threads") or []]
        links += [(i.get("draft") or {}).get("link", "") for i in t.get("items") or [] if B.is_open(i)]
        lasts = {th.get("url", ""): th.get("last_at", "") for th in t.get("threads") or []}
        for url in links:
            key = _slack_ts(url)
            if not key or key in seen:
                continue
            seen.add(key)
            last = audit.as_date(lasts.get(url)) or datetime.fromtimestamp(float(key[1])).date()
            if last < cut:
                old.add(key[0])
                continue
            # From the newest message the board has recorded, not from the last
            # sweep: a sweep that missed a reply must not move the window past it.
            oldest = _slack_oldest(lasts.get(url, ""))
            _out(f"     read_thread channel_id={key[0]} message_ts={key[1]}"
                 + (f" oldest={oldest}" if oldest else "")
                 + f"  ({t.get('ref')}, last {lasts.get(url) or '?'})")
    # A thread quiet for a month is not read whole, but its channel is searched,
    # so a reply that wakes it is still found.
    for channel in sorted(old):
        _out(f"     search in:<#{channel}> after:{after}  = wakes any thread here quiet over {SLACK_LIVE_DAYS} days")
    _out("     A reply in any of these is an event; the owner's own message is an event \"You\" (section 5).")
    _out("     Put each read thread's newest message in the patch's \"threads\" (last_at, last_from).")


def _sweep_plan(b: dict) -> None:
    """The worklist for a sweep, made from the watermarks rather than reasoned out.

    A sweep used to work out what to read by dumping the board through a dozen
    one-liners: which tickets, which threads, from when. All of that is already on
    the board, so this prints it as instructions the sweep can follow without
    opening `state/board.json` at all. Read exactly what this names, from the
    timestamps it names, and nothing turns into reconnaissance.
    """
    checked = b.get("checked_at", "")
    since = checked[:10] if checked else (datetime.now().date() - timedelta(days=14)).isoformat()
    gids = _project_gids()
    _out("SWEEP PLAN  (built from the watermarks; read this, not the raw board)")
    proj = f" projects={','.join(gids)}" if gids else ""
    _out("  Make calls 0, 1, M and every S together, in ONE message: they do not depend on each other.")
    _out(f"  0. Census, NO date filter: search_tasks assignee=me completed=false{proj}")
    _out("     = what BELONGS on the board. An assigned incomplete gid not below is MISSING: add it, deep-read it.")
    _out(f"  1. Gate: search_tasks modified_at.after={since or '?'}{proj} opt_fields=name,modified_at,assignee.name,completed")
    _out("     = what to deep-read (get_task + get_task_stories, in parallel): its gids, census additions,")
    _out("     and tickets with a thread listed in 2. Nothing else; an unchanged known ticket costs nothing.")
    for ms in b.get("milestones") or []:
        if ms.get("status") != "done":
            _out(f"  M. Milestone {ms.get('id')}: search_tasks text=\"{ms.get('name_ja') or ms.get('name')}\""
                 f" completed=false{proj} opt_fields=name,assignee.name,modified_at,completed")
            _out("     = its child tickets. Update the milestone's tickets/phases rows; a child assigned to")
            _out("     the owner becomes a ticket card too (new_tickets), anything else stays a row on the milestone.")
    _slack_plan(b, checked)
    _out("  2. Other threads to read (not looked at since the ticket last moved); mark them in the patch's \"checked\".")
    quiet_refs: list[str] = []
    for t in b.get("tickets", []):
        threads = [th for th in t.get("threads") or [] if not _slack_ts(th.get("url", ""))]
        if not threads:
            continue
        events = t.get("events") or []
        newest = max(
            (audit.as_date(e.get("on")) for e in events if audit.as_date(e.get("on"))),
            default=None,
        )
        need, quiet = [], 0
        for th in threads:
            looked = max(
                (d for d in (audit.as_date(th.get("checked")), audit.as_date(th.get("last_at"))) if d),
                default=None,
            )
            if newest is not None and looked is not None and looked >= newest:
                quiet += 1
            else:
                need.append(th)
        if not need:
            quiet_refs.append(t.get("ref", "?"))
            continue
        _out(f"     {t.get('ref')}:" + (f"  ({quiet} quiet, skip)" if quiet else ""))
        for th in need:
            _out(
                f"       - [{th.get('where', '?')}] from {th.get('last_at', '?')}"
                f"  {th.get('url', '')}"
            )
    if quiet_refs:
        _out(f"     every thread looked at since the last event, skip: {' '.join(quiet_refs)}")
    _out("  3. Build tickets, re-read only if the gate flags them:")
    any_build = False
    for t in b.get("tickets", []):
        bld = (t.get("internal_ticket") or {}).get("build") or {}
        if bld.get("kt"):
            any_build = True
            _out(
                f"       - {bld['kt']} ({t.get('ref')}) stage={bld.get('stage', '?')}"
                f" moved_on={bld.get('moved_on', '?')}"
            )
    if not any_build:
        _out("       - none on the board")
    # Sent, marked by the owner's own press since the last sweep. The mark stands; the
    # sweep only confirms it against the thread, the way a read receipt would.
    marked = []
    for t in b.get("tickets", []):
        for i in t.get("items") or []:
            last = (i.get("history") or [{}])[-1]
            if (i.get("state") == "waiting" and i.get("sent_by_you")
                    and last.get("state") == "waiting" and str(last.get("at", "")) > checked):
                marked.append((t, i, last))
    if marked:
        _out("  4. Marked sent since the last sweep: find the owner's message in the thread (it is in")
        _out("     2's reads, or the draft link). Answered: item back to todo, per section 6.")
        _out("     Not found: say so in the item's state \"note\", never move it back yourself.")
        for t, i, last in marked:
            link = (i.get("draft") or {}).get("link", "")
            _out(f"       - {i.get('id')} ({t.get('ref')}) -> {(i.get('waits_on') or {}).get('who', '?')}"
                 f" at {str(last.get('at', ''))[:16]}  {link}")
    # Meetings that ended with Gemini notes attached and no note record on the
    # board yet, read from the calendar the desk keeps. This is how a room the
    # owner sat in reaches the board without them pasting anything.
    _prioritization_plan(b)
    unread = calendar_sync.unread_notes(b)
    if unread:
        _out("  N. Meeting notes nobody has read: drive_read_file each doc, in the same message as 0/1/S.")
        _out("     Fold what was said into the tickets it touches (events, items), then add one")
        _out("     other_notes row per meeting {date, title, url, found, gist} (board \"other_notes+\"), or set")
        _out("     migration_note for one of the programme-wide meetings in config.json meeting.also.")
        _out("     A meeting with nothing for the owner's tickets still gets its row, gist")
        _out("     \"nothing for the owner's tickets\", so it stops showing.")
        for e in unread[:8]:
            doc = next((n for n in e.get("notes") or [] if calendar_sync.GEMINI.search(n.get("title", ""))), {})
            _out(f"       - {e['date']} {e['at']} {e['title'][:60]}  {doc.get('url', '')}")
    _out()


def _prioritization_plan(b: dict) -> None:
    """Step P: the owner's CE builds, read fresh for the prioritization round.

    Only when `config.json` has a `prioritization` block; with none, step P is
    not printed at all. Asana ignores a CDL-Owner filter on search (it once came
    back with every CDL's builds), and the CE projects read whole are 120k
    characters. So the builds already known are read by gid, new ones are found
    by creation date, and ownership is checked on the field itself. CDL-Owner is
    the only rule: another CDL's build never goes in, however close to the
    owner's tickets it sits.
    """
    if not settings.prioritization():
        return
    cfg = prioritize.config()
    if not cfg:
        return
    block = b.get("prioritization") or {}
    nxt = prioritize.next_round(cfg)
    since = (block.get("checked_at") or "")[:10] or "2026-09-01"
    _out(f"  P. Prioritization: next thread {nxt.get('date', '?')} {nxt.get('at', '')},"
         f" raise by {nxt.get('ask_by', '?')}. In the same message as 0/1/S:")
    gids = [x.get("gid") for x in block.get("builds") or [] if x.get("gid")]
    if gids:
        _out(f"     get_task each (include_comments=false): {' '.join(gids)}")
    _out(f"     search_tasks projects_any={','.join(cfg.get('ce_projects') or [])} created_on_after={since}"
         " completed=false opt_fields=name,due_on,completed,custom_fields.gid,custom_fields.display_value")
    owner = settings.owner_name() or "the owner (config.json owner.name)"
    _out(f"     Keep only CDL-Owner ({cfg.get('cdl_owner_field')}) = {owner}. Write the whole of")
    _out("     board \"prioritization.builds\" (section 7c), and \"prioritization.checked_at\" = now.")
    ran = prioritize.previous(cfg).get("date") or ""
    if ran and (block.get("last_round") or {}).get("date", "") < ran:
        _out(f"     A round has run: read {cfg.get('who')}'s thread in {cfg.get('channel')} ({cfg.get('channel_id')})")
        _out("     and set prioritization.last_round {date, url, ordered, not_pushed} for the owner's builds only.")


CHEATSHEET = """WRITE  (the whole of a sweep's writing -- no python, no help(board), no board.json dump)

Write ONE patch file with the Write tool, then apply it with ONE command:

  Write  tmp/patch.json           (inside this folder, outside state/, .json never .py)
  ./core/apply.py tmp/patch.json  (add --dry first only if unsure; a clean return is the confirmation)

  {"events":  [{"ticket": "HOLD-RELEASE", "on": "2026-10-07", "at": "12:08", "who": "Alex Kim, Kraken CE",
                "what": "...", "so_what": "...", "where": "Slack", "source_url": "https://..."}],
   "checked": {"HOLD-RELEASE": "*", "INVOICE-REPRINT": ["slack"]},   threads looked at (all, or url contains)
   "threads": [{"ticket": "...", "url": "...", "last_at": "2026-10-07 12:08", "last_from": "Alex"}],
   "items":   {"32": {"state": "todo", "note": "Alex replied 12:08",            moves like ./core/tick.py
                      "draft.body_ruby": "...", "progress_note": "...", "steps": ["..."]},
                      draft.reply_from / draft.expects_reply: false say what Posted it does;
                      never a "./core/tick.py N" step, the page has a button for it
               "45": {"state": "waiting", "who": "Sato-san, client", "chase_on": "2026-10-09"}},
   "new_items": [{"ticket": "...", "title": "...", "why": "...", "steps": ["..."],
                  "done_when": "...", "urgency": "today"}],        numbered for you
   "new_tickets": [{"id": "1200...", "ref": "SHORT-TAG", "title_ja": "...", "title_en": "...",
                    "asana_url": "https://app.asana.com/0/.../...",
                    "asana": {"status": "...", "section": "...", "assignee": "...", "completed": false},
                    "where_it_stands": "...", "closes_when": [{"what": "...", "done": false}]}],
   "tickets": {"HOLD-RELEASE": {"where_it_stands": "...", "asana.status": "...", "closes_when": [...]}},
   "board":   {"news": [...whole list, five or so...], "brief": {...}, "alert": null,
               "milestones.price-update.phases.1.state": "done",
               "milestones.price-update.items+": [58]},
   "glossary": {"<client term>": "open the day after close"},
   "swept": true}                                             sets checked_at = now, once, last

  A dotted key reaches inside; "key+" appends to a list; null removes. A list of records with
  ids (milestones) is addressed by id, any other list by position. Every key is optional.
  apply.py saves through board.save, which locks, merges with an ask that wrote meanwhile,
  keeps prepared.earlier for you, and refuses a shape no renderer survives. So: never
  Edit/sed/json.dump board.json, never delete an item (state "dropped" with a note).

EVERY JOB NEEDS A DAY: urgency is exactly today | this-week | monitor (hyphen), or
  waits_on.chase_on is an ISO date (a condition goes in waits_on.what).

NEED A RECORD WHOLE (a stale prepared block, a draft to rewrite)?  ./core/digest.py --item 42 52
  or --ticket HOLD-RELEASE prints it as JSON, which is also its shape. Never read output/ or *.py for it.
SHAPES (all a sweep writes; no need to look further):
  draft    = {language: "ja"|"en", target: "who and where", link, body_ruby: ja text with {word|reading},
              body_en: en text (or the translation under a ja one)}        null removes it
  prepared = {what, conclusion, findings:[...], notes:[...], unanswered:[...], files:[...],
              sources:[{label, url}], built_at}       rewrite whole; save() keeps what you cut
  news row = {topic, what, why, on, source_url}       why = the route to one of the owner's tickets
  brief    = {for_date, built_at, lines:[{kind: moved|do|watch|room|big, what, items:[n], ref, source_url, at}]}

Do not rebuild: the runner redraws both pages when the run ends."""


def _whole(b: dict, items: list[str], tickets: list[str]) -> int:
    """One record exactly as the board holds it, which is also its shape.

    A sweep fixing a stale `prepared` block needs that block whole, and without
    this it went to output/desk.md and board.py with grep and awk for it: five
    turns where one call does. The bookkeeping (`history`, `earlier`) stays out,
    because nothing a sweep writes needs it and it is most of the bytes.
    """
    skip = {"history", "earlier"}

    def trim(node):
        if isinstance(node, dict):
            return {k: trim(v) for k, v in node.items() if k not in skip}
        if isinstance(node, list):
            return [trim(v) for v in node]
        return node

    for ident in items:
        t, item = B.by_id(b, ident)
        if item is None:
            _out(f"no item numbered {ident}")
            continue
        _out(f"ITEM {ident} on {t.get('ref')}:")
        _out(json.dumps(trim(item), ensure_ascii=False, indent=1))
    for ref in tickets:
        t = next((x for x in b.get("tickets", []) if x.get("ref") == ref or str(x.get("id")) == ref), None)
        if t is None:
            _out(f"no ticket {ref}")
            continue
        body = {k: v for k, v in t.items() if k not in ("items", "events")}
        _out(f"TICKET {ref} (items and events left out; --item N for an item):")
        _out(json.dumps(trim(body), ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--full", action="store_true",
                    help="no truncation, for the tickets with open work")
    ap.add_argument("--everything", action="store_true",
                    help="--full, plus closed items and finished tickets")
    ap.add_argument("--items-only", action="store_true", help="just the work, no ticket context")
    ap.add_argument("--item", nargs="+", metavar="N",
                    help="print these items whole, as JSON: prepared, draft and all")
    ap.add_argument("--ticket", nargs="+", metavar="REF",
                    help="print these tickets whole, as JSON, without their items")
    args = ap.parse_args()
    full = args.full or args.everything

    global _scale
    b = B.load()
    if args.item or args.ticket:
        code = _whole(b, args.item or [], args.ticket or [])
        print("\n".join(_lines))
        return code
    # --full is meant to be long; only the default print is held to the budget.
    for _scale in SCALES if not full else (1.0,):
        _lines.clear()
        code = _board(b, args, full)
        if sum(len(x) + 1 for x in _lines) <= BUDGET:
            break
    print("\n".join(_lines))
    return code


def _board(b: dict, args: argparse.Namespace, full: bool) -> int:
    all_items = B.items(b)
    open_items = [(t, i) for t, i in all_items if B.is_open(i)]
    by_state: dict[str, int] = {}
    for _, i in all_items:
        by_state[i.get("state", "todo")] = by_state.get(i.get("state", "todo"), 0) + 1

    _out("BOARD DIGEST  (state/board.json, read-only)")
    _out(f"checked_at: {b.get('checked_at', 'never')}  ({_age(b.get('checked_at', ''))})")
    _out(f"next_id: {b.get('next_id')}   version: {b.get('version')}")
    _out(
        f"counts: {len(b.get('tickets', []))} tickets, {len(all_items)} items"
        f" ({len(open_items)} open) | " + ", ".join(f"{k}={v}" for k, v in sorted(by_state.items()))
    )
    id_counts = Counter(str(i.get("id")) for _, i in all_items)
    dupes = [ident for ident, n in id_counts.items() if n > 1]
    if dupes:
        _out(f"!! DUPLICATE ITEM NUMBERS: {sorted(dupes)} -- fix before writing")
    _out()

    # What the last sweep left behind, printed before anything else it might read.
    # A sweep is good at finding what moved and bad at noticing what it did not go
    # back and change, and every line here is the second kind: a date that has
    # passed, two records that disagree, a thread whose watermark is older than the
    # events taken out of it. That last one is why a sweep can run clean and still
    # miss things, because the next one reads forward from the stale mark.
    rep = audit.Report()
    for check in audit.CHECKS:
        check(b, rep)
    if rep.total:
        _out(f"OUT OF DATE ({rep.total}) -- fix these in this sweep, they are not warnings")
        for group, lines in rep.groups.items():
            _out(f"  {group}:")
            for line in lines:
                _out("  " + line)
        _out("  Run ./core/audit.py when you are done. It should print nothing.")
        _out()

    if not args.items_only:
        _sweep_plan(b)

    if not args.items_only:
        sessions = b.get("sessions") or []
        script = b.get("script") or {}
        _out(f"SESSIONS ({len(sessions)}, soonest first)   script.for_date={script.get('for_date', '-')}")
        for s in sessions:
            skip = "  SKIPPED: " + str(s.get("reason", "")) if s.get("skipped") else ""
            _out(
                f"  {s.get('date', '?')} {s.get('at', ''):<6} {s.get('kind', '?'):<9}"
                f" {_clip(s.get('label') or s.get('title'), 70, full)}{skip}"
            )
            for row in s.get("timetable") or []:
                if row.get("mine") or full:
                    star = "*" if row.get("mine") else " "
                    _out(f"      {star} {row.get('at', '?')} {_clip(row.get('what'), 100, full)}")
        _out()

        alert = b.get("alert") or {}
        if alert.get("what"):
            _out(f"ALERT: {_clip(alert['what'], 300, full)}")
            _out(f"       {alert.get('source_url', '')}")
            _out()

        news = b.get("news") or []
        _out(f"NEWS ({len(news)})")
        for n in news:
            _out(f"  - {n.get('on', '?')} {n.get('topic', '')}")
            _out(f"    {_clip(n.get('what'), 240 if full else 90, full)}")
            if n.get("why"):
                _out(f"    reaches you: {_clip(n['why'], 200 if full else 70, full)}")
        _out()

        for key in ("meeting_note", "migration_note"):
            note = b.get(key) or {}
            if note:
                _out(
                    f"{key}: date={note.get('date', '-')} found={note.get('found', '-')}"
                    f" {note.get('url', '')}"
                )
        others = b.get("other_notes") or []
        for note in others if full else others[-2:]:
            _out(f"other_note: {note.get('date', '-')} {_clip(note.get('title'), 60, full)}")
        if len(others) > 2 and not full:
            _out(f"other_note: +{len(others) - 2} older (--full)")
        _out(f"glossary: {len(b.get('glossary') or {})} terms")
        _out()

        for ms in b.get("milestones") or []:
            phases = ms.get("phases") or []
            _out(f"MILESTONE {ms.get('id')}: {ms.get('name')} target={ms.get('target')}"
                 f" status={ms.get('status')} items={ms.get('items') or []}")
            for n, ph in enumerate(phases, 1):
                _out(f"  phases.{n - 1} ({ph.get('state', 'next'):<7}) {_clip(ph.get('what'), 90, full)}"
                     + (f" [{ph['who']}]" if ph.get("who") else ""))
            # Position and gid on every row, so a sweep can match a search hit to
            # it and address it as milestones.<id>.tickets.<n>.state without
            # going back to the raw board for either.
            for n, r in enumerate(ms.get("tickets") or []):
                gid = str(r.get("asana_url") or "").rstrip("/").rsplit("/", 1)[-1]
                _out(f"  tickets.{n} gid={gid or 'none'} ref={r.get('ref') or 'none'}"
                     f" assignee={r.get('assignee') or 'none'} state={r.get('state') or '?'}"
                     f" | {_clip(r.get('title'), 60, full)}")
        if b.get("milestones"):
            _out()

    _out("TICKETS AND WORK")
    _out()
    quiet: list[str] = []
    for t in b.get("tickets", []):
        # A ticket with nothing of the owner's left open is finished work. Printed whole
        # it was 170KB of the 235KB --full digest, and once the model spent
        # most of a 34-minute prep re-reading it on every turn. One line keeps
        # it findable; --everything prints it for the rare sweep that needs it.
        if not args.everything and not any(B.is_open(i) for i in t.get("items", [])):
            status = (t.get("asana") or {}).get("status", "?")
            quiet.append(f"{t.get('ref')} {t.get('id', '?')} ({status})")
            continue
        if not args.items_only:
            _ticket_head(t, full, args.everything)
        else:
            _out(f"=== {t.get('ref')} ===")
        rows = [i for i in t.get("items", []) if args.everything or B.is_open(i)]
        closed = len(t.get("items", [])) - len(rows)
        _out(f"    items ({len(rows)} shown{'' if args.everything else f', {closed} closed hidden'}):")
        for i in rows:
            _item_line(i, full)
        _out()

    if quiet:
        _out("NOTHING OPEN (one line each; --everything prints them): " + "; ".join(quiet))
        _out()
    _out(CHEATSHEET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
