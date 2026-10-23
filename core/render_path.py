#!/usr/bin/env python3
"""The path to closing a ticket: its gates, and the work hanging off them.

Split out of `render_desk.py`, which had grown past 2200 lines and held eight
unrelated jobs in one file. This is the biggest single thing a ticket card
draws and the part most likely to be changed, so it gets its own file: the
spine, the items on it, and everything one item can carry (its steps, its
draft, the work already done for the owner).

`spine()` is the join that makes the merged section possible, and the comment
on it is the one to read before touching any of this. A gate names the items it
waits on in prose, not in a field, and an item no gate names is still the owner's work
and still gets a row.
"""

from __future__ import annotations

import re
from pathlib import Path

import board as B
import people
import render_standup
import settings
from render import (
    ASKED,
    SPARK,
    after_posting,
    apply_glossary,
    ask_block,
    blocked_on,
    status_button,
    code,
    due_words,
    esc,
    furi,
    item_order,
    item_state as state_of,
    link_btn,
    pill,
    plain,
    section,
    sub_line,
    when_tag,
)


ITEM_REF = re.compile(r"\bitems?\s+(\d+)", re.I)

def cell(value: object) -> str:
    """One cell of a prepared table, plain or carrying a verdict.

    A four-column comparison where every cell is a sentence hides the row that
    is the point of it. A cell written as {"text", "tone"} prints as a verdict
    instead, so the answer is findable before the evidence is read.
    """
    if isinstance(value, dict):
        tone = str(value.get("tone", "warn"))
        tone = tone if tone in ("good", "gap", "warn") else "warn"
        return f'<td class="pw-v"><span class="v-{tone}">{code(value.get("text"))}</span></td>'
    return f"<td>{code(value)}</td>"


def doc_btns(files: list) -> str:
    """The documents an item hands the owner, opened from the page it sits on.

    A handover sheet that lives only in the repo is a file they have to go and
    find, and one pasted into the page as prose is not the thing they give
    anybody. The server carries docs/ at /doc/, so the button opens the real
    file, in the form the reader gets it.

    Some of what the work produces cannot live in `docs/` at all: a Miro board is
    the product of an afternoon and belongs beside the file it explains, so an
    entry may name a `url` instead of a `path`.
    """
    out = []
    for f in files or []:
        path = str(f.get("path", "")).strip()
        url = str(f.get("url", "")).strip()
        if not path and not url:
            continue
        label = f.get("label") or Path(path).name or url
        if path.startswith(("http://", "https://")):
            href = path
        elif path:
            rel = re.sub(r"^(?:clients/[^/]+/)?(?:docs|scripts)/", "", path).lstrip("/")
            href = f"/doc/{rel or Path(path).name}"
        else:
            href = url
        out.append(link_btn(href, label))
    return " ".join(o for o in out if o)


item_drafts = B.drafts


def render_drafts(r: dict, st: dict | None = None) -> str:
    """The job's messages, each its own fold, above the evidence.

    A second message used to live as a note inside `prepared`, two folds deep,
    and the owner could not find the reply they had to send. So every message
    gets the same box in the same place, open while it is theirs to send, and each folds
    shut on its own when there are several.
    """
    drafts = item_drafts(r)
    many = len(drafts) > 1
    return "".join(
        render_draft(
            d, st, f"Reply {n} of {len(drafts)}" if many else "Reply to send", r, n
        )
        for n, d in enumerate(drafts, 1)
    )


def posted_bar(r: dict, k: int) -> str:
    """The press that says this message went, at the foot of the box it came from.

    Copy, paste it where it goes, come back, press this. It used to be a step
    reading "Then ./tick.py 52 once it is posted", which needed a terminal for
    the one move every draft ends in. The button says before it is pressed what
    it will do to the job, because sending is not finishing: a reply owed puts
    the job with that person, nothing owed closes it, and a second message still
    to send leaves it with the owner. Hidden until desk.js shows it, since a page
    opened from disk has no server to tell.
    """
    label, who = after_posting(r, k)
    kind = (
        "more" if label.endswith("more to send")
        else "ask" if label == "say who replies"
        else "wait" if who
        else "close"
    )
    ids = ",".join(p["id"] for p in people.find(who)) if who else ""
    return f"""
          <div class="draft-foot" hidden>
            <span class="df-hint">Once it is posted</span>
            <button type="button" class="posted" data-posted="{esc(r.get("id", ""))}"
                    data-draft="{k}" data-after="{kind}" data-who="{esc(people.said(who))}"
                    data-whoids="{esc(ids)}" data-title="{esc(r.get("title", ""))}">
              <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.5l2.5 2.5 4.5-5.5"/></svg>
              <b>Posted it</b><span class="posted-then">{esc(label)}</span>
            </button>{"" if kind == "more" else f'''<button type="button" class="posted-more"
                    aria-haspopup="menu" aria-label="Posted, but someone else replies or nothing comes back"
                    title="Someone else replies, or nothing comes back">
              <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 4l2.5 2.5L7.5 4"/></svg></button>'''}
          </div>"""


def render_draft(
    d: dict,
    st: dict | None = None,
    label: str = "Reply to send",
    item: dict | None = None,
    k: int = 1,
) -> str:
    """A draft to paste, or, once it has gone, a folded record of what went."""
    if not d:
        return ""
    is_ja = d.get("language") == "ja"
    # Two shapes of draft. A draft in the client's language keeps its text, with any
    # {word|reading} markup, in body_ruby, and body_en is the translation shown
    # beneath it. The `language` value is "ja" for historical reasons: it marks a
    # draft in the client's language. An English draft (to a Kraken colleague)
    # keeps its text in body_en and has no body_ruby. Reading body_ruby for both
    # rendered every English draft blank: the text was saved, never shown.
    # Both branches fall back through the other body keys, so a draft whose text
    # landed under body_ja or a bare body still renders instead of showing an empty
    # box: furi() passes plain text through untouched, so the worst case is no ruby,
    # never a blank card. board.check() refuses a draft with no body at all.
    if is_ja:
        body = d.get("body_ruby") or d.get("body_ja") or d.get("body", "")
        rendered = furi(body)
        trans = (
            f'<p class="draft-en">{esc(d.get("body_en"))}</p>'
            if d.get("body_en")
            else ""
        )
    else:
        body = d.get("body_en") or d.get("body_ruby") or d.get("body", "")
        rendered = esc(body)
        trans = ""
    # The draft is the thing the owner most often wants changed rather than explained,
    # and the box that changes it is at the foot of the card. This opens that box
    # with the ask already started, so a rewrite is one click from the words.
    inner = f"""
          <div class="draft-head">
            <span>{esc(d.get("target"))}</span>
            {link_btn(d.get("link", ""), "Go there")}
            <button class="copy" data-copy="{esc(plain(body))}">copy</button>
            <button class="ask-here" type="button"
                    data-ask-seed="Rewrite the draft {esc(d.get("target", ""))}: ">{SPARK}Rewrite or ask</button>
          </div>
          <div class="draft-body {"ja" if is_ja else ""}">{rendered}</div>
          {trans}"""
    gone = (st and st["state"] != "todo" and st["state"] != "hold") or not B.unsent(d)
    if gone:
        at = str(d.get("sent_at") or "")[11:16] or (st.get("at") if st else "")
        when = f" at {esc(at)}" if at else ""
        return f"""
        <details class="act-draft sent">
          <summary>What you sent{when}: {esc(d.get("target"))}</summary>
          {inner}
        </details>"""
    return f"""
        <details class="act-draft live" open data-draft-of="{esc((item or {}).get("id", ""))}">
          <summary><b>{esc(label)}</b> {esc(d.get("target"))}</summary>
          {inner}
          {posted_bar(item, k) if item else ""}
        </details>"""


# A finding is written as a claim and then the evidence for it: "The four
# reasons do not exist in the product. MANUAL_LATE_PAYMENT_FEE_GAS appears
# nowhere in the repository, and neither does..." The claim is the overview and
# the rest is the proof, so the page shows the claims as a list and keeps each
# proof one press away.
LEAD = re.compile(r"^(.{20,200}?[.。])\s+(?=\S)")
# Several findings are written as a dated addendum. The date is not the claim.
DATED = re.compile(r"^(\[[^\]]{2,24}\]\s*)")


def claim(text: str) -> tuple[str, str, str]:
    """A finding split into its date, its first claim, and the evidence under it."""
    body = str(text or "").strip()
    when = ""
    mark = DATED.match(body)
    if mark:
        when = mark.group(1).strip().strip("[]")
        body = body[mark.end():]
    lead = LEAD.match(body)
    if not lead:
        return when, body, ""
    return when, lead.group(1).strip(), body[lead.end():].strip()


def finding_html(text: str) -> str:
    """One finding: the claim on a line, the proof behind it."""
    when, head, rest = claim(text)
    tag = f'<span class="pw-when">{esc(when)}</span>' if when else ""
    if not rest:
        return f'<li class="pw-one">{tag}<span>{code(head)}</span></li>'
    return f"""<li class="pw-one has-more"><details>
                <summary>{tag}<span>{code(head)}</span><span class="fold-hint"></span></summary>
                <p>{code(rest)}</p></details></li>"""


def conclusion_html(text: object) -> str:
    """The bottom line, with its first sentence as the line.

    A conclusion is meant to be one sentence the owner could act on. Some run to four,
    and then the first is the answer and the rest is the qualification, so the
    first stands at full weight and the rest sits under it, smaller.
    """
    if not text:
        return ""
    _, head, rest = claim(str(text))
    more = f'<span class="pw-more">{code(rest)}</span>' if rest else ""
    return f'<div class="pw-answer"><b>Bottom line</b><span class="pw-line">{code(head)}</span>{more}</div>'


def prepared_block(p: dict, meeting_href: str = "") -> str:
    """Work that was already done for the owner, above the steps that remain.

    If a step can be followed without judgement then following it was never their
    job, so the page carries the product rather than the instruction. What is
    left underneath is the part only they can do: check it, decide with it, say it.

    The part that needed fixing is how much of it arrives at once. A full trace
    through the code is twelve findings, four essays and a table, and printed
    whole it is three screens with the answer somewhere inside: the owner could
    not tell what mattered or what to act on. So the shape is now the shape of an
    answer. The bottom line and what is still unanswered stand on their own; the
    evidence is one scannable claim per line behind a single fold, and each claim
    opens to its proof. Nothing is dropped, because the proof is the reason the
    conclusion can be trusted; it is just no longer in the way.
    """
    if not p:
        return ""
    head = ""
    tbl = p.get("table") or {}
    if tbl.get("rows"):
        cols = "".join(f"<th>{code(c)}</th>" for c in tbl.get("columns", []))
        body = "".join(
            "<tr>" + "".join(cell(c) for c in row) + "</tr>" for row in tbl["rows"]
        )
        head = f'<table class="pw-t"><thead><tr>{cols}</tr></thead><tbody>{body}</tbody></table>'
    finds = "".join(finding_html(f) for f in p.get("findings", []))
    notes = "".join(
        f'<details class="pw-note"><summary><b>{esc(n.get("heading"))}</b>'
        f'<span class="fold-hint"></span></summary>{code(n.get("body"))}</details>'
        for n in p.get("notes", [])
        if n.get("heading") and n.get("body")
    )
    meeting = p.get("meeting_use") or {}
    meeting_block = ""
    if meeting and meeting_href:
        meeting_block = (
            '<div class="pw-meeting">'
            f'<span>{esc(meeting.get("summary", "Use this in the next session."))}</span>'
            f'<a class="btn" href="#{esc(meeting_href)}" data-goto="standup">'
            f'{esc(meeting.get("label", "Open meeting version"))}</a></div>'
        )
    open_qs = "".join(f"<li>{code(q)}</li>" for q in p.get("unanswered", []))
    docs = doc_btns(p.get("files", []))
    srcs = " ".join(
        link_btn(s.get("url", ""), s.get("label", "Source"))
        for s in p.get("sources", [])
        if s.get("url")
    )
    # The evidence, counted in words on the fold so the owner knows what is behind it
    # without opening it. Three findings and nothing else is not worth a fold, so
    # a small block stays open and only a long one shuts.
    n_find = len(p.get("findings") or [])
    n_note = len([n for n in p.get("notes") or [] if n.get("heading") and n.get("body")])
    tally = ", ".join(
        part
        for part in (
            f"{n_find} finding{'' if n_find == 1 else 's'}" if n_find else "",
            f"{n_note} note{'' if n_note == 1 else 's'}" if n_note else "",
            "a table" if head else "",
        )
        if part
    )
    inside = f"""{head}
              {f'<ul class="pw-f">{finds}</ul>' if finds else ""}
              {notes}"""
    heavy = n_find + n_note > 3 or (head and n_find + n_note > 1)
    built_from = f'<div class="pw-src"><span>Built from</span>{srcs}</div>' if srcs else ""
    if tally and heavy:
        # The sources go inside with the evidence: they are how the owner checks it,
        # and checking it is the only reason to open the fold.
        evidence = f"""
              <details class="pw-ev">
                <summary>The evidence: {esc(tally)}<span class="fold-hint"></span></summary>
                {inside}
                {built_from}
              </details>"""
        built_from = ""
    else:
        evidence = inside if tally else ""
    return f"""
            <div class="act-prep">
              <p class="act-lab done">Prepared for you{f' &middot; {esc(p.get("built_at"))}' if p.get("built_at") else ""}
                <button class="ask-here" type="button"
                        data-ask-seed="About the prepared work: ">{SPARK}Ask about this</button></p>
              {f'<p class="pw-what">{code(p.get("what"))}</p>' if p.get("what") else ""}
              {conclusion_html(p.get("conclusion"))}
              {f'<div class="pw-open"><b>Still open</b><ul>{open_qs}</ul></div>' if open_qs else ""}
              {evidence}
              {f'<div class="pw-src"><span>Files it made</span>{docs}</div>' if docs else ""}
              {meeting_block}
              {built_from}
              {earlier_block(p.get("earlier") or [])}
            </div>"""


def earlier_block(rows: list) -> str:
    """What a rewrite condensed away, kept by board.save() and shown under a fold.

    A sweep is asked to keep the block to four findings, and that is right for
    reading it. But a line reference or a comment id it dropped is exactly what
    the owner may need in the room, so it is kept, newest first, one click down.
    """
    rows = [r for r in rows if isinstance(r, dict) and r.get("text")]
    if not rows:
        return ""
    lines = "".join(
        f'<li><span class="pw-when">{esc(str(r.get("at", ""))[5:16].replace("T", " "))}</span>'
        f"{code(r['text'])}</li>"
        for r in rows
    )
    return f"""
              <details class="pw-ev pw-earlier">
                <summary>Earlier detail: {len(rows)} line{'' if len(rows) == 1 else 's'} a later
                rewrite took out<span class="fold-hint"></span></summary>
                <ul class="pw-f">{lines}</ul>
              </details>"""


def stands_block(r: dict, done: bool) -> str:
    """Where the job is, above what to do about it, and labelled as such.

    Without it the status leaked into the steps: "the new hold has been on since
    Tuesday, and several hundred accounts carry it" read as an instruction and was
    not one."""
    text = r.get("where_it_stands")
    if not text or done:
        return ""
    return f'<p class="act-stands"><b>Where it stands</b>{code(text)}</p>'


def steps_list(r: dict) -> str:
    """Each step as a line of a checklist the owner ticks off on the page.

    The number is the box. Pressing it strikes the step through and moves the
    highlight to the next one, so a card with four steps says at a glance which
    one they are on, rather than four equal lines they have to remember their
    way down. It never moves the job: only the status and Posted it do that.
    """
    n = esc(r.get("id", ""))
    out, first = [], True
    for k, text in enumerate(r.get("steps") or [], 1):
        text = str(text)
        when = B.step_done(r, text)
        cls = "ds done" if when else "ds next" if first else "ds"
        first = first and bool(when)
        said = f"Step {k} done {esc(when[:10])}, press to put it back" if when else f"Mark step {k} done"
        out.append(
            f'<li class="{cls}" data-step-k="{k}">'
            f'<button type="button" class="ds-n" data-step-job="{n}" data-step="{k}" '
            f'aria-pressed="{"true" if when else "false"}" aria-label="{said}" title="{said}">'
            f'<span class="ds-k">{k}</span>'
            f'<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.5l2.5 2.5 4.5-5.5"/></svg>'
            f'</button><span class="ds-b"><span class="ds-t">{code(text)}</span></span></li>'
        )
    return "".join(out)


def steps_count(r: dict) -> str:
    """"1 of 4 done" beside the label, which desk.js keeps true as they tick."""
    steps = [s for s in r.get("steps") or [] if isinstance(s, str)]
    ticked = len(steps) - len(B.open_steps(r))
    if not steps:
        return ""
    look = ' class="steps-count all"' if ticked == len(steps) else ' class="steps-count"'
    none = " data-none" if not ticked else ""
    return f'<span{look}{none}>{ticked} of {len(steps)} done</span>'


def steps_block(r: dict, steps: str, active: bool) -> str:
    """The steps, first thing inside the item, with somewhere to do them.

    This block used to open with Why, which is the one question the owner never
    asks of their own list: they know why it is there, they want to know what to
    type. So
    the hands-on part comes first, numbered because they are in an order, with
    the place to do it attached to the last step rather than floating below.
    """
    where = esc(r.get("where") or "")
    go = link_btn(r.get("link", ""), "Open where this happens")
    place = (
        f'<div class="act-where">{f"<span>{where}</span>" if where else ""}{go}</div>'
        if where or go
        else ""
    )
    if not steps:
        # A job with no steps is a job the owner has to work out from its title, which
        # is the failure this block exists to prevent. Say so, rather than
        # leaving a confident-looking gap.
        if not active:
            return place
        return f"""
            <p class="act-nosteps"><b>No steps written yet</b>Ask the chat to
            break job {esc(r.get("id", "-"))} down.</p>{place}"""
    return f"""
            <div class="act-do">
              <p class="act-lab">Do this{steps_count(r)}</p>
              <ol class="steps">{steps}</ol>
              {place}
            </div>"""


def background(r: dict, quote: str) -> str:
    """Why this job exists, folded, under the work it explains.

    Why it matters, what already happened and the message it came out of are
    three paragraphs the owner reads once, when the job first appears, and never
    again. Open they pushed the next job off the screen; shut they are one line
    they can
    press when a job has stopped making sense.
    """
    inside = "".join(
        [
            f'<p class="act-why"><b>Why it matters</b>{esc(r.get("why"))}</p>'
            if r.get("why")
            else "",
            f'<p class="act-quote">Already happened: {esc(r.get("progress_note"))}</p>'
            if r.get("progress_note")
            else "",
            f'<p class="act-quote">{esc(quote)} '
            f'{link_btn(r.get("source_url", ""), "Source") if r.get("source_url") else ""}</p>'
            if quote
            else "",
        ]
    )
    if not inside:
        return ""
    return f"""
            <details class="act-more">
              <summary>Why this is here<span class="fold-hint"></span></summary>
              {inside}
            </details>"""


def item_html(
    r: dict,
    raise_label: str = "Raise at standup",
    sess: dict | None = None,
    standup_href: str = "",
    ticket_ref: str = "",
    index: dict[str, dict] | None = None,
) -> str:
    """One job, as the row the spine hangs it on.

    Split out of `render_items` so the same job renders identically whether it
    sits under a gate on the path or on its own. The markup, the classes and the
    ask box are the ones that were already here: only who calls it changed.
    """
    return render_items(
        [r], raise_label, sess, standup_href, ticket_ref, index, bare=True
    )


def render_items(
    rows: list[dict],
    raise_label: str = "Raise at standup",
    sess: dict | None = None,
    standup_href: str = "",
    ticket_ref: str = "",
    index: dict[str, dict] | None = None,
    bare: bool = False,
) -> str:
    if not rows:
        return section(
            "To do on this ticket",
            '<p class="empty">Nothing on this one needs you. It is here because '
            "it is still open in Asana.</p>",
            role="now",
        )
    index = index if index is not None else {str(r.get("id")): r for r in rows}
    out = []
    for r in sorted(rows, key=lambda x: item_order(x, index)):
        steps = steps_list(r)
        mins = r.get("est_minutes")
        committed = r.get("committed_to")
        blocked = r.get("blocked_by")
        quote = r.get("source_quote")
        hold = r.get("hold") or {}
        st = state_of(r)
        done = st["closed"]
        status_block = ""
        if done:
            note = f" {esc(st['note'])}" if st.get("note") else ""
            status_block = (
                f'<p class="closed"><b>{esc(st["label"])}</b>'
                f'{esc(st.get("at", ""))}.{note}</p>'
            )
        elif st["state"] == "waiting":
            note = f" {esc(st['note'])}." if st.get("note") else ""
            waits = r.get("waits_on") or {}
            owed = f" They owe: {esc(waits['what'])}." if waits.get("what") else ""
            chase_line = (
                f" {esc(due_words(waits['chase_on']))}" if waits.get("chase_on") else ""
            )
            sent = f"Sent {esc(st.get('at', ''))}" if st.get("sent") else "Not yours"
            status_block = (
                f'<p class="sent-note"><b>{sent}</b>'
                f'Nothing further from you until {esc(st.get("who", "they"))} '
                f"answers.{owed}{note}{chase_line}</p>"
            )
        elif hold:
            revisit = hold.get("revisit")
            status_block = f"""
            <p class="hold"><b>Do not send this yet</b>{esc(hold.get("why"))}
            <span class="hold-until">Wait for: {esc(hold.get("until"))}.</span>
            {f'<span class="hold-until"> Chase on {esc(revisit)}.</span>' if revisit else ""}</p>"""
        state_pill = status_button(r, st)
        if r.get("at_standup") and not done:
            state_pill += pill(raise_label, "amber")
        due, due_kind = when_tag(r, st, sess)
        when_chip = f'<span class="when {due_kind}">{esc(due)}</span>' if due else ""
        chase = (r.get("waits_on") or {}).get("chase_on", "")
        # Anything not sitting with the owner folds shut, so the page is only as
        # long as the work they still have. Work queued behind another job folds for the
        # same reason: it is not a decision until the one in front of it closes.
        behind = blocked_on(r, index) if st["state"] in {"todo", "hold"} else ""
        active = st["state"] in {"todo", "hold"} and not behind
        tag, attrs = ("div", "") if active else ("details", "")
        head_tag = "div" if active else "summary"
        sub = sub_line(st) or (esc(due).lower() if chase and due else "")
        if behind:
            lead = index.get(behind) or {}
            sub = f"after job {esc(behind)}"
            status_block = (
                f'<p class="act-block"><b>Queued behind job {esc(behind)}</b>'
                f"Nothing to do here until "
                f"&ldquo;{esc(lead.get('title', 'that one'))}&rdquo; closes.</p>"
            ) + status_block
            # A red "With you" on something the owner cannot start is the whole problem
            # this field exists to fix, and a due date on it is a second lie.
            state_pill = status_button(r, st, f"After {behind}", "grey") + (
                pill(raise_label, "amber") if r.get("at_standup") else ""
            )
            when_chip = ""
        out.append(
            f"""
        <{tag} class="act {st["state"]} {"held" if hold and not done else ""} {"commit" if committed else ""}" data-job="{esc(r.get("id", ""))}"{attrs}>
          <{head_tag} class="act-head">
            <span class="act-rank">{esc(r.get("id", "-"))}</span>
            <span class="act-title">{esc(r.get("title"))}
              {f'<span class="act-sub">{sub}</span>' if sub and not active else ""}</span>
            {when_chip}
            {state_pill}
            <span class="act-min">{f"{esc(mins)} min" if mins and active else ""}</span>
          </{head_tag}>
          <div class="act-body">
            {status_block}
            {stands_block(r, done)}
            {steps_block(r, steps, active)}
            {render_drafts(r, st)}
            {prepared_block(r.get("prepared") or {}, standup_href)}
            {f'<p class="act-done"><b>Finished when</b>{code(r.get("done_when"))}</p>' if r.get("done_when") and not done else ""}
            {f'<p class="act-commit">You committed this to {esc(committed)}</p>' if committed else ""}
            {f'<p class="act-block">Blocked by: {esc(blocked)}</p>' if blocked else ""}
            {background(r, quote)}
            {ask_block(
                f'item:{r.get("id")}',
                f'job {r.get("id")}, {ticket_ref}' if ticket_ref else f'job {r.get("id")}',
                ASKED.get(f'item:{r.get("id")}', []),
                f'Read prompts/ask.md, then answer this about job {r.get("id")}'
                f' on {ticket_ref} ("{r.get("title", "")}"):',
                bool(item_drafts(r)),
            )}
          </div>
        </{tag}>"""
        )
    if bare:
        return "".join(out)
    live = sum(1 for r in rows if not state_of(r)["closed"])
    done = len(rows) - live
    tally = f"{live} open" if live else "all done"
    if done:
        tally += f", {done} closed"
    return section(
        "To do on this ticket",
        "".join(out),
        role="now",
        count=tally,
        hint="each keeps its number until it closes",
    )


def gate_items(gate: dict, live: dict[str, dict]) -> list[str]:
    """Which of this ticket's items a gate names, in the order it names them.

    The link is prose: a gate's note says "Item 4." or "item 32 confirms it with
    Alex". There is no field for it, and adding one would mean a sweep
    maintaining a join it currently gets right by writing a sentence. So the
    sentence is read.

    Only ids that are really items on this ticket count, so "item 37" on a
    ticket whose 37 was dropped does not invent a row.
    """
    seen: list[str] = []
    for num in ITEM_REF.findall(f'{gate.get("what", "")} {gate.get("note") or ""}'):
        if num in live and num not in seen:
            seen.append(num)
    return seen


def spine(t: dict, rows: list[dict], items: list[dict]) -> tuple[list[dict], int, int]:
    """The one ordered path to closing this ticket: gates, with the owner's work on them.

    Three sections used to say overlapping things about the same fact. One item
    was a row in "To do", a gate in "What is left before this closes", and two
    events in the timeline, so the card said the same thing three times in three
    different voices and the owner had to read all three to be sure they agreed.

    This is the merge. A gate is a step on the path; the items that step is
    waiting on hang under it. The gate says what "done" means and whose it is;
    the item says what the owner actually types.

    **An item that no gate names is still a row of its own.** Fewer than a third
    of the gates on a real board name an item, so attaching items to gates alone
    would have hidden most of the owner's open work, which is the one thing this page exists to
    show. Orphans come first when they are the owner's and last when they are not.
    """
    live = {str(i.get("id")): i for i in items}
    claimed: set[str] = set()
    out: list[dict] = []
    for g in rows:
        ids = gate_items(g, live)
        claimed.update(ids)
        out.append({"gate": g, "items": [live[i] for i in ids]})

    loose = [i for i in items if str(i.get("id")) not in claimed]
    mine, theirs = [], []
    for i in loose:
        st = state_of(i)
        if st["closed"]:
            theirs.append(i)
        elif st["state"] == "todo":
            mine.append(i)
        else:
            theirs.append(i)
    lead = [{"gate": None, "items": [i]} for i in mine]
    tail = [{"gate": None, "items": [i]} for i in theirs]

    done = sum(1 for g in rows if g.get("state") == "done")
    return lead + out + tail, done, len(rows)


def render_path(
    t: dict,
    ident: str,
    raise_label: str,
    sess: dict | None,
    standup_href: str,
    index: dict[str, dict] | None,
    fold: bool = False,
) -> str:
    """The path to closing this ticket: every gate, with the owner's work hanging on it.

    One section where there were three. The rows that are still live are drawn
    open; the stretch that is already behind them folds into a single line,
    because "5 of 8 done" is the fact and the five are the footnote.
    """
    items = t.get("items", [])
    gates = t.get("closes_when", [])
    rows, done, total = spine(t, gates, items)
    ref = t.get("ref", "")

    def is_settled(row: dict) -> bool:
        if any(not state_of(i)["closed"] for i in row["items"]):
            return False
        g = row["gate"]
        return (g or {}).get("state") == "done" if g else True

    def row_html(row: dict) -> str:
        g, kids = row["gate"], row["items"]
        work = "".join(
            item_html(i, raise_label, sess, standup_href, ref, index) for i in kids
        )
        if not g:
            # An item no gate names. It is the owner's work all the same, so it is a step
            # on the path in its own right rather than a footnote to one. It
            # still gets a marker, or it floats beside the rail rather than on
            # it, and the column stops reading as one sequence.
            solo = state_of(kids[0]) if kids else {"state": "", "closed": False}
            dot = "done" if solo["closed"] else "mine" if solo["state"] == "todo" else "next"
            return f'<li class="step loose {dot}"><span class="g-m"></span>{work}</li>'
        state = g.get("state", "next")
        mine = any(state_of(i)["state"] == "todo" for i in kids)
        note = f'<span class="g-n">{esc(g.get("note"))}</span>' if g.get("note") else ""
        who = esc(g.get("who", ""))
        return f"""
        <li class="step {esc(state)}{" has-work" if work else ""}{" mine" if mine else ""}">
          <div class="step-head">
            <span class="g-m"></span>
            <span class="g-w">{esc(g.get("what"))}{note}</span>
            {f'<span class="g-who">{who}</span>' if who else ""}
          </div>
          {f'<div class="step-work">{work}</div>' if work else ""}
        </li>"""

    # A finished job hanging on a gate that is still open is behind the owner all the
    # same. Left on the gate it was drawn among the live work, struck through
    # but in the way, so it goes into the fold and the gate stays on its own.
    live_rows, past = [], []
    for r in rows:
        if is_settled(r):
            past.append(r)
            continue
        shut = [i for i in r["items"] if state_of(i)["closed"]]
        if shut:
            r = {**r, "items": [i for i in r["items"] if not state_of(i)["closed"]]}
            past.extend({"gate": None, "items": [i]} for i in shut)
        live_rows.append(r)
    live_html = "".join(row_html(r) for r in live_rows)
    # What is still live comes first. The stretch behind the owner is a footnote
    # under it, not a preamble to it: they open this section to find the next move.
    body = f'<ol class="steps-list">{live_html}</ol>' if live_html else (
        '<p class="empty">Every gate on this one has fallen. It stays on the '
        "page because Asana has not closed it.</p>"
    )
    if past:
        body += f"""
        <details class="step-past" data-remember="past-{esc(ident)}">
          <summary><span class="sp-n">{len(past)}</span>
            already behind you<span class="fold-hint"></span></summary>
          <ol class="steps-list">{"".join(row_html(r) for r in past)}</ol>
        </details>"""

    nxt = next((r for r in live_rows if r["gate"]), None)
    hint = ""
    if nxt:
        who = (nxt["gate"].get("who") or "").strip()
        whose = (
            "yours"
            if who.lower() in settings.owner_aliases()
            else f"with {who}"
            if who
            else ""
        )
        hint = f"Next: {nxt['gate'].get('what', '')}" + (f", {whose}" if whose else "")
        if len(hint) > 78:
            hint = hint[:77].rsplit(" ", 1)[0] + "…"
    mine_n = sum(
        1
        for i in items
        if state_of(i)["state"] == "todo" and not blocked_on(i, index or {})
    )
    count = f"{done} of {total} done" if total else ""
    if mine_n:
        count = (count + ", " if count else "") + f"{mine_n} with you"
    return section(
        "The path to closing this",
        f'<div class="path">{body}</div>',
        role="now",
        count=count,
        hint=hint,
        # Nothing on the owner: the count and the next gate are the fact, the rows
        # behind them are a click away. Open, this was a 1,000px card each.
        fold=fold,
        remember=f"path-{ident}" if fold else "",
    )


