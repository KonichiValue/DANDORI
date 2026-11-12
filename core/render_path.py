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
    day_words,
    draft_html,
    draft_plain,
    due_words,
    esc,
    esc_url,
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
        href = doc_href(f)
        if href:
            out.append(link_btn(href, f.get("label") or Path(str(f.get("path") or "")).name or href))
    return " ".join(o for o in out if o)


def doc_href(f: dict) -> str:
    """Where the page opens a prepared file: /doc/ for one in the repo, else its url."""
    path = str(f.get("path", "")).strip()
    url = str(f.get("url", "")).strip()
    if path.startswith(("http://", "https://")):
        return path
    if path:
        rel = re.sub(r"^(?:clients/[^/]+/)?(?:docs|scripts)/", "", path).lstrip("/")
        return f"/doc/{rel or Path(path).name}"
    return url


IMAGE = re.compile(r"\.(png|jpe?g|gif|webp)$", re.I)


def evidence_html(files: list) -> str:
    """Screenshots that go out with a message, drawn in its box rather than named.

    Evidence is the point of the post it goes with, so a chip reading
    "prod-dropdown.png" made the owner open it to know whether it said what the
    words claim. Drawn as a thumbnail it can be checked against the draft at a
    glance and dragged straight into Asana or Slack from the page.
    """
    out = []
    for f in files:
        href = doc_href(f)
        label = f.get("label") or Path(href).name
        where = str(f.get("where") or "").strip()
        where_html = f'<b class="ev-where">Paste: {esc(where)}</b>' if where else ""
        out.append(
            f'<a class="ev-shot" href="{esc_url(href)}" target="_blank" rel="noopener">'
            f'<img src="{esc_url(href)}" alt="{esc(label)}" loading="lazy">'
            f"{where_html}<span>{esc(label)}</span></a>"
        )
    return "".join(out)


def to_get_html(files: list) -> str:
    """Evidence the message needs and nobody has made yet, with how to make it.

    A draft that promises "screenshots attached" with nothing to attach is a
    post the owner cannot send, and the work of making the evidence was nowhere
    on the card. A file entry with `how` and no path is that work: it sits in
    the box it is for, so it is done before the words go.
    """
    rows = "".join(
        f'<li><b>{esc(f.get("label") or "Evidence")}</b>'
        f'<span>{code(f.get("how") or "")}</span></li>'
        for f in files
    )
    return f'<div class="draft-toget"><span>Still to get</span><ol>{rows}</ol></div>' if rows else ""


item_drafts = B.drafts


def render_drafts(r: dict, st: dict | None = None) -> str:
    """The job's messages: the ones still to send, then the chain of what went.

    Each live draft shows its true permanent `Draft N` number (`1` is `draft`,
    `2+` are `drafts`) and which step posts it (`for step K`), so a step saying
    "Post draft 4" always matches a box explicitly labelled `Draft 4`.
    """
    drafts = item_drafts(r)
    live = [(n, d) for n, d in enumerate(drafts, 1) if not draft_gone(d, st)]
    went = sorted(
        ((n, d) for n, d in enumerate(drafts, 1) if draft_gone(d, st)),
        key=lambda nd: str(nd[1].get("sent_at") or ""),
    )
    total = len(drafts)
    out = []
    for k, (n, d) in enumerate(live, 1):
        s_num = B.step_for_draft(r, d)
        step_tag = f"for step {s_num}" if s_num else ""
        if total == 1:
            lbl = "Draft"
        elif len(live) == 1:
            lbl = f"Draft {n}"
        else:
            lbl = f"Draft {n} ({k} of {len(live)} open)"
        out.append(render_draft(d, st, lbl, r, n, step_tag=step_tag))
    return "".join(out) + sent_chain(went, r)


def draft_gone(d: dict, st: dict | None) -> bool:
    """Whether a draft is history: posted, or its job has moved past the owner."""
    return bool((st and st["state"] not in {"todo", "hold"}) or not B.unsent(d))


def sent_when(d: dict) -> str:
    """When a message went, as the owner would say it: "today 10:20", "Mon 26 Oct 13:18"."""
    at = str(d.get("sent_at") or "")
    if len(at) < 10:
        return ""
    return f"{day_words(at[:10])} {at[11:16]}".strip()


def sent_chain(went: list[dict], r: dict) -> str:
    """Every message already sent on this job, as one fold holding a timeline.

    Shut by default and remembered per job, because the owner opens it to check
    what they said, not to work from it. The newest message is the one they are
    most likely after, so it is the one drawn open inside.
    """
    if not went:
        return ""
    last = went[-1][1]
    n = len(went)
    head = (
        f"{n} message{'' if n == 1 else 's'} sent"
        f'<span class="ch-last">last: {esc(last.get("target") or "")}'
        f'{f" &middot; {esc(sent_when(last))}" if sent_when(last) else ""}</span>'
    )
    steps = []
    for k, (num, d) in enumerate(went, 1):
        newest = k == n
        who = d.get("reply_from")
        expects = d.get("expects_reply")
        reply = (
            f"reply from {esc(who)}" if who
            else "nothing comes back" if expects is False
            else ""
        )
        steps.append(f"""
            <li class="ch-step{" newest" if newest else ""}">
              <span class="ch-dot" aria-hidden="true"></span>
              <details class="ch-msg"{" open" if newest else ""}>
                <summary><span class="ch-num">Draft {num}</span>
                  <span class="ch-at">{esc(sent_when(d) or "sent")}</span>
                  <span class="ch-to">{esc(d.get("target") or "")}</span>
                  {f'<span class="ch-reply">{reply}</span>' if reply else ""}</summary>
                {draft_inner(d, sent=True)}
                {attach_row(r, num, sent=True)}
              </details>
            </li>""")
    return f"""
        <details class="sent-chain" data-remember="chain-{esc(r.get("id", ""))}">
          <summary><span class="ch-k">{head}</span><span class="fold-hint"></span></summary>
          <ol class="chain">{"".join(steps)}</ol>
        </details>"""


def files_for(r: dict, k: int) -> list[dict]:
    """The prepared files that go out with draft `k`, by the file's `draft` number."""
    files = ((r or {}).get("prepared") or {}).get("files") or []
    return [f for f in files if isinstance(f, dict) and f.get("draft") == k]


def has_file(f: dict) -> bool:
    return bool(str(f.get("path") or "").strip() or str(f.get("url") or "").strip())


def attach_row(r: dict, k: int, sent: bool = False) -> str:
    """The files that go with this message, inside the box it is sent from.

    Twenty-eight files under one "Files it made" line told the owner what exists
    and not what to do with any of it. A file that is meant to go out with a
    message names that message (`draft`), and then it is offered where the hand
    already is, between the words and Posted it.
    """
    mine = files_for(r, k)
    have = [f for f in mine if has_file(f)]
    shots = [f for f in have if IMAGE.search(doc_href(f))]
    docs = doc_btns([f for f in have if f not in shots])
    toget = "" if sent else to_get_html([f for f in mine if not has_file(f) and f.get("how")])
    if not (docs or shots or toget):
        return ""
    lab = "Went with it" if sent else "Attach"
    row = (
        f'<div class="draft-attach"><span>{lab}</span>{docs}'
        f'{f"<div class=ev-shots>{evidence_html(shots)}</div>" if shots else ""}</div>'
        if docs or shots else ""
    )
    return row + toget


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
    label: str = "Draft",
    item: dict | None = None,
    k: int = 1,
    step_tag: str = "",
) -> str:
    """A draft still to paste, open, with its Draft N number, step badge, and Posted it bar."""
    if not d:
        return ""
    job_id = esc((item or {}).get("id", ""))
    inner = draft_inner(d, draft_num=k, job_id=str((item or {}).get("id", "")))
    step_badge = f'<span class="draft-step">{esc(step_tag)}</span>' if step_tag else ""
    return f"""
        <details class="act-draft live" id="job-{job_id}-draft-{k}" open data-draft-of="{job_id}" data-draft-k="{k}">
          <summary><b>{esc(label)}</b> {esc(d.get("target"))}{step_badge}<span class="fold-hint"></span></summary>
          {inner}
          {attach_row(item, k) if item else ""}
          {posted_bar(item, k) if item else ""}
        </details>"""


def draft_inner(d: dict, sent: bool = False, draft_num: int = 1, job_id: str = "") -> str:
    """The words of one message, with where they go, copy, and handoff to AI chat."""
    is_ja = d.get("language") == "ja"
    is_canvas = "canvas" in str(d.get("target") or "").lower() or "/docs/" in str(d.get("link") or "")
    if is_ja:
        body = d.get("body_ruby") or d.get("body_ja") or d.get("body", "")
        rendered = draft_html(body, ruby=True, keep_links=is_canvas)
        trans = (
            f'<div class="draft-en">{draft_html(d.get("body_en"), keep_links=is_canvas)}</div>'
            if d.get("body_en")
            else ""
        )
    else:
        body = d.get("body_en") or d.get("body_ruby") or d.get("body", "")
        rendered = draft_html(body, ruby=False, keep_links=is_canvas)
        trans = ""
    rewrite = "" if sent else f"""<button class="ask-here" type="button"
                    data-ask-seed="Rewrite Draft {draft_num} ({esc(d.get("target", ""))}): ">{SPARK}Rewrite</button>"""
    ctx_btn = "" if (sent or not job_id) else f"""<button class="ctx-copy" type="button"
                    data-ctx-ref="item:{esc(job_id)}" data-ctx-draft="{draft_num}"
                    title="Open Cursor in DANDORI with this draft, ticket, timeline &amp; DB X-Ray preloaded (Option-click to copy instead)">Open in Cursor &#8599;</button>"""
    return f"""
          <div class="draft-head">
            {"" if sent else f'<span>{esc(d.get("target"))}</span>'}
            {link_btn(d.get("link", ""), "Go there")}
            <button class="copy" data-copy="{esc(draft_plain(body))}" data-copy-html="{esc(draft_html(body, ruby=False, keep_links=is_canvas))}">copy</button>
            {rewrite}
            {ctx_btn}
          </div>
          <div class="draft-body {"ja" if is_ja else ""}">{rendered}</div>
          {trans}"""


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


SCRIPT = re.compile(r"\.(py|sh|sql|js)$", re.I)


def file_groups(files: list) -> str:
    """The prepared files, sorted by what the owner does with each.

    One row of twenty-eight chips was a list of everything that exists. Split by
    use it answers the question the owner opens it with: what goes in a message,
    what is the product itself, and what is only the means of making it again.
    """
    tied, made, tools = [], [], []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        path = str(f.get("path") or f.get("url") or "")
        if f.get("draft"):
            tied.append(f)
        elif SCRIPT.search(path):
            tools.append(f)
        else:
            made.append(f)
    rows = []
    for lab, group in (
        ("Goes with a message", tied),
        ("The work itself", made),
        ("Scripts that made it", tools),
    ):
        docs = doc_btns(group)
        if docs:
            rows.append(f'<div class="pw-src"><span>{lab}</span>{docs}</div>')
    return "".join(rows)


def prepared_block(p: dict, meeting_href: str = "") -> str:
    """Work already done for the owner: the answer, then how it was reached.

    The panel used to print everything the work produced, the line naming it, a
    bottom line, a table, every finding, every note, every file and every source,
    in one green box below the reply. It read as a report to file, and the owner
    went past it to the reply, which was the right instinct: the reply is where
    the work lands. So what stays in view is only what moves the job: the answer
    in a sentence, what is still open, and the meeting version when there is one.
    Files that go out with a message sit in that message's box. The rest is the
    reasoning, and it is one fold down, counted, for the moment the answer needs
    checking. Nothing is dropped.
    """
    if not p:
        return ""
    tbl = p.get("table") or {}
    table = ""
    if tbl.get("rows"):
        cols = "".join(f"<th>{code(c)}</th>" for c in tbl.get("columns", []))
        body = "".join(
            "<tr>" + "".join(cell(c) for c in row) + "</tr>" for row in tbl["rows"]
        )
        table = f'<table class="pw-t"><thead><tr>{cols}</tr></thead><tbody>{body}</tbody></table>'
    findings = p.get("findings") or []
    notes = [n for n in p.get("notes") or [] if n.get("heading") and n.get("body")]
    files = [f for f in p.get("files") or [] if isinstance(f, dict) and (f.get("path") or f.get("url"))]
    srcs = [s for s in p.get("sources") or [] if s.get("url")]
    finds = "".join(finding_html(f) for f in findings)
    note_html = "".join(
        f'<details class="pw-note"><summary><b>{esc(n.get("heading"))}</b>'
        f'<span class="fold-hint"></span></summary>{code(n.get("body"))}</details>'
        for n in notes
    )
    src_html = " ".join(link_btn(s.get("url", ""), s.get("label", "Source")) for s in srcs)
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
    open_block = (
        f'<div class="pw-open-top"><b>Open question{"s" if len(p.get("unanswered", [])) != 1 else ""} to verify</b><ul>{open_qs}</ul></div>'
        if open_qs else ""
    )

    def count(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'}" if n else ""

    tally = " &middot; ".join(
        part
        for part in (
            "a table" if table else "",
            count(len(findings), "finding"),
            count(len(notes), "note"),
            count(len(files), "file"),
            count(len(srcs), "source"),
        )
        if part
    )
    built_raw = str(p.get("built_at") or "")
    built_short = ""
    if len(built_raw) >= 16 and "T" in built_raw:
        built_short = f"{day_words(built_raw[:10])} {built_raw[11:16]}"
    elif built_raw:
        built_short = built_raw[:16]

    answer = conclusion_html(p.get("conclusion")) or (
        f'<div class="pw-answer"><b>Summary</b><span class="pw-line">{code(p.get("what"))}</span></div>'
        if p.get("what") else ""
    )
    if not (answer or tally or open_block or meeting_block or p.get("earlier")):
        return ""
    meta_bits = " &middot; ".join(x for x in (tally, built_short) if x)
    return f"""
            {open_block}
            {meeting_block}
            <details class="act-prep">
              <summary>
                <span class="pw-title">Background &amp; evidence</span>
                {f'<span class="pw-tally">{meta_bits}</span>' if meta_bits else ""}
                <span class="fold-hint"></span>
              </summary>
              <div class="act-prep-in">
                {answer}
                {table}
                {f'<ul class="pw-f">{finds}</ul>' if finds else ""}
                {note_html}
                {file_groups(files)}
                {f'<div class="pw-src"><span>Sources</span>{src_html}</div>' if src_html else ""}
                {earlier_block(p.get("earlier") or [])}
              </div>
            </details>"""


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


DRAFT_REF_RE = re.compile(r"\b(draft\s+(\d+))\b", re.I)


def link_draft_refs(html_str: str, job_id: str, live_nums: set[int]) -> str:
    """Turn `draft 4` in a step into a jump chip pointing at `#job-N-draft-4`."""
    def repl(m: re.Match) -> str:
        k = int(m.group(2))
        if k not in live_nums:
            return f'<span class="ds-dref sent">{m.group(1)} (sent)</span>'
        return f'<a class="ds-dref" href="#job-{job_id}-draft-{k}">Draft {k}</a>'

    return DRAFT_REF_RE.sub(repl, html_str)


def steps_list(r: dict) -> str:
    """Each step as a line of a checklist the owner ticks off on the page."""
    n = esc(r.get("id", ""))
    drafts = item_drafts(r)
    live_nums = {idx for idx, d in enumerate(drafts, 1) if B.unsent(d)}
    out, first = [], True
    for k, text in enumerate(r.get("steps") or [], 1):
        text = str(text)
        when = B.step_done(r, text)
        cls = "ds done" if when else "ds next" if first else "ds"
        first = first and bool(when)
        said = f"Step {k} done {esc(when[:10])}, press to put it back" if when else f"Mark step {k} done"
        rendered_step = link_draft_refs(code(text), n, live_nums)
        out.append(
            f'<li class="{cls}" data-step-k="{k}">'
            f'<button type="button" class="ds-n" data-step-job="{n}" data-step="{k}" '
            f'aria-pressed="{"true" if when else "false"}" aria-label="{said}" title="{said}">'
            f'<span class="ds-k">{k}</span>'
            f'<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.5l2.5 2.5 4.5-5.5"/></svg>'
            f'</button><span class="ds-b"><span class="ds-t">{rendered_step}</span></span></li>'
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


