#!/usr/bin/env python3
"""The prioritization round on the desk: when it is, and what to raise before it.

Only when `config.json` has a `prioritization` block. The prioritization lead it
names (`who`) posts the round's result first thing on the round's morning, and
a build nobody raised by then waits for the next round. So the card's first
line is the day the owner has to have spoken by, not the day of the round, and
it is drawn red once that day has come. Under it, the owner's builds: the ones
to raise, the edits to make in Asana first, and the ones already ordered, which
only need their dates kept right. The note to the lead is built from the same
rows, so it cannot say something the list does not. With no block there is no
round, and both renderers return "".

Desk only. Nothing here is ever said to the client. `prioritize.py` holds the
dates and the rules; this file only draws them.
"""

from __future__ import annotations

from datetime import date

import prioritize as P
import settings
from render import day_words, esc, esc_url, link_btn, ticket_tags

KIND = {
    "raise": ("Raise", "red"),
    "ordered": ("Ordered", "green"),
    "refining": ("Not refined", "grey"),
}


def when(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%a %-d %b")
    except (TypeError, ValueError):
        return iso


def row(r: dict, refs: dict[str, str], tags: dict[str, str] | None = None) -> str:
    label, tone = KIND[r["kind"]]
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
    facts.append(f"due {when(r['due_on'])}" if r.get("due_on") else "no due date")
    fixes = "".join(f'<li>{esc(f)}</li>' for f in r.get("fixes") or [])
    why = f'<span class="pr-why">{esc(r["why"])}</span>' if r.get("why") and r["kind"] == "raise" else ""
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
    if ask_days < 0:
        due, tone = f"Ask-by day passed ({when(nxt['ask_by'])}); raise it first thing", "red"
    elif ask_days == 0:
        due, tone = "Raise anything today", "red"
    elif ask_days == 1:
        due, tone = "Raise anything by tomorrow", "amber"
    else:
        due, tone = f"Raise anything by {when(nxt['ask_by'])}", "grey"
    moved = f' <span class="pr-moved">(moved: {esc(nxt["moved"])})</span>' if nxt.get("moved") else ""
    raise_n, fix_n = len(s["raise"]), len(s["fixes"])
    counts = []
    if raise_n:
        counts.append(f"{raise_n} to raise")
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
          <span>To {esc(lead_first(cfg))}, in {esc(cfg.get("channel") or "Slack")}, before {esc(when(nxt["date"]))} {esc(nxt["at"])}</span>
          {link_btn(cfg.get("example_thread", ""), "Last round's thread")}
          <button class="copy" data-copy="{esc(text)}">copy</button>
        </div>
        <div class="draft-body">{esc(text)}</div>
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
    return f"""
    <details class="pr" id="prioritization" data-remember="prioritization"{" open" if ask_days <= 1 and (raise_n or fix_n) else ""}>
      <summary class="pr-head">
        <span class="pr-kind">Prioritization</span>
        <span class="pr-name-h">{esc(when(nxt["date"]))} {esc(nxt["at"])}, {esc(who)}'s thread{moved}</span>
        <span class="pr-due {tone}">{esc(due)}</span>
        <span class="pr-sum">{esc(tally)}</span>
        <span class="fold-hint"></span>
      </summary>
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
    out.append(
        f"Next round {nxt['date']} {nxt['at']} ({who}'s thread in "
        f"{s['config'].get('channel') or 'Slack'}). Raise anything by {nxt['ask_by']}."
        + (f" Moved: {nxt['moved']}." if nxt.get("moved") else "")
    )
    if s["stale"]:
        out.append(f"_{s['stale']}_")
    out.append("")
    for r in s["rows"]:
        label = KIND[r["kind"]][0]
        line = (f"- **{label}** {r.get('kt', '')} {P.short(r.get('name', ''))}"
                + (f" ({r['ticket']})" if r.get("ticket") else "")
                + f": {r.get('status', '')}, {r.get('size', '') or 'no size'}"
                + (f", {settings.client_short()} Order {r['client_order']}" if r.get("client_order") else "")
                + (f", {r['wave']}" if r.get("wave") else "")
                + f", due {r.get('due_on') or 'none'}.")
        if r.get("why") and r["kind"] == "raise":
            line += f" {r['why']}"
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
