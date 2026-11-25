#!/usr/bin/env python3
"""The speaking view: the same tickets as the desk, in the order the meeting
walks them, with the words to say out loud.

This is not a second page. It renders into the same HTML file as the desk and
reads the same `state/board.json`, because the two views answering to different
files is exactly how a script ends up describing a status that has moved on.

The desk answers "what do I do". This answers "what do I say", and every ticket
carries a link back to its items on the desk.

Most days that means the main standup in `config.json` `meeting`. Some days it
is an onsite, which is the same tickets with a day in the room instead of
fifteen minutes, so the session banner, the running order and each card carry
the extra weight an onsite needs: what has to be decided before everyone goes
home, and what to say when the client pushes back.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import render_milestone
import settings
from render import (
    ASKED,
    SPARK,
    ai_split_btn,
    ask_block,
    asset,
    day_words,
    esc,
    furi,
    item_state as state_of,
    link_btn,
    opening,
    pill,
    plain,
    render_consequences,
    render_script,
    script_meta,
    script_session,
    section,
    sessions as all_sessions,
    ticket_tag,
    ticket_tags,
    when_words,
)


# The script block that holds the spoken questions. `質問` is the convention
# prompts/prep.md gives a Japanese-speaking client; the English names let a desk
# in any other language mark the same block by its `heading_en`.
QUESTION_HEADINGS = {"質問"}
QUESTION_HEADINGS_EN = {"question", "questions", "what i need answered", "what we need answered"}

JOURNEY_WORDS = {
    "done": ("Done", "done"),
    "now": ("Now", "now"),
    "blocked": ("Blocked", "blocked"),
    "next": ("Next", "next"),
}

BOARD_SLOT_RE = re.compile(r"^(\d+)\s+of\s+(\d+)")
IMG_EXT_RE = re.compile(r"\.(png|jpe?g|webp|gif)$", re.I)


def _visual_urls(raw_path: str) -> tuple[str, str]:
    raw = str(raw_path or "").strip()
    if raw.startswith(("http://", "https://")):
        return raw, raw
    rel = re.sub(r"^(?:clients/[^/]+/)?(?:docs|scripts)/", "", raw).lstrip("/")
    name = rel or Path(raw).name
    return f"/doc/{name}", f"../docs/{name}"


def _explainer_visuals_html(t: dict) -> str:
    """Compact visual screenshot/diagram strip inside `Understand this ticket`."""
    exp = ((t.get("prep") or {}).get("explainer") or {})
    candidates: list[dict] = [v for v in (exp.get("visuals") or []) if isinstance(v, dict)]
    for item in reversed(t.get("items") or []):
        for f in ((item.get("prepared") or {}).get("files") or []):
            if isinstance(f, dict):
                candidates.append(f)

    seen: set[str] = set()
    cards: list[str] = []
    for f in candidates:
        raw = str(f.get("path") or f.get("url") or "").strip()
        if not raw or not IMG_EXT_RE.search(raw) or raw in seen:
            continue
        seen.add(raw)
        href, fallback = _visual_urls(raw)
        label = str(f.get("label") or Path(raw).name)
        caption = str(f.get("caption") or f.get("where") or "")
        fb_attr = (
            f' onerror="if(this.dataset.fb&&this.src!==this.dataset.fb){{this.src=this.dataset.fb;'
            f'var a=this.closest(\'a\');if(a)a.href=this.dataset.fb}}"'
            f' data-fb="{esc_attr(fallback)}"'
            if fallback != href
            else ""
        )
        cards.append(
            f'<a class="exp-shot" href="{esc(href)}" data-lightbox="{esc(label)}" '
            f'data-caption="{esc(caption)}" target="_blank" rel="noopener">'
            f'<span class="exp-shot-img"><img src="{esc(href)}" alt="{esc(label)}" loading="lazy"{fb_attr}></span>'
            f'<span class="exp-shot-cap"><b>{esc(label)}</b>'
            f'{f"<i>{esc(caption)}</i>" if caption else ""}</span></a>'
        )
        if len(cards) >= 4:
            break

    if not cards:
        return ""
    return (
        '<div class="exp-vis">'
        '<span class="exp-vis-h">Visual reference <i>click any screenshot to enlarge</i></span>'
        f'<div class="exp-shots">{"".join(cards)}</div>'
        "</div>"
    )


def esc_attr(val: object) -> str:
    return esc(val)


def is_question_block(block: dict) -> bool:
    """Whether a script block is the one where the questions are said out loud."""
    if block.get("heading") in QUESTION_HEADINGS:
        return True
    # A Japanese desk has always gone by `質問` alone, and still does.
    if settings.language().get("client") == "ja":
        return False
    return str(block.get("heading_en") or "").strip().lower() in QUESTION_HEADINGS_EN


def owed_by_client(who: object) -> bool:
    """An open question the client answers, which the script already asks aloud.

    A blank holder counts as the client, as it always has: a question nobody
    tagged was written for the room. A missing one does not.
    """
    if who is None:
        return False
    return who == "" or settings.is_client(str(who))


def also_room(kinds: tuple[str, ...], default: str) -> str:
    """The name of a room from `config.json` `meeting.also`, found by its `kind`."""
    room = room_of(kinds)
    name = str(room.get("tab") or room.get("name") or "").strip()
    return name or default


def room_of(kinds: tuple[str, ...]) -> dict:
    """The first room in `meeting.also` with one of these kinds, or {}."""
    for room in settings.meeting().get("also") or []:
        if isinstance(room, dict) and str(room.get("kind", "")).lower() in kinds:
            return room
    return {}


def room_link(kinds: tuple[str, ...]) -> str:
    """The room's own page (`link` on its `meeting.also` entry), if it has one."""
    url = str(room_of(kinds).get("link") or "").strip()
    return url if url.startswith(("https://", "http://")) else ""


def rollup_name() -> str:
    """The programme-wide rollup (board field `xws`, for cross-workstream)."""
    return also_room(("rollup", "xws"), "Programme rollup")


def huddle_name() -> str:
    """The Kraken-side team huddle (board field `huddle`)."""
    return also_room(("huddle",), f"{settings.client_short()} Team Huddle")


def script_only_words() -> str:
    """What the `s` key shows: the client-language lines on their own."""
    return "Japanese only" if settings.language().get("client") == "ja" else "script only"


def anchor(ident: str) -> str:
    """Standup anchors mirror the desk's, with an s- prefix, so a ticket can
    link to its own other half."""
    return "s-" + ident.removeprefix("t-")


def _one_line(text: str, limit: int = 128) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    for stop in (". ", "。"):
        if stop in clean:
            clean = clean.split(stop)[0] + ("." if stop == ". " else "。")
            break
    if len(clean) > limit:
        clean = clean[: limit - 1].rsplit(" ", 1)[0] + "…"
    return clean


def has_explainer(prep: dict) -> bool:
    exp = prep.get("explainer")
    return isinstance(exp, dict) and any(
        exp.get(k) for k in ("summary", "flow", "journey", "facts", "if_asked")
    )


def ticket_gist(t: dict) -> str:
    """One crisp English line summarizing what the owner says or asks on this ticket."""
    prep = t.get("prep") or {}
    if prep.get("gist"):
        return _one_line(str(prep["gist"]), 136)
    blocks = prep.get("script") or []
    # Prefer the ask/request block when the ticket needs something from the client,
    # otherwise lead with the status line.
    if prep.get("client_ask_needed") is not False and len(blocks) > 1:
        last_lines = blocks[-1].get("lines") or []
        if last_lines and last_lines[0].get("en"):
            return _one_line(str(last_lines[0]["en"]), 136)
    for b in blocks:
        for ln in b.get("lines") or []:
            if ln.get("en"):
                return _one_line(str(ln["en"]), 136)
    issue = prep.get("issue") or []
    if issue:
        return _one_line(str(issue[0]), 136)
    return _one_line(str(t.get("where_it_stands") or ""), 136)


def raise_rows(t: dict, desk_id: str) -> str:
    """Items the board says to raise in the meeting, pulled straight from the
    desk so nothing has to be written twice."""
    rows = []
    for item in t.get("items", []):
        if not item.get("at_standup"):
            continue
        st = state_of(item)
        if st["closed"]:
            continue
        rows.append(
            f"""
        <li>
          <span class="n">{esc(item.get("id", "-"))}</span>
          <span class="t">{esc(item.get("title"))}
            {f'<span class="st-secret">{esc(item.get("at_standup_note"))}</span>' if item.get("at_standup_note") else ""}</span>
          <a href="#{esc(desk_id)}" data-goto="desk">on the desk</a>
        </li>"""
        )
    if not rows:
        return ""
    return f'<ul class="st-raise">{"".join(rows)}</ul>'


def render_session(sess: dict, big: bool) -> str:
    """What this room has to produce, and what the owner has to walk in holding."""
    if not sess.get("date") and not sess.get("focus"):
        return ""
    bring = sess.get("bring", [])
    bring_block = ""
    if bring:
        rows = "".join(f"<li>{esc(b)}</li>" for b in bring)
        bring_block = (
            f'<div class="sess-bring"><b>Walk in with</b>'
            f'<ul style="margin:0;padding-left:17px">{rows}</ul></div>'
        )
    focus = (
        f'<p class="sess-focus">{esc(sess.get("focus"))}</p>'
        if sess.get("focus")
        else ""
    )
    if not focus and not bring_block:
        return ""
    return f"""
  <section class="sess {"big" if big else ""} {"" if focus else "bare"}">
    {focus}{bring_block}
  </section>"""


def render_pushback(rows: list[dict]) -> str:
    """The objection you can see coming, and the sentence that answers it."""
    if not rows:
        return ""
    out = []
    for r in rows:
        raw = plain(r.get("say_ja", ""))
        out.append(
            f"""
        <li>
          <div class="they">{esc(r.get("they_say"))}</div>
          <div class="mine">
            <p class="jp">{furi(r.get("say_ja", ""))}</p>
            <p class="en">{esc(r.get("say_en"))}</p>
          </div>
          {f'<button class="copy" data-copy="{esc(raw)}">copy</button>' if raw else ""}
        </li>"""
        )
    return section(
        "If they push back",
        f'<ul class="st-push">{"".join(out)}</ul>',
        role="ref",
        count=f"{len(rows)} answers ready",
        fold=True,
        hint="the objection you can see coming",
    )


def move_li(e: dict) -> str:
    """One move: when, who, what it was, and what it meant."""
    src = e.get("source_url", "")
    return f"""
        <li>
          <span class="mv-when">{esc(e.get("at", ""))}</span>
          <span class="mv-what"><b>{esc(e.get("who", ""))}</b> {esc(e.get("what"))}
            {f'<span class="mv-so">{esc(e.get("so_what"))}</span>' if e.get("so_what") else ""}
          </span>
          {link_btn(src, "Source") if src else ""}
        </li>"""


def _moves_markup(t: dict, since: str) -> tuple[str, str, int]:
    """Return `(body_html, tally_words, new_count)` for a ticket's timeline moves."""
    rows = sorted(t.get("events", []), key=lambda e: (e.get("on", ""), e.get("at", "")))
    if not rows:
        return "", "", 0
    new = [e for e in rows if (e.get("on") or "") >= since]
    old = [e for e in rows if (e.get("on") or "") < since]

    days: dict[str, list[dict]] = {}
    for e in rows:
        days.setdefault(e.get("on", ""), []).append(e)
    order = sorted(days, reverse=True)
    parts, marked = [], False
    for d in order:
        if old and not marked and d < since:
            parts.append(
                f'<p class="mv-mark">You last spoke about this '
                f"{esc(day_words(since))}. Everything below is older.</p>"
            )
            marked = True
        moves = days[d]
        n = len(moves)
        parts.append(
            f"""
        <details class="mv-fold" {"open" if d == order[0] else ""}>
          <summary><span class="mv-d">{opening(esc(day_words(d)))}</span>
          <span class="mv-c">{n} {"move" if n == 1 else "moves"}</span>
          <span class="fold-hint"></span></summary>
          <ul class="moves">{"".join(move_li(e) for e in moves)}</ul>
        </details>"""
        )
    body = f'<div class="mv-days">{"".join(parts)}</div>'
    if new and old:
        tally = f"{len(new)} since {day_words(since)}, {len(old)} before"
    elif new:
        tally = f"{len(new)} since {day_words(since)}"
    else:
        tally = f"{len(old)} {'move' if len(old) == 1 else 'moves'}, none since you spoke"
    return body, tally, len(new)


def since_last(t: dict, since: str) -> str:
    """What moved on this ticket since the last meeting, and the days before it."""
    body, tally, _ = _moves_markup(t, since)
    if not body:
        return ""
    return section(
        "What moved on this ticket",
        body,
        role="log",
        count=tally,
        hint="the question they always ask",
        fold=True,
        remember=f"moved-{t.get('ref', '')}",
    )


def render_explainer(t: dict, ident: str, desk_id: str, since: str) -> str:
    """Pedagogical explanation drawer on the speaking card.

    For ordinary, familiar tickets, keeps the light one-fold summary.
    For complex, new, or newly-assigned tickets carrying `prep.explainer`,
    renders a compact segmented walkthrough (How it works / Done & next /
    Pocket facts & Q&A / Moves) so the owner grasps the whole mental model,
    prior history, and speaking rationale in seconds without bloating the page.
    """
    prep = t.get("prep") or {}
    t_ref = str(t.get("ref") or "")
    issue_items = "".join(f"<li>{esc(b)}</li>" for b in (prep.get("issue") or []))
    raised = raise_rows(t, desk_id)
    ask_btn = (
        f'<button class="qe-btn exp-ask-btn" type="button" data-qe-kind="ticket" '
        f'data-qe-ref="ticket:{esc(t_ref)}" data-qe-lang="en" aria-expanded="false" '
        f'title="Ask Opus anything about this ticket, or clarify how it works">{SPARK}Ask AI</button>'
    )

    if not has_explainer(prep):
        preview = _one_line(
            (prep.get("issue") or [""])[0] or str(t.get("where_it_stands") or ""),
            125,
        )
        return f"""
      <details class="sub key st-brief" data-remember="brief-{esc(ident)}">
        <summary><h3>If they ask what this is
          <span class="hint">the issue, where it stands, what moved</span></h3>
          {ask_btn}
          <span class="fold-hint"></span>
          {f'<span class="exp-closed-sum">{esc(preview)}</span>' if preview else ""}</summary>
        <div class="st-brief-in">
          <div class="qe-slot exp-qe-slot" data-qe-slot hidden></div>
          {f'<ul class="st-issue">{issue_items}</ul>' if issue_items else ""}
          {f'<p class="matters">{esc(prep.get("why_it_matters"))}</p>' if prep.get("why_it_matters") else ""}
          <p class="st-stands">{esc(t.get("where_it_stands"))}</p>
          {raised}
          {since_last(t, since)}
        </div>
      </details>"""

    exp = prep["explainer"]
    is_new = bool(exp.get("new_to_you"))
    badge_text = str(exp.get("badge") or ("New to you · Walkthrough" if is_new else "Walkthrough"))
    summary_text = str(exp.get("summary") or (prep.get("issue") or [""])[0] or "")
    why_say = str(exp.get("why_say") or "")

    # Tab 1: Mental model + visual step-by-step flow + screenshots + why your script says what it says
    flow_rows = exp.get("flow") or []
    flow_html = ""
    if flow_rows:
        cards = []
        for idx, f in enumerate(flow_rows, 1):
            step_lbl = str(f.get("step") or f.get("label") or f"Step {idx}")
            step_body = str(f.get("detail") or f.get("what") or "")
            who_str = str(f.get("who") or "").strip()
            where_str = str(f.get("where") or "").strip()
            meta_bits = [x for x in (f"<b>{esc(who_str)}</b>" if who_str else "", esc(where_str) if where_str else "") if x]
            meta_html = f'<span class="ef-meta">{" &middot; ".join(meta_bits)}</span>' if meta_bits else ""
            cards.append(
                f'<li class="ef-card"><div class="ef-top"><span class="ef-step">{esc(step_lbl)}</span>{meta_html}</div>'
                f'<p class="ef-body">{esc(step_body)}</p></li>'
            )
        flow_html = f'<ol class="exp-flow">{"".join(cards)}</ol>'

    vis_html = _explainer_visuals_html(t)

    pane_how = f"""
          {f'<p class="exp-sum">{esc(summary_text)}</p>' if summary_text else (f'<ul class="st-issue">{issue_items}</ul>' if issue_items else "")}
          {f'<p class="exp-impact"><b>Why it matters</b> {esc(prep.get("why_it_matters"))}</p>' if prep.get("why_it_matters") else ""}
          {flow_html}
          {vis_html}
          {f'<div class="exp-why"><b>Why you say this today</b><p>{esc(why_say)}</p></div>' if why_say else ""}"""

    panes: list[tuple[str, str, str]] = [("how", "1. How it works", pane_how)]

    # Tab 2: Done so far -> Where we stand -> What comes next
    journey_rows = exp.get("journey") or []
    if journey_rows or t.get("where_it_stands") or raised:
        j_items = []
        for j in journey_rows:
            st_raw = str(j.get("state") or "done").lower()
            word, cls = JOURNEY_WORDS.get(st_raw, ("Done", "done"))
            lbl = str(j.get("label") or j.get("what") or "")
            det = str(j.get("detail") or j.get("note") or "")
            j_items.append(
                f'<li class="ej {cls}"><span class="ej-pill">{esc(word)}</span>'
                f'<div class="ej-b"><b>{esc(lbl)}</b>'
                f'{f"<span>{esc(det)}</span>" if det else ""}</div></li>'
            )
        j_list = f'<ol class="exp-journey">{"".join(j_items)}</ol>' if j_items else ""
        stands_line = (
            f'<p class="exp-stands"><b>Right now on your desk</b> {esc(t.get("where_it_stands"))}</p>'
            if t.get("where_it_stands")
            else ""
        )
        panes.append(("journey", "2. Done & next", f"{j_list}{stands_line}{raised}"))

    # Tab 3: Pocket facts & ready answers if asked
    facts_rows = exp.get("facts") or []
    qna_rows = exp.get("if_asked") or []
    if facts_rows or qna_rows:
        facts_html = ""
        if facts_rows:
            cells = "".join(
                f'<div class="ef-kv"><dt>{esc(r.get("label"))}</dt><dd>{esc(r.get("value"))}</dd></div>'
                for r in facts_rows
                if r.get("label") or r.get("value")
            )
            facts_html = f'<dl class="exp-facts">{cells}</dl>' if cells else ""
        qna_html = ""
        if qna_rows:
            qs = []
            for r in qna_rows:
                raw_ja = plain(r.get("say_ja", ""))
                say_part = (
                    f'<div class="eq-ja"><p class="jp">{furi(r.get("say_ja", ""))}</p>'
                    f'{f"""<button class="copy" data-copy="{esc(raw_ja)}">copy</button>""" if raw_ja else ""}</div>'
                    if r.get("say_ja")
                    else ""
                )
                qs.append(
                    f'<li><p class="eq-q"><b>If asked:</b> {esc(r.get("q"))}</p>'
                    f'<p class="eq-a">{esc(r.get("a"))}</p>{say_part}</li>'
                )
            qna_html = f'<ul class="exp-qna">{"".join(qs)}</ul>'
        tab_num = len(panes) + 1
        panes.append(("facts", f"{tab_num}. Key facts & Q&A", f"{facts_html}{qna_html}"))

    # Tab 4: Timeline moves (kept one click away, zero default vertical height)
    moves_body, moves_tally, new_cnt = _moves_markup(t, since)
    if moves_body:
        mv_label = f"Timeline ({new_cnt} new)" if new_cnt else f"Timeline ({len(t.get('events') or [])})"
        panes.append(
            ("moves", mv_label, f'<p class="exp-mv-sum">{esc(moves_tally)}</p>{moves_body}')
        )

    tabs_bar = ""
    if len(panes) > 1:
        btns = "".join(
            f'<button type="button" class="exp-tab" role="tab" '
            f'data-exp-tab="{esc(key)}" aria-selected="{"true" if idx == 0 else "false"}">'
            f"{esc(label)}</button>"
            for idx, (key, label, _) in enumerate(panes)
        )
        tabs_bar = f'<div class="exp-tabs" role="tablist" aria-label="Ticket explanation">{btns}</div>'

    pane_divs = "".join(
        f'<div class="exp-pane" data-exp-pane="{esc(key)}"{"" if idx == 0 else " hidden"}>{html}</div>'
        for idx, (key, _, html) in enumerate(panes)
    )

    closed_preview = _one_line(summary_text, 128)
    rem_key = f"expnew-{ident}" if is_new else f"exp-{ident}"
    return f"""
      <details class="sub key st-brief has-exp{" is-new" if is_new else ""}"
               data-remember="{esc(rem_key)}"{" open" if is_new else ""}>
        <summary>
          <h3>Understand this ticket
            <span class="exp-badge{" new" if is_new else ""}">{esc(badge_text)}</span>
            <span class="hint">mental model, progress, pocket facts</span>
          </h3>
          {ask_btn}
          <span class="fold-hint"></span>
          {f'<span class="exp-closed-sum">{esc(closed_preview)}</span>' if closed_preview else ""}
        </summary>
        <div class="st-brief-in exp-shell" data-exp-group="{esc(ident)}">
          <div class="qe-slot exp-qe-slot" data-qe-slot hidden></div>
          {tabs_bar}
          {pane_divs}
        </div>
      </details>"""


def needs(t: dict, spoken: bool) -> str:
    """Everything the owner has to come out of the room with, in one list."""
    prep = t.get("prep") or {}
    rows, hardest = [], 0
    for d in prep.get("decisions", []):
        hardest = 2
        rows.append(
            f"""
        <li class="need-row settle">
          <div class="need-k">Settle it today</div>
          <div class="need-b">
            <p class="need-what">{esc(d.get("need"))}</p>
            {f'<p class="need-why">{esc(d.get("why"))}</p>' if d.get("why") else ""}
            {f'<p class="need-fall"><b>If they will not settle it</b> {esc(d.get("fallback"))}</p>' if d.get("fallback") else ""}
          </div>
        </li>"""
        )

    asks = list(prep.get("open_questions", []))
    if spoken:
        asks = [q for q in asks if not owed_by_client(q.get("who"))]
    for q in asks:
        hardest = max(hardest, 1)
        who = q.get("who", settings.client_short())
        raw = plain(q.get("ja_ruby", ""))
        rows.append(
            f"""
        <li class="need-row ask">
          <div class="need-k">Answer from {esc(who)}</div>
          <div class="need-b">
            <p class="need-what">{esc(q.get("en"))}
              {f'<button class="copy" data-copy="{esc(raw)}">copy</button>' if raw else ""}</p>
            {f'<p class="jp small">{furi(q.get("ja_ruby", ""))}</p>' if q.get("ja_ruby") else ""}
          </div>
        </li>"""
        )

    for item in t.get("items", []):
        st = state_of(item)
        if st["state"] != "waiting":
            continue
        owed = (item.get("waits_on") or {}).get("what", "")
        rows.append(
            f"""
        <li class="need-row owed">
          <div class="need-k">Already owed</div>
          <div class="need-b">
            <p class="need-what">{esc(owed or item.get("title", ""))}</p>
            <p class="need-why">{esc(st.get("who", "They"))} has had this since
            {esc(st.get("at", ""))}. Item {esc(item.get("id", ""))} on your list.
            Chase it here if they are in the room.</p>
          </div>
        </li>"""
        )

    if not rows:
        return ""
    words = {0: "", 1: "to get answered", 2: "one has to be settled today"}
    return section(
        "What I need back",
        f'<ul class="needs">{"".join(rows)}</ul>',
        role="warn",
        count=f"{len(rows)} in total",
        hint=words[hardest],
    )


def render_ticket(t: dict, ident: str, desk_id: str, since: str) -> str:
    prep = t.get("prep") or {}
    no_ask = prep.get("client_ask_needed") is False and not prep.get("decisions")
    unknowns = prep.get("unknowns", [])
    warn = ""
    if unknowns:
        rows = "".join(f"<li>{esc(u)}</li>" for u in unknowns)
        warn = f"""
      <section class="st-warn">
        <h3>Check before you speak</h3>
        <ul>{rows}</ul>
      </section>"""

    internal = t.get("internal_ticket") or {}
    secret = (
        f'<p class="st-secret">Never to {esc(settings.client_short())}: {esc(internal.get("name"))}. '
        "No dates, no sizing, no queue position.</p>"
        if internal.get("name")
        else ""
    )
    safe = (internal.get("build") or {}).get("safe_to_say")
    if safe:
        secret += f'<p class="st-safe">If they ask when: {furi(safe)}</p>'

    open_items = [i for i in t.get("items", []) if not state_of(i)["closed"]]
    desk_link = (
        f'<a class="btn" href="#{esc(desk_id)}" data-goto="desk">'
        f'{len(open_items)} open on my list</a>'
        if open_items
        else ""
    )
    spoken = any(is_question_block(b) for b in prep.get("script", []))
    tone = "mine" if not no_ask else "clear"

    return f"""
    <article class="st-tk {tone}" id="{esc(ident)}">
      <header class="st-head">
        <div class="tk-id">
          <span class="tag">{esc(ticket_tag(t))}</span>
          {f'<span class="run-prio {esc(_prio_cls((t.get("asana") or {}).get("priority")))}">{esc(str((t.get("asana") or {}).get("priority")).strip())}</span>' if (t.get("asana") or {}).get("priority") else ""}
          <span class="tk-kind">{"Work in hand" if t.get("no_ticket_yet") else "Asana ticket"}</span>
          {pill("Nothing to ask", "green") if no_ask else pill("You need an answer", "amber")}
        </div>
        <h2>{esc(t.get("title_en"))}</h2>
        {f'<p class="ja">{esc(t["title_ja"])}</p>' if t.get("title_ja") else ""}
        <p class="pos">
          {f'<span>{esc(prep.get("board_position"))}</span>' if prep.get("board_position") else ""}
          {f'<span class="est">{esc(prep.get("estimate"))}</span>' if prep.get("estimate") else ""}
        </p>
        <div class="tk-links">
          <span class="lab">Go to</span>
          {link_btn(t.get("asana_url", ""), "Open in Asana")}
          {desk_link}
        </div>
      </header>

      <section class="sub say script">
        <h3>Say this<span class="hint">out loud, as written</span>
          <button class="qe-btn" type="button" data-qe-kind="script" data-qe-ref="ticket:{esc(t.get("ref"))}"
                  data-qe-lang="{"ja" if settings.language().get("client") == "ja" else "en"}" aria-expanded="false"
                  title="Ask Opus what a line means, check accuracy, or revise these speaking lines">{SPARK}Ask / Revise</button>
          {ai_split_btn(
              f'ticket:{t.get("ref")}',
              tpl="Edit in {app}",
              cls="sc-ai",
              title="Open this speaking script & ticket in Cursor, Claude Code, or Claude Desktop",
          )}</h3>
        <div class="qe-slot script-qe-slot" data-qe-slot hidden></div>
        {f'<p class="st-none">Status only. Nothing needed from {esc(settings.client_short())}.</p>' if no_ask else ""}
        <div class="st-script-wrap">{render_script(prep.get("script", []))}</div>
      </section>

      {warn}
      {render_pushback(prep.get("pushback", []))}
      {needs(t, spoken)}
      {render_explainer(t, ident, desk_id, since)}

      <div class="st-cons">{render_consequences(prep.get("consequences", {}), t.get("ref", ""))}</div>
      {secret}
    </article>"""


def _prio_cls(raw: object) -> str:
    p = str(raw or "").strip().lower()
    if "top" in p or p == "high":
        return "top"
    if p == "priority":
        return "prio"
    return "other"


def _slot_badge(prep: dict, fallback: int) -> tuple[str, str]:
    pos = str(prep.get("board_position") or "").strip()
    m = BOARD_SLOT_RE.match(pos)
    if m:
        return m.group(1), f"/{m.group(2)}"
    return str(prep.get("order", fallback)), ""


def running_order(tickets: list[dict], ids: dict[str, str]) -> str:
    """The order the meeting reaches the owner's tickets, with Priority group,
    1-line status gist, and posture badge on each row."""
    client = settings.client_short()
    rows = []
    for n, t in enumerate(tickets, 1):
        prep = t.get("prep") or {}
        asana = t.get("asana") or {}
        prio_txt = str(asana.get("priority") or "").strip()
        prio_badge = (
            f'<span class="run-prio {_prio_cls(prio_txt)}">{esc(prio_txt)}</span>'
            if prio_txt
            else ""
        )
        decisions = len(prep.get("decisions", []))
        no_ask = prep.get("client_ask_needed") is False and not decisions
        slot, denom = _slot_badge(prep, n)
        gist = ticket_gist(t)
        if decisions:
            posture_lbl, posture_cls = f"{decisions} to settle", "settle"
        elif no_ask:
            posture_lbl, posture_cls = "Status only", "status"
        else:
            posture_lbl, posture_cls = f"Ask {client}", "ask"
        exp_chip = (
            '<span class="run-exp" title="Includes pedagogical walkthrough below">Walkthrough</span>'
            if has_explainer(prep)
            else ""
        )
        rows.append(
            f"""
      <li>
        <span class="n" title="{esc(prep.get("board_position") or f"Speaking order {slot}")}">{esc(slot)}{f'<i>{esc(denom)}</i>' if denom else ""}</span>
        <div class="run-main">
          <a href="#{esc(ids[t.get("ref", "")])}">{prio_badge}<b>{esc(t.get("ref", ""))}</b> &middot; {esc(t.get("title_en"))}</a>
          {f'<span class="run-ja">{esc(t["title_ja"])}</span>' if t.get("title_ja") else ""}
          {f'<span class="run-gist">{esc(gist)}</span>' if gist else ""}
        </div>
        <div class="run-tags">
          {exp_chip}
          <span class="run-pill {posture_cls}">{esc(posture_lbl)}</span>
        </div>
      </li>"""
        )
    return f'<ul class="st-run">{"".join(rows)}</ul>'


def before_start_box(
    board: dict,
    tickets: list[dict],
    ids: dict[str, str],
    since: str,
) -> str:
    """Compact pre-session briefing inside the standup card: what shifted since the
    last session, where the client's answer is needed, and any room context."""
    meta = script_meta(board)
    explicit = meta.get("before_start") or []
    rows: list[tuple[str, str, str]] = []

    if explicit:
        for entry in explicit[:4]:
            if isinstance(entry, str):
                rows.append(("Note", entry, ""))
            elif isinstance(entry, dict) and entry.get("what"):
                rows.append((
                    str(entry.get("label") or "Status"),
                    str(entry.get("what") or ""),
                    str(entry.get("ref") or ""),
                ))
    else:
        # Synthesize concise pre-meeting bullets straight from the board if none were written.
        ask_refs = [
            str(t.get("ref") or "")
            for t in tickets
            if (t.get("prep") or {}).get("client_ask_needed") is not False
            or (t.get("prep") or {}).get("decisions")
        ]
        if ask_refs:
            rows.append((
                "Your asks",
                f"Need {settings.client_short()}'s input on {', '.join(ask_refs)}; "
                f"the remaining {max(0, len(tickets) - len(ask_refs))} ticket(s) are status-only.",
                ask_refs[0] if len(ask_refs) == 1 else "",
            ))
        else:
            rows.append((
                "Posture",
                f"All {len(tickets)} spoken tickets today are status updates; nothing owed back in the room.",
                "",
            ))

        moved_bits = []
        for t in tickets:
            evs = [
                e for e in (t.get("events") or [])
                if (e.get("on") or "") >= since
                and str(e.get("who") or "").strip().lower() not in ({"you"} | settings.owner_aliases())
            ]
            if evs:
                last = evs[-1]
                moved_bits.append(f"{t.get('ref')}: {_one_line(str(last.get('so_what') or last.get('what') or ''), 76)}")
        if moved_bits:
            rows.append((
                f"Since {day_words(since)}",
                " · ".join(moved_bits[:2]),
                "",
            ))

    if not rows:
        return ""

    items_html = []
    for label, text, ref in rows:
        chip = (
            f'<a class="nk-pre-ref" href="#{esc(ids[ref])}">{esc(ref)}</a>'
            if ref and ref in ids
            else (f'<span class="nk-pre-ref">{esc(ref)}</span>' if ref else "")
        )
        items_html.append(
            f'<li><span class="nk-pre-k">{esc(label)}</span>'
            f'<span class="nk-pre-w">{esc(text)} {chip}</span></li>'
        )
    return (
        '<div class="nk-pre">'
        '<div class="nk-pre-h">Main status before it starts</div>'
        f'<ul class="nk-pre-list">{"".join(items_html)}</ul>'
        "</div>"
    )


def quiet_on_board(board: dict, desk_ids: dict[str, str]) -> str:
    """Open client tickets assigned to the owner that need no speaking slot today.

    Shown as one quiet strip at the foot of the standup card so the owner is
    never caught cold if the client pauses on one while scrolling the board.
    """
    owners = settings.owner_aliases()
    quiet = []
    for t in board.get("tickets") or []:
        asana = t.get("asana") or {}
        if asana.get("completed") or t.get("closed") or t.get("no_ticket_yet"):
            continue
        if not (asana.get("section") or asana.get("status")):
            continue
        assignee = str(asana.get("assignee") or "").strip().lower()
        if not assignee or (owners and not any(o in assignee for o in owners)):
            continue
        if (t.get("prep") or {}).get("script"):
            continue
        quiet.append(t)

    if not quiet:
        return ""

    chips = []
    for t in quiet:
        ref = str(t.get("ref") or "")
        st = str((t.get("asana") or {}).get("status") or "open")
        tip = _one_line(str(t.get("where_it_stands") or ""), 150)
        desk_id = desk_ids.get(ref, "")
        chips.append(
            f'<a class="nk-q-chip" href="#{esc(desk_id)}" data-goto="desk" '
            f'title="{esc(tip)}"><b>{esc(ref)}</b><span>{esc(st)}</span></a>'
        )
    return (
        '<div class="nk-quiet">'
        '<span class="nk-q-k">Quiet on board</span>'
        '<span class="nk-q-sub">already covered, speak only if asked:</span>'
        f'<div class="nk-q-chips">{"".join(chips)}</div>'
        "</div>"
    )


def prep_jump(
    tickets: list[dict],
    ids: dict[str, str],
    has_also: bool = False,
    has_xws: bool = False,
    has_huddle: bool = False,
) -> str:
    """The same jump bar as the work view, in the order the meeting walks."""
    chips = ['<a href="#prep-need">Before call</a>']
    if has_huddle:
        chips.append('<a href="#prep-huddle">Huddle</a>')
    if has_xws:
        chips.append(f'<a href="#prep-xws">{esc(rollup_name())}</a>')
    if has_also:
        chips.append('<a href="#prep-also">Also today</a>')
    for n, t in enumerate(tickets, 1):
        ref = t.get("ref", "")
        prep = t.get("prep") or {}
        slot, _ = _slot_badge(prep, n)
        chips.append(
            f'<a class="tkt" href="#{esc(ids[ref])}">{esc(ticket_tag(t))}'
            f'<span class="ord">#{esc(slot)}</span></a>'
        )
    return f"""
  <nav class="jump">
    <span class="lab">Jump to</span>
    <div class="jump-track">{"".join(chips)}</div>
    <button class="find" data-find type="button">Find <kbd>/</kbd></button>
  </nav>"""


def whole_day(sess: dict) -> str:
    """The rest of the meeting, folded, for a long session with other people's
    topics in it."""
    rows = sess.get("agenda", [])
    if not rows:
        return ""
    out = "".join(
        f"""
      <li>
        <span class="ag-t">{esc(r.get("topic"))}</span>
        <span class="ag-w">{esc(r.get("why"))}</span>
        {f'<span class="ag-o">{esc(r.get("owner"))}</span>' if r.get("owner") else ""}
      </li>"""
        for r in rows
    )
    return f"""
      <details class="day-fold">
        <summary><span class="ev-d">The whole day</span>
        <span class="ev-c">{len(rows)} items, yours and everyone else's</span>
        <span class="fold-hint"></span></summary>
        <ul class="agenda">{out}</ul>
      </details>"""


def render_also(board: dict, focused: dict, ids: dict[str, str]) -> str:
    """The other rooms the owner still has to speak in the same span, folded small."""
    others = [
        s
        for s in all_sessions(board)
        if not s.get("skipped")
        and (s.get("date"), s.get("at"), s.get("kind"))
        != (focused.get("date"), focused.get("at"), focused.get("kind"))
    ]
    if not others:
        return ""
    tags = ticket_tags(board.get("tickets", []))
    blocks = []
    for s in others:
        when = when_words(s.get("date", ""), s.get("at", ""))
        rows = []
        for n in s.get("notes", []):
            ref = n.get("ref", "")
            label = tags.get(ref, ref)
            tag = (
                f'<a class="tag" href="#{esc(ids[ref])}">{esc(label)}</a>'
                if ref in ids
                else f'<span class="tag">{esc(label)}</span>'
            )
            say = ""
            if n.get("say_ja"):
                raw = plain(n.get("say_ja", ""))
                say = f"""
            <div class="also-say">
              <p class="jp">{furi(n.get("say_ja", ""))}</p>
              {f'<p class="en">{esc(n.get("say_en"))}</p>' if n.get("say_en") else ""}
              {f'<button class="copy" data-copy="{esc(raw)}">copy</button>' if raw else ""}
            </div>"""
            rows.append(
                f"""
          <li>
            {tag}
            <div class="also-b">
              <p class="also-think">{esc(n.get("think"))}</p>
              {say}
            </div>
          </li>"""
            )
        body = ""
        if s.get("focus"):
            body += f'<p class="also-focus">{esc(s.get("focus"))}</p>'
        if rows:
            body += f'<ul class="also-notes">{"".join(rows)}</ul>'
        elif not s.get("focus"):
            body += '<p class="also-none">Nothing flagged to think through yet.</p>'
        blocks.append(
            f"""
      <div class="also-sess">
        <h4 class="also-when">{esc(s.get("name"))} &middot; {esc(when)}</h4>
        {body}
      </div>"""
        )
    n_sess = len(others)
    return f"""
  <details class="also" id="prep-also" data-remember="prep-also">
    <summary>
      <span class="also-tag">Also today</span>
      <span class="also-sum">the other room{"s" if n_sess != 1 else ""}, and what to think through before you sit down</span>
      <span class="fold-hint"></span>
    </summary>
    <div class="also-in">{"".join(blocks)}</div>
  </details>"""


def render_xws(board: dict, ids: dict[str, str]) -> str:
    """The programme-wide rollup, one folded card pinned at the top."""
    xws = board.get("xws") or {}
    if not xws.get("for_date") and not xws.get("headline") and not xws.get("raised"):
        return ""
    when = when_words(xws.get("for_date", ""), xws.get("at", ""))
    tags = ticket_tags(board.get("tickets", []))
    raised = xws.get("raised", [])
    rows = []
    for r in raised:
        ref = r.get("ref", "")
        label = tags.get(ref, ref)
        tag = (
            f'<a class="tag" href="#{esc(ids[ref])}">{esc(label)}</a>'
            if ref in ids
            else f'<span class="tag">{esc(label)}</span>'
        )
        say = []
        for line in r.get("say", []):
            raw = plain(line.get("ja_ruby", ""))
            say.append(
                f"""
            <div class="xws-say">
              <p class="jp">{furi(line.get("ja_ruby", ""))}</p>
              {f'<p class="en">{esc(line.get("en"))}</p>' if line.get("en") else ""}
              {f'<button class="copy" data-copy="{esc(raw)}">copy</button>' if raw else ""}
            </div>"""
            )
        rows.append(
            f"""
        <li>
          {tag}
          <div class="xws-b">
            {f'<p class="xws-why">{esc(r.get("why"))}</p>' if r.get("why") else ""}
            {"".join(say)}
          </div>
        </li>"""
        )
    ws = settings.workstream()
    if rows:
        body = f'<ul class="xws-list">{"".join(rows)}</ul>'
        sum_line = (
            f"{len(raised)} {ws + ' ' if ws else ''}item{'s' if len(raised) != 1 else ''} "
            "big enough to raise at the programme rollup"
        )
    else:
        body = (
            f'<p class="xws-none">Nothing from {esc(ws or "your work")} is big enough '
            "to raise this week.</p>"
        )
        sum_line = f"nothing from {ws or 'your work'} needs the room this week"
    lead = (
        f'<p class="xws-lead">{esc(xws.get("headline"))}</p>'
        if xws.get("headline")
        else ""
    )
    return f"""
  <details class="xws" id="prep-xws" data-remember="prep-xws">
    <summary>
      <span class="xws-tag">{esc(rollup_name())}{f" &middot; {esc(when)}" if when else ""}</span>
      <span class="xws-sum">{esc(sum_line)}</span>
      <span class="fold-hint"></span>
    </summary>
    <div class="xws-in">
      {lead}
      {body}
    </div>
  </details>"""


MS_WORDS = {"on-track": "🟢 On track", "at-risk": "🟠 At risk", "off-track": "🔴 Off track",
            "not-started": "⚪ Not started", "done": "✅ Done"}
BLOCK_WORDS = {"blocked": "🔴 Blocked", "waiting": "🟡 Waiting", "resolved": "✅ Resolved"}


def huddle_milestones(board: dict) -> list[dict]:
    """Every open milestone the huddle talks about, merged with what the prep
    wrote for it (`huddle.milestones`, keyed by `id`). Board milestones with
    `in_huddle: false` (internal projects outside the huddle database) stay out."""
    h = board.get("huddle") or {}
    said = {m.get("id"): m for m in h.get("milestones") or [] if isinstance(m, dict)}
    out = []
    for ms in board.get("milestones") or []:
        if not isinstance(ms, dict):
            continue
        if ms.get("in_huddle") is False:
            said.pop(ms.get("id"), None)
            continue
        if ms.get("status") == "done" and ms.get("id") not in said:
            continue
        out.append(dict(ms, **{k: v for k, v in said.pop(ms.get("id"), {}).items() if v}))
    out += [m for m in said.values() if m.get("name") or m.get("update")]
    return out


def huddle_blockers(h: dict) -> list[dict]:
    """`blockers` as rows."""
    rows = []
    for b in h.get("blockers") or []:
        row = b if isinstance(b, dict) else {"blocker": str(b)}
        if re.fullmatch(r"\s*no blockers?\.?\s*", row.get("blocker", ""), re.I):
            continue
        rows.append(row)
    return rows


def render_huddle(board: dict) -> str:
    """The team huddle card: milestones first, then blockers."""
    h = board.get("huddle") or {}
    mss = huddle_milestones(board)
    blockers = huddle_blockers(h)
    plate = h.get("plate") or []
    if not h.get("for_date") and not mss and not blockers:
        return ""
    for_date = str(h.get("for_date") or "")
    today_iso = settings.now().date().isoformat()
    is_stale = bool(for_date and for_date < today_iso)
    when = when_words(for_date, h.get("at", ""))

    def copy(text: str) -> str:
        return f'<button class="copy" data-copy="{esc(text)}">copy</button>' if text else ""

    ms_rows = ""
    for ms in mss:
        st = ms.get("status", "")
        conf = ms.get("confidence", "")
        target = ms.get("target", "")
        update = ms.get("update", "")
        ms_rows += f"""
        <li class="hd-ms {esc(st)}">
          <div class="hd-top">
            <b>{esc(ms.get("name") or ms.get("name_ja") or ms.get("id", ""))}</b>
            <span class="hd-st">{esc(MS_WORDS.get(st, st))}</span>
            {f'<span class="hd-due">{esc(day_words(target))} &middot; {esc(render_milestone.days_left(target))}</span>' if target else ""}
            {f'<span class="hd-conf">{esc(conf)}</span>' if conf else ""}
          </div>
          {f'<p class="hd-up">{esc(update)} {copy(update)}</p>' if update else '<p class="hd-none">No update written for this week yet.</p>'}
        </li>"""
    ms_block = (f'<p class="xws-lead"><b>Milestones</b></p><ul class="hd-list">{ms_rows}</ul>'
                if ms_rows else "")

    bl_rows = ""
    for b in blockers:
        st = b.get("status", "blocked")
        meta = " &middot; ".join(esc(x) for x in [
            b.get("milestone", ""),
            f"waiting on {b['waiting_on']}" if b.get("waiting_on") else "",
            f"next: {b['next_step']}" if b.get("next_step") else "",
        ] if x)
        bl_rows += f"""
        <li class="hd-bl {esc(st)}">
          <div class="hd-top"><b>{esc(b.get("blocker", ""))}</b>
            <span class="hd-st">{esc(BLOCK_WORDS.get(st, st))}</span>{copy(b.get("blocker", ""))}</div>
          {f'<p class="hd-meta">{meta}</p>' if meta else ""}
        </li>"""
    bl_block = (f'<p class="xws-lead"><b>Blockers</b></p><ul class="hd-list">{bl_rows}</ul>'
                if bl_rows else '<p class="xws-none">No blockers.</p>')

    plate_block = ""
    if plate:
        items = "".join(f"<li>{esc(x)}</li>" for x in plate)
        plate_block = f"""
      <details class="hd-plate" data-remember="prep-huddle-plate">
        <summary>On my plate, if asked ({len(plate)})</summary>
        <ul>{items}</ul>
      </details>"""

    risky = sum(1 for m in mss if m.get("status") in ("at-risk", "off-track"))
    sum_line = ", ".join(filter(None, [
        f"{len(mss)} milestone{'s' if len(mss) != 1 else ''}" if mss else "",
        f"{risky} at risk" if risky else "",
        f"{len(blockers)} blocker{'s' if len(blockers) != 1 else ''}" if blockers else "no blockers",
    ]))
    link = room_link(("huddle",))
    link_line = (f'<p class="xws-link"><a href="{esc(link)}" target="_blank" rel="noopener">'
                 f'Open the huddle page</a></p>' if link else "")
    stale_pill = (f'<span class="xws-stale">Out of date &middot; {esc(day_words(for_date))}</span>'
                  if is_stale else "")
    stale_warn = (
        f'<p class="xws-warn">Out of date: written for {esc(when)}. '
        f'Press <b>Write prep</b> or <b>Refresh</b> before copying into Notion.</p>'
        if is_stale else ""
    )
    return f"""
  <details class="xws hd{' is-stale' if is_stale else ''}" id="prep-huddle" data-remember="prep-huddle">
    <summary>
      <span class="xws-tag">{esc(huddle_name())}{f" &middot; {esc(when)}" if when else ""}</span>
      {stale_pill}
      <span class="xws-sum">{esc(sum_line)}</span>
      <span class="fold-hint"></span>
    </summary>
    <div class="xws-in">
      {stale_warn}
      {link_line}
      {ms_block}
      {bl_block}
      {plate_block}
    </div>
  </details>"""


def last_session(board: dict, sess: dict) -> str:
    """The date of the meeting before this one, for 'what moved since'."""
    note = (board.get("meeting_note") or {}).get("date")
    if note:
        return note
    try:
        return (date.fromisoformat(sess.get("date", "")) - timedelta(days=7)).isoformat()
    except ValueError:
        return ""


def render(board: dict, desk_ids: dict[str, str], built: str, stale: bool) -> str:
    """The speaking half of the page."""
    sess = script_session(board)
    onsite = sess.get("kind") != "standup"
    lead = script_meta(board).get("headline") or ""
    room_sess = dict(sess, focus="") if lead else sess
    banner = render_session(room_sess, onsite)

    tickets = [t for t in board.get("tickets", []) if (t.get("prep") or {}).get("script")]
    if not tickets:
        when = when_words(sess.get("date", ""), sess.get("at", ""))
        for_what = f"for {sess.get('name', 'the standup').lower()}" + (
            f" {when}" if when else ""
        )
        extra = (
            " An onsite gets the longer treatment: what has to be decided in the "
            "room, and the answer ready for the pushback."
            if onsite
            else ""
        )
        empty_ids = {
            ref: anchor(desk_id) for ref, desk_id in desk_ids.items()
        }
        return f"""
  {render_huddle(board)}
  {render_milestone.speaking(board)}
  {render_xws(board, empty_ids)}
  {banner}
  <div class="st-empty">
    <h2>Nothing written yet {esc(for_what)}</h2>
    <p>Press <strong>Write prep</strong> and it reads every open ticket, the
    threads behind them and the last meeting's decisions, then writes what moved,
    what you say and what you need out of the room.{esc(extra)} Takes a couple of
    minutes.</p>
  </div>"""

    tickets.sort(key=lambda t: (t.get("prep") or {}).get("order", 99))
    ids = {t.get("ref", ""): anchor(desk_ids.get(t.get("ref", ""), "")) for t in tickets}
    since = last_session(board, sess)
    xws = render_xws(board, ids)
    huddle = render_huddle(board)
    also = render_also(board, sess, ids)
    cards = "".join(
        render_ticket(t, ids[t.get("ref", "")], desk_ids.get(t.get("ref", ""), ""), since)
        for t in tickets
    )
    stale_note = (
        '<div class="st-stale">Written before the last refresh, so it may not know '
        "the newest replies. Press Update prep if that matters.</div>"
        if stale
        else ""
    )
    settle = sum(len((t.get("prep") or {}).get("decisions", [])) for t in tickets)
    asks = sum(
        1
        for t in tickets
        if (t.get("prep") or {}).get("client_ask_needed") is not False
        or (t.get("prep") or {}).get("decisions")
    )
    status_only_n = len(tickets) - asks
    room = esc(sess.get("title") or settings.meeting().get("name") or sess.get("name"))
    when = esc(when_words(sess.get("date", ""), sess.get("at", "")))
    parts = [f"{len(tickets)} to speak on"]
    if asks:
        parts.append(f"{asks} needing {settings.client_short()}'s answer")
    if status_only_n:
        parts.append(f"{status_only_n} status-only")
    if settle:
        parts.append(f"{settle} to settle today")
    speak = ", ".join(parts)
    pre_box = before_start_box(board, tickets, ids, since)
    quiet_box = quiet_on_board(board, desk_ids)

    return f"""
  {prep_jump(tickets, ids, bool(also), bool(xws), bool(huddle))}
  {stale_note}
  {huddle}
  {render_milestone.speaking(board)}
  {xws}
  <details class="nk" id="prep-need" data-remember="prep-need" open>
    <summary>
      <span class="nk-k">Before {esc(sess.get("tab") or sess.get("name") or "Standup")}</span>
      <span class="nk-n">main status, goal, and running order</span>
      <span class="fold-hint"></span>
      <span class="nk-sum"><b>{room}, {when}.</b> {esc(speak)}.</span>
    </summary>
    <div class="nk-in">
      {f'<p class="nk-alert plain"><b>Goal in the room</b>{esc(lead)}</p>' if lead else ""}
      {pre_box}
      {banner}
      <h3 class="run-h">The order they reach your tickets<span class="q">card slot on {esc(settings.client_short())}'s board + what you say</span></h3>
      {running_order(tickets, ids)}
      {quiet_box}
      {whole_day(sess)}
    </div>
  </details>
  {also}
  {cards}
  <p class="foot">Written {esc(built)}. <kbd>1</kbd> your work,
  <kbd>2</kbd> what you say, <kbd>s</kbd> {script_only_words()}, <kbd>/</kbd> find
  anything, <kbd>?</kbd> how this works.</p>"""
