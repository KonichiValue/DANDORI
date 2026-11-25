#!/usr/bin/env python3
"""The prioritization round on the desk: when it is, and what to raise before it.

Only when `config.json` has a `prioritization` block. The prioritization lead it
names (`who`) posts the round's result first thing on the round's morning, and
a build nobody raised by then waits for the next round. Tickets already in a
`Wave` (or carrying a client order / assigned engineer) are already scheduled
into a delivery cycle and never appear as `Raise`. Every `Raise` row carries a
short 1-sentence motivation (`why`) explaining why it needs to be prioritized
now; refined builds with no urgent motivation sit calmly under `Hold` instead of
padding the prioritization thread.

Desk only. Nothing here is ever said to the client. `prioritize.py` holds the
dates and the rules; this file only draws them.
"""

from __future__ import annotations

from datetime import date

import prioritize as P
import settings
from render import day_words, draft_html, draft_plain, esc, esc_url, link_btn, ticket_tags

KIND = {
    "raise": ("Raise", "red"),
    "raised": ("Raised", "blue"),
    "ordered": ("In Wave", "green"),
    "parked": ("Hold", "grey"),
    "refining": ("Not refined", "grey"),
}


def when(iso: str) -> str:
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%a %-d %b")
    except (TypeError, ValueError):
        return str(iso or "")


def row(r: dict, refs: dict[str, str], tags: dict[str, str] | None = None) -> str:
    label, tone = KIND[r["kind"]]
    if r["kind"] == "ordered" and r.get("client_order") and not r.get("wave"):
        label = "Ordered"
    ref = r.get("ticket", "")
    tag_txt = (tags or {}).get(ref, ref)
    card = (f'<a class="pr-ref" href="#{esc(refs[ref])}">{esc(tag_txt)}</a>'
            if ref and ref in refs else "")
    kt = (f'<a class="pr-kt" href="{esc_url(r["url"])}" target="_blank" rel="noopener">{esc(r.get("kt", ""))}</a>'
          if r.get("url") else f'<span class="pr-kt">{esc(r.get("kt", ""))}</span>')

    facts = [r.get("status", ""), r.get("size", "")]
    if r.get("client_order"):
        facts.append(f"{settings.client_short()} Order {r['client_order']}")
    if r.get("wave"):
        facts.append(r["wave"])
    if r.get("engineer"):
        facts.append(f"engineer {r['engineer']}")
    if r.get("due_on"):
        facts.append(f"due {when(r['due_on'])}")
    elif r.get("committed_on"):
        facts.append(f"committed {when(r['committed_on'])}")
    elif not r.get("wave"):
        facts.append("not scheduled yet")

    fixes = "".join(f"<li>{esc(f)}</li>" for f in r.get("fixes") or [])
    why = ""
    if r["kind"] == "raise" and r.get("why"):
        why = f'<span class="pr-why"><b>Why raise:</b> {esc(r["why"])}</span>'
    elif r["kind"] == "parked":
        reason = r.get("hold_why") or r.get("why") or "No urgent reason to push in this round."
        why = f'<span class="pr-why muted"><b>Not raising:</b> {esc(reason)}</span>'
    return f"""
        <li class="pr-row {esc(r["kind"])}">
          <span class="pr-tag {tone}">{esc(label)}</span>
          <span class="pr-main">{kt}<span class="pr-name">{esc(P.short(r.get("name", "")))}</span>{card}
            <span class="pr-facts">{esc(" · ".join(f for f in facts if f))}</span>
            {why}
            {f'<ul class="pr-fix">{fixes}</ul>' if fixes else ""}</span>
        </li>"""


def lead(cfg: dict) -> str:
    """Who runs the round, for the card's words: their name, or the role."""
    return str(cfg.get("who") or "") or "the prioritization lead"


def lead_first(cfg: dict) -> str:
    """The lead's first name, for the line that addresses the note to them."""
    who = str(cfg.get("who") or "")
    return who.split(" ")[0] if who else "the prioritization lead"


def render(board: dict, refs: dict[str, str]) -> str:
    s = P.summary(board)
    nxt = s["next"]
    if not s["config"] or not nxt:
        return ""
    cfg = s["config"]
    who = lead(cfg)
    ask_days = nxt["ask_days"]
    cutoff = nxt.get("cutoff_at") or nxt.get("at") or "13:30"
    same_day = nxt.get("ask_by") == nxt.get("date")
    by_clock = f" before {cutoff}" if same_day else ""
    raise_n, raised_n, fix_n = len(s["raise"]), len(s.get("raised") or []), len(s["fixes"])
    in_wave_n = sum(1 for r in s["rows"] if r["kind"] == "ordered")
    if not raise_n and raised_n:
        due, tone = f"Raised for {when(nxt['date'])}", "green"
    elif not raise_n:
        due, tone = f"Nothing to raise for {when(nxt['date'])}", "green"
    elif ask_days < 0:
        due, tone = f"Ask-by passed ({when(nxt['ask_by'])}) · before {when(nxt['date'])} {cutoff}", "red"
    elif ask_days == 0:
        due, tone = f"Raise today{by_clock}" + (" (off on round day)" if nxt.get("pto_shifted") else ""), "red"
    elif ask_days == 1:
        due, tone = f"Raise tomorrow{by_clock}", "amber"
    else:
        due, tone = (
            f"Raise on {when(nxt['ask_by'])}{by_clock}"
            if same_day
            else f"Raise by {when(nxt['ask_by'])} (off {when(nxt['date'])})"
        ), "grey"
    moved_clean = str(nxt.get("moved") or "").strip()
    moved = f' <span class="pr-moved">&middot; {esc(moved_clean)}</span>' if moved_clean else ""
    counts = []
    if raise_n:
        counts.append(f"{raise_n} to raise")
    if raised_n:
        counts.append(f"{raised_n} raised")
    if in_wave_n:
        counts.append(f"{in_wave_n} in wave")
    if fix_n:
        counts.append(f"{fix_n} to fix in Asana")
    tally = " · ".join(counts) or "nothing to raise"
    tags = ticket_tags(board.get("tickets", []))
    rows = "".join(row(r, refs, tags) for r in s["rows"])
    text = P.message(s)
    note = ""
    if text:
        note = f"""
      <div class="draft pr-note">
        <div class="draft-head">
          <span>To {esc(lead_first(cfg))}, in {esc(cfg.get("channel") or "Slack")}, {f"on {esc(when(nxt['date']))} before {esc(cutoff)}" if same_day else f"by {esc(when(nxt['ask_by']))} (before {esc(when(nxt['date']))} {esc(cutoff)})"}</span>
          {link_btn(cfg.get("example_thread", ""), "Last round's thread")}
          <button class="copy" data-copy="{esc(draft_plain(text))}" data-copy-html="{esc(draft_html(text, ruby=False))}">copy</button>
        </div>
        <div class="draft-body">{draft_html(text, ruby=False)}</div>
      </div>"""
    last = s["last_round"]
    last_line = ""
    if last.get("date"):
        bits = []
        if last.get("ordered"):
            bits.append("ordered " + ", ".join(last["ordered"]))
        if last.get("not_pushed"):
            bits.append("not pushed " + ", ".join(last["not_pushed"]))
        last_line = (f'<p class="pr-last">Last round {esc(day_words(last["date"]))}: '
                     f'{esc("; ".join(bits) or "nothing of yours")}. '
                     f'{link_btn(last.get("url", ""), "Thread")}</p>')
    stale = f'<p class="pr-stale">{esc(s["stale"])}</p>' if s["stale"] else ""
    quiet = not (raise_n or fix_n) and ask_days > 1
    return f"""
    <details class="pr{" quiet" if quiet else ""}" id="prioritization" data-remember="prioritization"{" open" if ask_days <= 0 and (raise_n or fix_n) else ""}>
      <summary class="pr-head">
        <span class="pr-kind">Prioritization</span>
        <span class="pr-name-h">{esc(when(nxt["date"]))} {esc(nxt["at"])}{moved}</span>
        <span class="pr-due {tone}">{esc(due)}</span>
        <span class="pr-sum">{esc(tally)}</span>
        <span class="fold-hint"></span>
      </summary>
      <p class="pr-where">In {esc(who)}'s thread{f" in {esc(cfg.get('channel'))}" if cfg.get("channel") else ""}.</p>
      {stale}
      <ul class="pr-rows">{rows or '<li class="pr-row">No builds of yours on the board yet.</li>'}</ul>
      {note}
      {last_line}
    </details>"""


def markdown(board: dict) -> str:
    """The same for desk.md, which a chat reads before answering."""
    s = P.summary(board)
    nxt = s["next"]
    if not s["config"] or not nxt:
        return ""
    who = lead(s["config"])
    out = ["## Prioritization", ""]
    cutoff = nxt.get("cutoff_at") or nxt.get("at") or "13:30"
    same_day = nxt.get("ask_by") == nxt.get("date")
    raise_txt = (
        f"Raise in the thread before {cutoff}"
        if same_day
        else f"Raise by {nxt['ask_by']} (off on {nxt['date']}; closes {cutoff})"
    )
    out.append(
        f"Next round {nxt['date']} {nxt['at']} ({who}'s thread in "
        f"{s['config'].get('channel') or 'Slack'}). {raise_txt}."
        + (f" Moved: {nxt['moved']}." if nxt.get("moved") else "")
    )
    if s["stale"]:
        out.append(f"_{s['stale']}_")
    out.append("")
    for r in s["rows"]:
        label = KIND[r["kind"]][0]
        sched_tail = (
            f", due {r['due_on']}"
            if r.get("due_on")
            else f", committed {r['committed_on']}"
            if r.get("committed_on")
            else ""
        )
        line = (f"- **{label}** {r.get('kt', '')} {P.short(r.get('name', ''))}"
                + (f" ({r['ticket']})" if r.get("ticket") else "")
                + f": {r.get('status', '')}, {r.get('size', '') or 'no size'}"
                + (f", {settings.client_short()} Order {r['client_order']}" if r.get("client_order") else "")
                + (f", {r['wave']}" if r.get("wave") else "")
                + f"{sched_tail}.")
        if r["kind"] == "raise" and r.get("why"):
            line += f" Why raise: {r['why']}"
        elif r["kind"] == "parked":
            line += f" Held back: {r.get('hold_why') or r.get('why') or 'no urgent reason this round'}"
        out.append(line)
        for f in r["fixes"]:
            out.append(f"  - fix in Asana: {f}")
    last = s["last_round"]
    if last.get("date"):
        out += ["", f"Last round {last['date']}: ordered {', '.join(last.get('ordered') or []) or 'none'};"
                    f" not pushed {', '.join(last.get('not_pushed') or []) or 'none'}. {last.get('url', '')}"]
    text = P.message(s)
    if text:
        out += ["", f"Note to {lead_first(s['config'])} (draft, never sent):", "", "```", text, "```"]
    return "\n".join(out) + "\n"
