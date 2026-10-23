#!/usr/bin/env python3
"""A milestone the owner leads, drawn the way Jira draws an epic.

A milestone is bigger than one ticket and owns no work of its own, so the card is
a roll-up rather than another to-do list: the target date and how far off it is,
a bar for how much of its work is closed, the phases between here and done in
order, and every ticket under it. A ticket that is the owner's links to its card
further down; one that is somebody else's is a row they follow, never a card,
because the board is their to-do list and not the programme's.

The counts come from the items themselves, so the bar cannot claim more than the
board does. `board.py` documents the shape.
"""

from __future__ import annotations

from datetime import date

import board as B
import settings
from render import day_words, esc, esc_url, item_state, link_btn, ticket_tags

STATUS = {
    "on-track": ("On track", "green"),
    "at-risk": ("At risk", "amber"),
    "off-track": ("Off track", "red"),
    "done": ("Done", "green"),
}
PHASE = {"done": "Done", "now": "Now", "next": "Next", "blocked": "Blocked"}


def days_left(iso: str) -> str:
    try:
        left = (date.fromisoformat(iso) - date.today()).days
    except (TypeError, ValueError):
        return ""
    if left < 0:
        return f"{-left} days late"
    if left == 0:
        return "due today"
    weeks = left // 7
    return f"{left} days left" + (f" ({weeks} wk)" if weeks >= 2 else "")


def counts(ms: dict, index: dict[str, tuple[dict, dict]]) -> tuple[int, int]:
    states = [item_state(index[str(n)][1]) for n in ms.get("items") or [] if str(n) in index]
    return sum(1 for s in states if s["closed"]), len(states)


def due_text(iso: str) -> str:
    """A phase's due day as the owner would say it, with how far off it is."""
    try:
        date.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    left = days_left(iso)
    return left if left == "due today" else f"due {day_words(iso)}, {left}"


def job_href(n, index: dict[str, tuple[dict, dict]], refs: dict[str, str]) -> str:
    """The ticket card a job lives on; base.js opens the job itself on arrival."""
    t, _ = index[str(n)]
    return f'href="#{esc(refs.get(t.get("ref", ""), ""))}" data-job-link="{esc(n)}"'


def job_link(n, index: dict[str, tuple[dict, dict]], refs: dict[str, str]) -> str:
    """An item number that goes to the job and wears its live state."""
    if str(n) not in index:
        return f'<span class="ms-num">{esc(n)}</span>'
    _, item = index[str(n)]
    st = item_state(item)
    return (
        f'<a class="ms-num {esc(st["tone"])}" {job_href(n, index, refs)} '
        f'title="{esc(item.get("title", ""))} ({esc(st["label"])})">{esc(n)}</a>'
    )


def phase_row(p: dict, n: int, index: dict[str, tuple[dict, dict]] | None = None,
              refs: dict[str, str] | None = None) -> str:
    index, refs = index or {}, refs or {}
    state = p.get("state", "next")
    when = f'<span class="ms-when">{esc(p["when"])}</span>' if p.get("when") else ""
    due = due_text(p.get("due", "")) if state != "done" else ""
    due = f'<span class="ms-pdue">{esc(due)}</span>' if due else ""
    who = f'<span class="ms-who">{esc(p["who"])}</span>' if p.get("who") else ""
    nums = "".join(job_link(i, index, refs) for i in p.get("items") or [])
    note = f'<span class="ms-note">{esc(p["note"])}</span>' if p.get("note") else ""
    return (
        f'<li class="ms-ph {esc(state)}"><span class="ms-dot">{n}</span>'
        f'<span class="ms-body"><span class="ms-what">{esc(p.get("what", ""))}</span>'
        f'<span class="ms-meta"><span class="ms-st">{PHASE.get(state, state)}</span>{who}{when}{due}{nums}</span>'
        f"{note}</span></li>"
    )


def live(phases: list[dict]) -> list[tuple[int, dict]]:
    """Every phase being worked now, in order. Two tracks can run at once."""
    return [(n, p) for n, p in enumerate(phases, 1) if p.get("state") in ("now", "blocked")]


def next_move(item: dict) -> str:
    """The one line that says what happens next on a job."""
    st = item_state(item)
    if st["closed"]:
        return st["label"]
    if st["state"] == "waiting":
        return f"Waiting on {st['who'] or 'someone else'}"
    steps = B.open_steps(item)
    if steps:
        return f"Next: {steps[0]}"
    return "Every step ticked off" if item.get("steps") else "No steps written yet"


def now_panel(phases: list[dict], index: dict[str, tuple[dict, dict]], refs: dict[str, str]) -> str:
    """What is moving the milestone today: the phase, who acts, by when, and
    the job behind it with its next step, so the card answers "what now"
    without opening anything."""
    rows = ""
    for n, p in live(phases):
        jobs = ""
        for i in p.get("items") or []:
            if str(i) not in index:
                continue
            _, item = index[str(i)]
            st = item_state(item)
            jobs += (
                f'<a class="ms-nj" {job_href(i, index, refs)}><span class="ms-num {esc(st["tone"])}">{esc(i)}</span>'
                f'<span class="ms-nj-t">{esc(item.get("title", ""))}</span>'
                f'<span class="ms-nj-st {esc(st["tone"])}">{esc(st["label"])}</span>'
                f'<span class="ms-nj-next">{esc(next_move(item))}</span></a>'
            )
        due = due_text(p.get("due", ""))
        jobs = jobs or '<span class="ms-nj-none">No job on the board for this phase.</span>'
        due_b = f" &middot; <b>{esc(due)}</b>" if due else ""
        rows += (
            f'<li class="ms-nw {esc(p.get("state", "now"))}">'
            f'<span class="ms-nw-n">Phase {n}</span>'
            f'<span class="ms-nw-what">{esc(p.get("what", ""))}</span>'
            f'<span class="ms-nw-meta">{esc(p.get("who", ""))}'
            f"{due_b}</span>"
            f"{jobs}</li>"
        )
    return f'<div class="ms-now-box"><h4>Moving now</h4><ul>{rows}</ul></div>' if rows else ""


def ticket_row(row: dict, refs: dict[str, str], index_refs: set[str],
               tags: dict[str, str] | None = None) -> str:
    ref = row.get("ref", "")
    title = row.get("title", "")
    label = (tags or {}).get(ref, ref)
    mine = ref in index_refs
    if mine:
        name = f'<a class="ms-tk-ref" href="#{esc(refs[ref])}">{esc(label)}</a>'
    elif row.get("asana_url"):
        name = f'<a class="ms-tk-ref" href="{esc_url(row["asana_url"])}" target="_blank" rel="noopener">{esc(label or title)}</a>'
    else:
        name = f'<span class="ms-tk-ref">{esc(label or title)}</span>'
    tag = "Your card" if mine else (row.get("assignee") or "Not yours yet")
    state = f'<span class="ms-tk-st">{esc(row["state"])}</span>' if row.get("state") else ""
    return (
        f'<li class="ms-tk {"mine" if mine else "follow"}">{name}'
        f'<span class="ms-tk-t">{esc(title if ref else "")}</span>{state}'
        f'<span class="ms-tk-who">{esc(tag)}</span></li>'
    )


def one(ms: dict, refs: dict[str, str], index: dict[str, tuple[dict, dict]],
        tags: dict[str, str] | None = None) -> str:
    label, tone = STATUS.get(ms.get("status", "on-track"), STATUS["on-track"])
    done, total = counts(ms, index)
    phases = ms.get("phases") or []
    ph_done = sum(1 for p in phases if p.get("state") == "done")
    pct = round(100 * ph_done / len(phases)) if phases else (round(100 * done / total) if total else 0)
    now = [p for _, p in live(phases)]
    now_txt = " / ".join(esc(p.get("what", "")) for p in now)
    target = ms.get("target", "")
    try:
        target_txt = date.fromisoformat(target).strftime("%a %-d %b %Y")
    except (TypeError, ValueError):
        target_txt = target
    note = f' <span class="ms-tn">({esc(ms["target_note"])})</span>' if ms.get("target_note") else ""
    people = " &middot; ".join(
        esc(f"{k} {ms[f]}") for k, f in (("Lead", "lead"), ("Support", "support")) if ms.get(f)
    )
    index_refs = {t.get("ref", "") for t, _ in index.values()} | set(refs)
    tickets = "".join(ticket_row(r, refs, index_refs, tags) for r in ms.get("tickets") or [])
    jobs = ""
    for n in ms.get("items") or []:
        if str(n) not in index:
            continue
        t, item = index[str(n)]
        st = item_state(item)
        jobs += (
            f'<li class="ms-job {esc(st["state"])}"><span class="ms-num {esc(st["tone"])}">{esc(n)}</span>'
            f'<a {job_href(n, index, refs)}>{esc(item.get("title", ""))}</a>'
            f'<span class="ms-tk-st">{esc(st["label"])}</span></li>'
        )
    links = link_btn(ms.get("source_url", ""), "Notion") + link_btn(ms.get("asana_url", ""), "Asana")
    return f"""
    <details class="ms" data-remember="ms-{esc(ms["id"])}">
      <summary class="ms-head">
        <span class="ms-kind">Milestone</span>
        <span class="ms-name">{esc(ms.get("name", ""))}
          {f'<span class="ms-ja">{esc(ms["name_ja"])}</span>' if ms.get("name_ja") else ""}</span>
        <span class="ms-pill {tone}">{esc(label)}</span>
        <span class="ms-due"><b>{esc(target_txt)}</b>{note} &middot; {esc(days_left(target))}</span>
        <span class="fold-hint"></span>
        <span class="ms-bar" role="progressbar" aria-valuenow="{pct}" aria-valuemin="0" aria-valuemax="100">
          <span style="width:{pct}%"></span></span>
        <span class="ms-sum">{ph_done} of {len(phases)} phases done{f" &middot; {done} of {total} jobs closed" if total else ""}
          {f' &middot; now: <b>{now_txt}</b>' if now else ""}
          {f' &middot; {people}' if people else ""}</span>
      </summary>
      {now_panel(phases, index, refs)}
      {f'<p class="ms-notes">{esc(ms["notes"])}</p>' if ms.get("notes") else ""}
      <div class="ms-grid">
        <div><h4>Path to done</h4><ol class="ms-phases">{"".join(phase_row(p, i, index, refs) for i, p in enumerate(phases, 1))}</ol></div>
        <div>
          {f'<h4>Tickets under it</h4><ul class="ms-tks">{tickets}</ul>' if tickets else ""}
          {f'<h4>Your jobs on it</h4><ul class="ms-jobs">{jobs}</ul>' if jobs else ""}
          <div class="ms-links">{links}</div>
        </div>
      </div>
    </details>"""


def render(board: dict, refs: dict[str, str]) -> str:
    rows = [m for m in board.get("milestones") or [] if isinstance(m, dict)]
    if not rows:
        return ""
    index = {str(i.get("id")): (t, i) for t in board.get("tickets", []) for i in t.get("items", [])}
    tags = ticket_tags(board.get("tickets", []))
    # Open ones first, soonest target first; a finished milestone folds to the end.
    rows.sort(key=lambda m: (m.get("status") == "done", m.get("target") or "9999"))
    return '<section class="milestones">' + "".join(one(m, refs, index, tags) for m in rows) + "</section>"


def markdown(board: dict) -> str:
    """The same roll-up for desk.md, which a chat reads before answering."""
    rows = [m for m in board.get("milestones") or [] if isinstance(m, dict)]
    if not rows:
        return ""
    index = {str(i.get("id")): (t, i) for t in board.get("tickets", []) for i in t.get("items", [])}
    out = ["## Milestones", ""]
    for ms in rows:
        label = STATUS.get(ms.get("status", "on-track"), STATUS["on-track"])[0]
        done, total = counts(ms, index)
        out.append(f"### {ms.get('name', '')} {ms.get('name_ja', '')}".rstrip())
        out.append("")
        out.append(
            f"- Target {ms.get('target', '?')}"
            + (f" ({ms['target_note']})" if ms.get("target_note") else "")
            + f", {days_left(ms.get('target', ''))}. {label}."
            + (f" Lead {ms['lead']}." if ms.get("lead") else "")
            + (f" Support {ms['support']}." if ms.get("support") else "")
        )
        if ms.get("notes"):
            out.append(f"- {ms['notes']}")
        if ms.get("source_url"):
            out.append(f"- Source: {ms['source_url']}")
        if total:
            out.append(f"- Jobs closed: {done} of {total}")
        out.append("")
        for n, p in live(ms.get("phases") or []):
            out.append(f"**Moving now, phase {n}:** {p.get('what', '')}"
                       + (f" ({p['who']})" if p.get("who") else "")
                       + (f", {due_text(p['due'])}" if p.get("due") else ""))
            for i in p.get("items") or []:
                if str(i) in index:
                    out.append(f"- Job {i}: {index[str(i)][1].get('title', '')}. {next_move(index[str(i)][1])}")
            out.append("")
        for n, p in enumerate(ms.get("phases") or [], 1):
            bits = [p.get("when", ""), p.get("who", ""),
                    due_text(p["due"]) if p.get("due") and p.get("state") != "done" else ""]
            items = ", ".join(str(i) for i in p.get("items") or [])
            out.append(
                f"{n}. [{p.get('state', 'next')}] {p.get('what', '')}"
                + (f" ({', '.join(b for b in bits if b)})" if any(bits) else "")
                + (f" items {items}" if items else "")
                + (f". {p['note']}" if p.get("note") else "")
            )
        for r in ms.get("tickets") or []:
            out.append(
                f"- Ticket {r.get('ref') or r.get('title', '')}: {r.get('title', '')}"
                + (f", {r['state']}" if r.get("state") else "")
                + f", {r.get('assignee') or 'not assigned to ' + (settings.first_name() or 'you')}"
                + (f" {r['asana_url']}" if r.get("asana_url") else "")
            )
        for n in ms.get("items") or []:
            if str(n) in index:
                t, item = index[str(n)]
                out.append(f"- Job {n} ({t.get('ref', '')}, {item_state(item)['state']}): {item.get('title', '')}")
        out.append("")
    return "\n".join(out)


def speaking(board: dict) -> str:
    """One line per open milestone on the speaking view: where it is, and the
    words for the room if the prep wrote any (`say`, in the client's language and
    the profile's house style)."""
    from render import furi

    rows = [m for m in board.get("milestones") or []
            if isinstance(m, dict) and m.get("status") != "done"]
    if not rows:
        return ""
    out = ""
    for ms in rows:
        phases = ms.get("phases") or []
        now = " / ".join(p.get("what", "") for _, p in live(phases))
        said = "".join(f"<p class=\"ms-say\">{furi(line)}</p>" for line in ms.get("say") or [])
        out += (
            f'<div class="ms-spk"><span class="ms-kind">Milestone</span> '
            f'<b>{esc(ms.get("name_ja") or ms.get("name", ""))}</b> '
            f'<span class="ms-due">{esc(ms.get("target", ""))} &middot; {esc(days_left(ms.get("target", "")))}</span>'
            + (f' <span class="ms-now">now: {esc(now)}</span>' if now else "")
            + f"{said}</div>"
        )
    return f'<section class="ms-spks">{out}</section>'
