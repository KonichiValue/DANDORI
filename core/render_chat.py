"""The side panel: one long conversation, beside the page rather than on it.

A card's ask box is right for a question about that card, and wrong for the
half hour of back and forth the owner kept taking to a chat window instead: on a card
every exchange folds, the composer shuts after each send, and the conversation
is somewhere down a long page. The panel is the same record, so a question asked
here is on the card too, drawn the way a chat is drawn: everything open, oldest
at the top, the composer always there, and each new question a follow-up to the
last answer unless they start a new topic.

It follows whichever conversation they picked last, the desk as a whole by
default, and any card's composer can send them here with what they had typed.

What it is about is said twice, because a dropdown reading "Job 12: Reply to
Sato-san" was not enough to know which ticket the answer was working on: the
picker names the ticket, and a strip under the header carries the job's number,
its ticket, where it stands and a link to its card.
"""

from __future__ import annotations

from render import (
    ASKED, SPARK, SEND, CLOSED_STATES, chat_thread, esc, item_state, model_picker, prefs,
)


def about(data: dict, refs: dict[str, str]) -> dict[str, dict]:
    """Every conversation the owner could be in, by ref, with what to call it."""
    out: dict[str, dict] = {}
    jobs = 0
    for t in data.get("tickets", []):
        ref = str(t.get("ref", ""))
        name = str(t.get("title_en") or t.get("title_ja") or "")
        live = [i for i in t.get("items", []) if i.get("state") not in CLOSED_STATES]
        jobs += len(live)
        out[f"ticket:{ref}"] = {
            "num": "", "tag": ref, "title": name or ref,
            "label": f"{ref}, the whole ticket",
            "state": f"{len(live)} open" if live else "Nothing open",
            "tone": "grey", "href": refs.get(ref, ""), "open": False,
        }
        for i in t.get("items", []):
            st = item_state(i)
            title = str(i.get("title", ""))
            short = title[:52] + ("…" if len(title) > 52 else "")
            out[f"item:{i.get('id')}"] = {
                "num": str(i.get("id", "")), "tag": ref, "title": title,
                "label": f"{i.get('id')} · {ref} · {short}",
                "state": st["label"], "tone": st["tone"],
                "href": refs.get(ref, ""), "open": not st["closed"],
            }
    out["board"] = {
        "num": "", "tag": "Desk", "title": "The day as a whole",
        "label": "The desk, the day as a whole",
        "state": f"{jobs} open jobs", "tone": "grey", "href": "", "open": False,
    }
    return out


def subjects(info: dict[str, dict]) -> list[tuple[str, int]]:
    """The order to offer them in: the desk, then talked about, then open jobs."""
    talked = sorted(
        (r for r in ASKED if r != "board" and r in info),
        key=lambda r: max(str(a.get("asked_at", "")) for a in ASKED[r]),
        reverse=True,
    )
    out = [("board", len(ASKED.get("board", [])))]
    out += [(r, len(ASKED[r])) for r in talked]
    out += [(r, 0) for r, v in info.items() if v["open"] and r not in talked]
    return out


def context(ref: str, v: dict) -> str:
    """The strip that says what the panel is talking about."""
    num = f'<span class="chat-num">{esc(v["num"])}</span>' if v["num"] else ""
    go = (
        f'<a class="chat-goto" href="#{esc(v["href"])}" title="Go to its card">Card &rarr;</a>'
        if v["href"] else ""
    )
    return f"""
  <div class="chat-about" data-about="{esc(ref)}">
    {num}<span class="chat-tag">{esc(v["tag"])}</span>
    <span class="chat-title" title="{esc(v["title"])}">{esc(v["title"])}</span>
    <span class="chat-state {esc(v["tone"])}">{esc(v["state"])}</span>
    {go}
  </div>"""


def render(data: dict, refs: dict[str, str] | None = None) -> str:
    p = prefs()
    info = about(data, refs or {})
    picks = subjects(info)
    ref = str(p.get("desk-chat-ref") or "board")
    ref = ref if ref in info else "board"
    shown = "" if p.get("desk-chat") == "1" else " hidden"

    def option(r: str, n: int) -> str:
        v = info[r]
        count = f" ({n})" if n else ""
        data_attrs = "".join(
            f' data-{k}="{esc(v[k])}"' for k in ("num", "tag", "title", "state", "tone", "href")
        )
        return (
            f'<option value="{esc(r)}"{" selected" if r == ref else ""}{data_attrs}>'
            f'{esc(v["label"])}{count}</option>'
        )

    talked = [x for x in picks if x[1] or x[0] == "board"]
    fresh = [x for x in picks if not x[1] and x[0] != "board"]
    opts = "".join(option(*x) for x in talked)
    if fresh:
        opts += '<optgroup label="Start one about">' + "".join(option(*x) for x in fresh) + "</optgroup>"
    return f"""
<aside id="chat" class="chat" aria-label="Ask"{shown}>
  <div class="chat-head">
    <span class="chat-t">{SPARK}Ask</span>
    <label class="chat-pick" title="Which conversation">
      <select aria-label="Which conversation">{opts}</select></label>
    <button type="button" class="chat-new" title="Ask something new rather than follow on from the last answer">New topic</button>
    <span class="chat-clear">
      <button type="button" class="chat-more" data-chat-more aria-haspopup="menu"
              aria-label="Clear conversations" title="Clear conversations">&middot;&middot;&middot;</button>
      <span class="chat-menu" role="menu" hidden>
        <button type="button" role="menuitem" data-chat-clear="this">Clear this conversation</button>
        <button type="button" role="menuitem" data-chat-clear="all">Clear every conversation</button>
      </span>
    </span>
    <button type="button" class="chat-x" data-chat-close aria-label="Close the panel" title="Close (a)">&times;</button>
  </div>
  {context(ref, info[ref])}
  <div class="ask chat-body" data-ask="{esc(ref)}" data-chat="1">
    <div class="ask-thread">{chat_thread(ASKED.get(ref, []))}</div>
  </div>
  <div class="chat-box" data-parent="">
    <div class="chat-follow" hidden>Following on from the last answer</div>
    <div class="ask-field">
      {SPARK}
      <textarea rows="1" aria-label="Ask"
        placeholder="Ask, or tell it what to change&hellip;"></textarea>
      <button type="button" class="ask-send" data-ask-send hidden
              aria-label="Send" title="Send (Enter)">{SEND}</button>
    </div>
    <div class="ask-go">
      {model_picker()}
      <span class="ask-keys">Enter sends &middot; Shift+Enter for a new line &middot; Esc closes</span>
      <span class="ask-hint"></span>
    </div>
  </div>
</aside>"""
