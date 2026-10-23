#!/usr/bin/env python3
"""The board: every open ticket assigned to the owner, and every item of work on it.

This is the one durable file. Days come and go, the standup happens or does
not, but a ticket stays open until Asana says otherwise and an item keeps its
number until it is closed. That is the difference between a record of a meeting
and a to-do list you can actually work from.

Shape of `state/board.json`:

    {
      "version": 1,
      "checked_at": "2026-08-24T18:20:00+00:00",
      "next_id": 8,
              "sessions": [ ...see below... ],
              "script": {"for_date", "at", "built_at", "headline"},
              "news": [ ...see below... ],
              "prioritization": { ...see below... },
              "tickets": [
                {
                  "id": "<asana gid>",             Asana gid, so nothing is duplicated
                  "ref": "HOLD-RELEASE",           short tag the owner uses out loud
                  "title_en", "title_ja", "asana_url",
                  "asana": {"status", "section", "priority", "category",
                            "severity", "assignee", "project", "completed"},
                  "no_ticket_yet": "why Asana has never heard of this",
                  "last_activity": {"at", "who", "where"},
                  "where_it_stands", "threads",
                  "closes_when": [{"what", "who", "state", "note"}],
                  "internal_ticket": {"name", "url", "build": {...}},
                  "terms": [{"term", "say", "means", "source_url"}],
                  "events": [{"on": "2026-08-24", "at": "15:14", "who", "what",
                              "so_what", "where", "source_url"}],
                  "prep": { ...see below... },
                  "items": [ ...see below... ]
                }
              ]
            }

`sessions` is every room the owner still has to speak in, soonest first. A list,
because an onsite on Wednesday and a standup on Monday are two different
rooms, and `script.for_date` says which one the current script was written for.
A standup that is not running stays in the list with `skipped` set, since "no
standup Wednesday" changes what has to move into Asana instead.

    [{"kind": "standup|weekly|onsite|workshop", "date": "2026-08-26", "at": "09:30",
      "label", "title", "place", "focus", "skipped", "reason", "quote",
      "timetable": [{"at": "11:00", "what": "...", "mine": true}],
      "agenda": [{"topic", "why", "owner"}], "bring": ["..."]}]

`timetable` is for a session that is a day rather than a slot. An onsite has a
building to be at, a standup somewhere in the middle of it and an hour when the
real discussion starts, and none of those is the time on the invite. `mine` on a
row is the part that concerns the owner, and those are the rows the page emphasises.
Leave it out for an ordinary standup, where `at` is the whole story.

`xws` is the weekly programme-wide rollup (one of the meetings in `config.json`
`meeting.also`), and it is deliberately not one of the `sessions`. The standup
is a room the owner walks ticket by ticket; the rollup is the whole programme in
one room, where only the biggest items from the workstream are ever taken up
from them. So it does not drive the full speaking walk (that stays the
soonest real `session`), it renders as one folded card at the top of the speaking
view, shown or hidden with a button, holding a shortlist of the items big enough
to surface. Keeping it out of `sessions` is what lets both be prepared at once:
the standup as the full walk, the rollup as the exception list on top of it.

    {"for_date": "2026-09-11", "at": "11:00", "built_at": "ISO",
     "headline": "what they raise at the rollup, or that nothing is big enough",
     "raised": [{"ref": "INVOICE-REPRINT",
                 "why": "why this one is big enough for the whole workstream",
                 "say": [{"ja_ruby": "{word|reading}…", "en": "…"}]}]}

An empty `raised` is a fact worth walking in with, so the card still draws on its
`headline` alone: "nothing from the workstream needs the room this week" is a thing to
know, not a blank.

`huddle` is the weekly Kraken-internal team huddle for the client (one of the
meetings in `config.json` `meeting.also`), a short update in English, and like
`xws` it is not one of the `sessions`. It renders as its own folded card on the
speaking view and is only rewritten on the day the huddle runs.

    {"for_date": "2026-09-16", "at": "11:30", "built_at": "ISO",
     "plate": ["one terse line per thing on their plate now"],
     "blockers": ["only a genuine blocker; usually one line or none"]}

Both `plate` and `blockers` are plain strings, English, no reading markup. Keep it to
the short bullet shape the owner writes in their notes, and be selective: "No blockers" is
the common, honest answer.

`news` is what is moving around the owner that is not one of their tickets: a
priority that changed, an outage upstream, a decision on somebody else's ticket
that lands on theirs. The desk answers "what do I do" perfectly well without it, and
answers "what has changed around me" not at all, which is the thing you walk
into a room not knowing. Five rows at most, dropped as they go stale.

    [{"topic", "what", "why", "on": "2026-08-24", "source_url"}]

Questions asked from the page do not live here. They sit in `state/asks.json`,
keyed by what they were asked about, because an agent rewriting the board should
never be able to lose the reason a draft says what it says:

    {"asks": [{"id", "ref": "item:8" or "ticket:HOLD-RELEASE", "question",
               "asked_at": "2026-08-25 16:36", "answer",
               "state": "running|answered|failed"}]}

`prompts/ask.md` is what answers one, and an answer may change the item it was
asked about. Item state is not part of that: `tick.py` is the only thing that
moves an item, however plainly the question says the work is done.

`meeting_note` and `migration_note` record which standup notes were last folded
in, so a refresh does not re-read them. Same shape: `date`, `found`, `url`.
The main standup (`config.json` `meeting`) goes in `meeting_note`; the
programme-wide meetings in `meeting.also`, where they mention the workstream, go
in `migration_note` (the name is historical).

`other_notes` is the same record for a room that is neither: a one-off session
whose note was read once because something in it reached a ticket. Same shape
plus `title` and `gist`, so a later refresh knows it has already been read and
what came out of it.

    [{"date", "title", "url", "found", "gist"}]

`news` is drawn in the brief at the top of the work view: the newest row as the
Big news line, the rest folded under it. The next session and `alert` are lines
of the same brief, so the morning is one block rather than a brief and a second
fold repeating it.

`alert` is the exception, and it is empty almost every day: something that needs
the owner inside the hour and is not an item, because there is nothing to do until
somebody else moves. Two sentences that make sense to someone who has read
nothing else, and a link to the message.

    {"what": "...", "source_url": "..."}

`brief` is the morning briefing: the five things the owner would want a
secretary to tell them before the day starts, and nothing else.

    {"built_at": "2026-09-28T08:15:00+00:00", "for_date": "2026-09-28",
     "lines": [{"what": "...", "kind": "room|moved|do|watch|big",
                "items": ["43"], "ref": "HOLD-RELEASE",
                "source_url": "...", "at": "09:30"}]}

**Five lines, hard cap, and fewer is the normal answer.** Everything on the board
is already on a card; this is only the part that is true *this morning* and would
cost the owner something to miss. A sixth line means one of the five was not important
enough, not that today was busy.

`kind` is what sort of thing it is, and orders the list:

- `room` a session today and what the owner owes it. Always first if there is one.
- `moved` something that changed since the last brief: a reply landed, the client
  closed a ticket, a flag went on, a ticket was assigned to the owner.
- `do` what to start on, named by item number so they can act on it at once.
- `watch` something with a clock on it they cannot act on: a hold window, someone
  away, a release going out.
- `big` the exception to "their work only". Something happening across the client
  **or another Kraken client on the same platform** with enough people and enough
  consequence that not knowing it would be worse than any of the above: a platform
  decision, a migration-wide freeze, an incident that will reach the workstream.
  It does not need a route to one of their tickets, which is the one place on this board that rule is relaxed. Most days
  there is none, and inventing one to fill the slot is the failure mode.

`items` are item numbers the line is about, so the page can link them. `ref` is a
ticket tag when the line is about one. A line with neither is fine for `big`.

Written by the sweep as its last step, and rewritten every time, so it always
matches the board it was drawn from. The page says its age and refuses to draw a
brief written for a day that has passed, because a stale briefing read as a
current one is worse than no briefing.

`terms` carries the vocabulary of the ticket in both languages: `term` in
English, `say` as the client say it (with `{word|reading}` markup when the
profile asks for readings), `means` in a sentence or two that opens with what the
thing is. The owner hears the client-language term in the room and has two
seconds to place it, so the client-language form is not optional.

`glossary` is the short version of the same thing, board-wide, and the renderers
use it rather than the page. A flat map of the client-language term to two or
three words of English:

    {"<client term>": "open the day after close", "<another term>": "meter reading date"}

Every English line on the page gets the meaning put after the first client-language
term it contains, once per card and once again in the script, since the script is all
that is left in client-language-only mode. So write the English plainly and leave the
gloss out of it: writing it by hand is how a term ends up explained twice in one
sentence. A term that is not in here is a term the owner reads without knowing,
so add it when it first appears in something written for them.

`prep` is what the ticket sounds like out loud, and only the speaking view reads
it. It is rebuilt whenever the owner presses Write prep, and it deliberately holds no
status of its own: that view takes `where_it_stands` from the ticket, so a script
can never contradict the desk.

    {"order": 1, "board_position": "5 of 12 on the board",
     "issue": ["plain English, two or three lines"], "why_it_matters",
     "consequences": {"fix_covers", "falls_outside", "accumulates",
                      "who_owns_it", "done_means", "still_open"},
     "open_questions": [{"en", "ja_ruby", "who"}],
     "script": [{"heading": "<client-language heading>", "heading_en": "Where it stands",
                 "lines": [{"ja_ruby": "{word|reading}...", "en": "..."}]}],
     "decisions": [{"need", "why", "fallback"}],      onsite only
     "pushback": [{"they_say", "say_ja", "say_en"}],   onsite only
     "client_ask_needed": true, "estimate": "", "unknowns": [], "built_at"}

`decisions` and `pushback` are what an onsite needs and a 15 minute standup does
not: what has to be settled before everyone leaves the room, and the sentence
ready for the objection that stops it being settled.

An item with `at_standup` true is work that has to be spoken about rather than
only done, so it shows on both views: on the desk with a "raise at" pill naming
the session, in the script under the ticket it belongs to.

An item is a piece of work with a life:

            {"id": 3, "title", "prepared", "steps": [], "done_when", "why",
             "state": "todo|waiting|hold|done|sent|dropped",
             "state_at", "state_note", "waits_on", "hold", "draft",
             "where", "link", "urgency", "est_minutes", "opened", "closed_at",
             "after": 17,
             "at_standup", "at_standup_note",
             "history": [{"at", "state", "note"}]}

`after` is the item number this one queues behind, and it is the difference
between a list of five things and one thing to do. Most work on a ticket is a
sequence: the scope cannot be confirmed before the sheet exists, and the sheet
cannot go out before the questions on it are settled. Listing all three as jobs
invents two decisions the owner does not have, and the page stops telling them
where to start. So only the front of a queue is a job. The rest stay on the ticket, folded,
saying which number they follow, and each becomes a job by itself the moment the
one in front of it closes.

Use it only for a real dependency, where doing this one first would be wrong or
impossible. Two jobs that merely happen to be on the same ticket are two jobs.
Two jobs that end in the same message are not two jobs at all: fold them into one
item rather than chaining them, because they send one message. An `after` pointing
at a closed item is spent and ignored, so nothing has to clean it up.

`steps` is what the owner actually does, in order, verb first, and it is the one
field an item cannot be useful without. `done_when` is the finished thing in a
line, so they can tell whether they are there. `why` is last on the card and
shortest: nobody needs persuading about their own list, they need to know what
to type.

`where_it_stands` is the status, one to three plain sentences drawn above the
steps under its own label: what is known now, what has not happened yet, who is
holding what. It exists so status stops leaking into `steps`. A step is only
something the owner does now, each one verb, place and what to do with the result; a
list of accounts or settings goes in a `prepared` table and the step says "check
the table in prepared". `progress_note` stays the dated log underneath.

`steps_done` is which of those steps the owner has ticked off on the page, as
`{step text: when}`. It is keyed by the words rather than the number, so a sweep
that trims or reorders the list keeps every mark on the step it was made on, and
a step rewritten in new words starts unticked. Only `./tick.py N --step K` writes
it; a sweep trims the ticked steps at its next pass, and `save()` forgets the
tick of any step no longer in `steps`.

`draft` is the whole of the doing when the step is "send this", so a step that
tells the owner to ask, tell, confirm or reply and carries no draft is a bug.

    {"language": "ja",                     "ja" or "en", and it picks where the body is read from
     "target": "who and where it goes",    the head of the box, e.g. "Asana comment on HOLD-RELEASE, reply to Sato-san"
     "link": "https://...",                optional, the Go there button
     "body_ruby": "{word|reading}…",       a ja draft's text, with readings on whole words when the profile asks
     "body_en": "…",                       an en draft's text; or the translation shown under a ja draft
     "reply_from": "Sato-san",             optional, who answers it, when that is not the addressee
     "expects_reply": false,               optional, only when nothing comes back from it
     "step": 1,                            optional, the step that posts it (1 is the first)
     "sent": false}                        set true by the Sent button (or tick.py --draft)

Posting a draft ticks the step that sends it, so "Post the reply below" is struck
through the moment Posted it is pressed. `step` names that step. Without it the
desk takes the open step that starts with a sending verb and shares the most words
with the draft's `target`, which is right for every step written the usual way;
set `step` when a job has two such steps and the words do not tell them apart.

A job that sends more than one message (a reply to the client and one to the engineer
who answered) puts the first in `draft` and the rest in `drafts`, a list of the
same shape. Each shows as its own box above `prepared`, never as a note inside it.

Every unsent draft carries a Sent bar on the page. Pressing it marks that draft
`sent`, and once the last draft on the job has gone it moves the job: waiting on
everyone a draft expects a reply from, or closed as `sent` when none does. So
`reply_from` and `expects_reply` are how a writer says what happens after the
message, and a step never says it. **Never write a `./tick.py` command, or any
other instruction for moving the job, into `steps`**: the owner would need a terminal
for a thing the card does in one press, and audit.py flags it.

The body has to be under the key the renderer reads, or the card shows an empty
box that reads as ready to paste: a ja draft in `body_ruby` (readings on whole
words when the profile asks for them), an en draft in `body_en`. `body_ja` and a
bare `body` still render as a fallback but carry no readings, so they are wrong
for anything going to the client when the profile asks for readings. board.check() refuses a draft with no readable body at all.

`prepared` is the part an agent could do and therefore did. If the steps could
be followed by something without judgement, following them was the agent's job,
and what is left for the owner is checking it, deciding with it or saying it out loud.

    {"what": "one line naming the thing that now exists",
     "conclusion": "one or two plain sentences: what is true, then what it means",
     "built_at": "2026-08-25 11:20",
     "table": {"columns": [], "rows": [[...]]},   optional
     "findings": ["one fact each, so-what first, four at most"],
     "notes": [{"heading": "what kind of information this is", "body": "..."}],
     "unanswered": ["what the work could not settle"],
     "files": [{"label": "Specification, client language, PDF",
                "path": "docs/spec-example.pdf"},
               {"label": "Process diagram, Miro",
                "url": "https://miro.com/app/board/..."}],
     "meeting_use": {"label": "Open onsite version",
                     "summary": "what was moved into the script"},
     "sources": [{"label", "url"}]}

All of it is written to be read once. One idea to a sentence, the plain word
over the dense one, numbers and file:line rather than adjectives, and the bottom
line first so the owner can act on it without reading further. prompts/post.md, "Write
it so they read it once", carries the standard, and it holds for every card the
app writes. It changes how the work reads, never what is in it: `unanswered`
stays whole and every source stays cited.

The table contains only like-for-like evidence. A stakeholder's desired
outcome, an implementation constraint and a query condition are different kinds
of information, so label them in `notes` rather than dropping them into one
comparison row. A cell written as `{"text": "The one gap", "tone":
"good|gap|warn"}` prints as a verdict instead of prose, so the row that is the
point of the table is findable without reading every cell. One verdict column,
and the reasoning stays in `findings`.

`unanswered` is what the work could not settle, and it is not optional
politeness. Prepared work that hides its own gaps reads as a finished answer,
and the owner carries it into a room on that basis. Name the thing, and name why it is
open: nobody has been asked, or the answer given was not usable.

`files` is for work that leaves the desk as a document rather than as a message:
a handover sheet, a procedure the client keep open while they work. The document lives
in `docs/` as HTML and goes out as the PDF beside it, built by `make_doc.py`.
The page serves `docs/` at `/doc/`, so the button opens the real file rather
than a copy pasted into the board. Never reading markup in one of these: `draft`
text is for the owner reading aloud, a document is for the reader who receives it.

An entry may carry a `url` rather than a `path` when the thing the work produced
cannot live in the repo, a Miro board being the case that forced it. Use it only
for something the work made. Something it merely read is a `sources` row, and
somewhere a conversation is happening is a `threads` row on the ticket.

`meeting_use` links the work product to the same ticket on the What I say view;
the meeting-ready words live in `prep`, not twice on the item.

`closes_when` is every gate between here and a closed ticket, in the order it
has to fall, including the ones that are nobody's item: an engineer being
assigned, a release landing, the client checking the bills that came out after it.
Without it a ticket with no open items reads as finished when it is only quiet.
`state` is `done`, `now` (the owner's), `blocked` or `next` (somebody else's), and `who`
names them. Point `note` at the item number when an item is already tracking the
gate, rather than restating it.

    [{"what": "An engineer assigned to the build", "who": "Kraken CE",
      "state": "next", "note": "The one thing this ticket is waiting on."}]

`no_ticket_yet` is for work the owner is carrying that Asana has never heard of: agreed
in a Slack thread, real enough to prepare for, and with no gid to hang it on.
The ticket takes a slug for its `id`, leaves `asana` and `asana_url` empty, and
this field says why there is no ticket and what would raise one. The card says
"work in hand" rather than "Asana ticket", so nobody goes looking for a link
that does not exist. Fill it in the moment a ticket is raised and delete it.

`internal_ticket` links the Kraken-side CE build when the client is waiting on code.
`name` and `url` open it. `build` is what the desk prints under Where it stands,
because "queued behind an engineer" is half of where a client ticket is:

    {"kt": "KT-00000",
     "stage": "refining|queued|building|review|released|verified",
     "asana_status": "Asana's own words for it",
     "engineer": "",                    empty means nobody has picked it up
     "size": "2 SP", "refined_by": "Alex Kim", "refinement": "🟢",
     "feature_flag": "Yes|No",          Yes means release and switch-on differ
     "moved_on": "2026-08-24",          Asana modified_at, date only
     "waiting_on": "the one sentence saying why it is not moving",
     "safe_to_say": "the line to the client that carries none of the above"}

`stage` runs one stop past the release because a client ticket closes when the
client have checked the result, not when the code lands. All of it is desk only,
and `safe_to_say` is the only part that may be repeated to the client. Never in `prep`.
`none_yet` replaces `build` when no build has been raised, and says why, since
an empty queue is a fact about the ticket too.

An item with a `prepared` block has steps that start after it: read this, take
it into the room, send it. Never steps that redo it.

Only `tick.py` and the agents write here. Renderers read it and never change it.

**Every write goes through `save()`, and `save()` is safe to call from anywhere
at once.** A refresh, two asks and a `./tick.py` in a terminal can all hold the
board in memory at the same time. Each `load()` remembers the file it read, and
`save()` takes `state/board.lock`, reads the file again, and if somebody else
wrote it in between, merges: their change and this one both land, field by
field, and only a field both of them changed is decided by this writer. Items
are matched by number and tickets by gid, so a sweep adding an event never
erases the draft an ask rewrote a minute earlier. Never edit `board.json` with
a text editor or a string replace: that skips the lock, the merge and the check.

`save()` also keeps what a writer throws away. A `prepared` finding or note that
a rewrite drops moves into `prepared.earlier` with the time it went, so a sweep
condensing twelve findings into four costs nothing the owner can not get back,
and an item may never vanish from a ticket that is still on the board. Close it
as `dropped` instead, because the number is how they refer to it.

    "earlier": [{"at": "ISO", "text": "a finding or 'Heading: body'"}]

`milestones` is work the owner leads that is bigger than one ticket: an Asana
milestone, a Jira epic, a row on a Notion page with their name as Lead. It is
assigned to them there, which is what earns it a place, and it holds no work
of its own. The work is the tickets and items it points at, so the epic and its
children can never disagree about what is open.

    [{"id": "price-update",                 a slug, so apply.py can address it
      "name": "Spring Release", "name_ja": "<client-language name>",
      "target": "2026-11-30",              ISO, the date it has to be done by
      "target_note": "desired due date",
      "status": "on-track|at-risk|off-track|done",
      "outcome", "lead", "support", "notes", "source_url", "asana_url",
      "phases": [{"what", "when", "state": "done|now|next|blocked",
                  "who", "due": "YYYY-MM-DD", "note", "items": [52]}],
      "tickets": [{"ref", "title", "asana_url", "assignee", "state"}],
      "items": [52, 54]}]

`prioritization` is every CE build whose CDL-Owner is the owner, as Asana had it
when a sweep last read them, for the recurring prioritization round run by
`config.json` `prioritization.who`. It exists only when `config.json` has a
`prioritization` block. The round dates are not here: `prioritize.py` works them
out from `config.json`. CDL-Owner is the whole membership rule, so a build owned
by another CDL never appears, however close it sits to one of their tickets.

    {"checked_at": "ISO",
     "last_round": {"date": "2026-10-13", "url": "the Slack thread",
                    "ordered": ["KT-00001", "KT-00002"],
                    "not_pushed": ["KT-00003"]},   the owner's builds only
     "builds": [{"kt": "KT-00004", "gid", "url", "name",
                 "ticket": "HOLD-RELEASE",         board ref, or "" when none
                 "status": "Status (Kraken Cust)", "refinement": "🟢",
                 "size": "3 SP", "client_order": "", "wave": "", "squad": "",
                 "due_on": "YYYY-MM-DD or empty", "engineer": "",
                 "why": "one line for the prioritization lead on why it should be ordered"}]}

A build with no client order (`client_order`) that is refined
is one to raise; `why` is the reason in the prioritization lead's terms, written
for a Kraken reader. Like `internal_ticket.build`, none of it ever reaches the
client.

`phases` is the path to done, in order, the way `closes_when` is for a ticket.
Each phase has one owner in `who`, never "the client, then Kraken": a handover that has
happened is a done phase of its own, and the work after it is the next one. A
phase being worked (`now` or `blocked`) names the items that move it and, when
there is a date it has to land by, `due`; the card leads with those phases in a
Moving now box, with each job's live state and next step.
`tickets` lists every ticket under it, the owner's or not: a `ref` that is on the
board links to that card, anything else is a child they follow and do not own, and
it stays a row here rather than becoming a card. `items` are item numbers on
any ticket that move the milestone; the page counts them for its progress bar.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import settings

ROOT = settings.REPO
BOARD = settings.STATE_DIR / "board.json"
LOCKFILE = settings.STATE_DIR / "board.lock"

# What each loaded board looked like on disk, so save() can tell what this writer
# changed from what somebody else wrote meanwhile. Keyed by the object, bounded
# because the server loads the board on every timeline request and never saves.
_BASES: dict[int, tuple[dict, str]] = {}
_BASES_KEPT = 8
EARLIER_KEPT = 40

OPEN_STATES = ("todo", "hold", "waiting")
CLOSED_STATES = ("done", "sent", "dropped")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _remember(board: dict, text: str) -> None:
    # The board object itself is held beside its text, so its id() cannot be
    # handed to a new dict while the entry is here and match the wrong base.
    _BASES[id(board)] = (board, text)
    while len(_BASES) > _BASES_KEPT:
        _BASES.pop(next(iter(_BASES)))


def load(path: Path | None = None) -> dict:
    path = path or BOARD
    if not path.exists():
        return {"version": 1, "checked_at": "", "next_id": 1, "tickets": []}
    text = path.read_text(encoding="utf-8")
    board = json.loads(text)
    if path == BOARD:
        _remember(board, text)
    return board


@contextmanager
def locked():
    """Hold `state/board.lock` for one read-merge-write.

    An flock rather than a file that exists, so a writer that dies lets go of it
    with its process and nothing ever has to decide a lock is stale. Held for the
    write only, which is milliseconds, never for a whole sweep: that is what lets
    an ask and a refresh run side by side.
    """
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCKFILE.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def save(board: dict, path: Path | None = None) -> None:
    # The board is written by agents, so this is the only place a bad shape can be
    # stopped. Two items sharing a number makes "do 20" ambiguous; findings held as
    # dicts takes the whole page down with a TypeError halfway through a card.
    # Refusing to write is the kind failure: the board on disk stays readable, and
    # whoever is holding it gets told what to fix.
    problems = check(board)
    if problems:
        raise ValueError(
            "the board was not written, because it would not render:\n  "
            + "\n  ".join(problems)
            + "\nFix the item in memory and save again. For a new number use "
            "board.next_id(board) rather than setting id by hand."
        )

    path = path or BOARD
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked():
        disk_text = path.read_text(encoding="utf-8") if path.exists() else ""
        disk = json.loads(disk_text) if disk_text else None
        entry = _BASES.get(id(board))
        base_text = entry[1] if entry and entry[0] is board else None
        merged = board
        # Somebody wrote the board after this writer read it. Their change and
        # this one both land; see the module docstring.
        if disk is not None and base_text is not None and base_text != disk_text:
            merged = merge(json.loads(base_text), board, disk)
            _renumber_collisions(merged, disk)
        if disk is not None:
            vanished(disk, merged)
            keep_earlier(disk, merged)
        prune_ticked(merged)
        problems = check(merged)
        if problems:
            raise ValueError(
                "the board was not written, because merging it with what another "
                "writer saved meanwhile would not render:\n  " + "\n  ".join(problems)
            )
        text = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
        # Write to a sibling temp file and rename it into place. The rename is
        # atomic on the same filesystem, so a crash or a full disk mid-write
        # leaves the old board intact rather than truncating it to nothing.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    # The caller goes on holding what is now on disk, so a second save from the
    # same writer merges against this one rather than undoing the other writer.
    if merged is not board:
        board.clear()
        board.update(merged)
    if path == BOARD:
        _remember(board, text)


# ---------------------------------------------------------------- the merge
#
# Three versions of one board: what a writer loaded (base), what it now holds
# (mine) and what is on disk because somebody else saved first (theirs). A field
# only one side changed takes that side's value; a field both changed takes mine,
# because this writer is the one saving now. Tickets and items are matched by id,
# so two writers on two different jobs never touch each other at all.

_MISSING = object()
KEYED = {"tickets", "items", "milestones"}  # lists of records with an "id"
LOGS = {"events", "history", "earlier"}  # append-mostly: keep both sides' additions


def merge(base, mine, theirs, field: str = ""):
    if mine == base:
        return theirs
    if theirs == base or mine == theirs:
        return mine
    if isinstance(mine, dict) and isinstance(theirs, dict):
        base = base if isinstance(base, dict) else {}
        out = {}
        for key in list(mine) + [k for k in theirs if k not in mine]:
            value = merge(base.get(key, _MISSING), mine.get(key, _MISSING),
                          theirs.get(key, _MISSING), key)
            if value is not _MISSING:
                out[key] = value
        return out
    if isinstance(mine, list) and isinstance(theirs, list):
        base = base if isinstance(base, list) else []
        if field in KEYED:
            return _merge_keyed(base, mine, theirs, field)
        if field in LOGS:
            gone = [x for x in base if x not in theirs]
            kept = [x for x in mine if x not in gone]
            return kept + [x for x in theirs if x not in base and x not in kept]
    return mine


def _merge_keyed(base: list, mine: list, theirs: list, field: str) -> list:
    def by_id(rows):
        return {str(r.get("id")): r for r in rows if isinstance(r, dict)}

    b, m, t = by_id(base), by_id(mine), by_id(theirs)
    out = []
    for row in mine:
        key = str(row.get("id")) if isinstance(row, dict) else None
        if key is None:
            out.append(row)
        elif key in t and key not in b and row != t[key]:
            # Both writers added a record under the same id. They are two
            # different things, so both stay, and the renumbering below gives
            # this writer's the next free number.
            out += [t[key], row]
        elif key in t:
            out.append(merge(b.get(key, _MISSING), row, t[key], ""))
        elif key not in b or row != b[key]:
            out.append(row)   # new here, or they removed what this writer changed
    for key, row in t.items():
        if key in m:
            continue
        if key not in b or row != b[key]:
            out.append(row)   # new there, or this writer removed what they changed
    return out


def _renumber_collisions(board: dict, theirs: dict) -> None:
    """Two writers that each took board.next_id() got the same number.

    Theirs is already on disk and may already be on the page, so it keeps the
    number and this writer's item takes the next free one.
    """
    on_disk = {str(i.get("id")): i for _, i in items(theirs)}
    rows = [i for _, i in items(board)]
    count: dict[str, int] = {}
    for item in rows:
        count[str(item.get("id"))] = count.get(str(item.get("id")), 0) + 1
    top = max((int(i.get("id")) for i in rows if str(i.get("id", "")).isdigit()), default=0)
    top = max(top, int(theirs.get("next_id", 1)) - 1, int(board.get("next_id", 1)) - 1)
    for key in [k for k, n in count.items() if n > 1]:
        twins = [i for i in rows if str(i.get("id")) == key]
        keeper = next((i for i in twins if i == on_disk.get(key)), twins[0])
        for item in twins:
            if item is not keeper:
                top += 1
                item["id"] = top
    board["next_id"] = top + 1


def vanished(before: dict, after: dict) -> None:
    """Refuse a save that loses an item from a ticket still on the board."""
    tickets = {str(t.get("id")) for t in after.get("tickets", [])}
    now_ids = {str(i.get("id")) for _, i in items(after)}
    lost = [
        f"item {i.get('id')} on {t.get('ref')}"
        for t, i in items(before)
        if str(i.get("id")) not in now_ids and str(t.get("id")) in tickets
    ]
    if lost:
        raise ValueError(
            "the board was not written, because it would lose "
            + ", ".join(lost)
            + ". An item keeps its number until it closes: close it with "
            "./tick.py <n> --dropped -n 'why' rather than deleting it."
        )


def _note_text(note) -> str:
    if isinstance(note, dict):
        return f"{note.get('heading', '')}: {note.get('body', '')}".strip(": ")
    return str(note)


def keep_earlier(before: dict, after: dict) -> None:
    """Move whatever a rewrite dropped from `prepared` into `prepared.earlier`.

    A sweep is told to keep a prepared block short, and it should be: four
    findings the owner can act on beat sixteen they cannot. But the detail it condensed
    away was real work, a line reference or a comment id they may need in the
    room, and a model deciding what to forget is how good work went missing. So
    nothing is forgotten. The page shows it under one fold, newest first, and
    `earlier` itself can only grow until it reaches EARLIER_KEPT.
    """
    old = {str(i.get("id")): i for _, i in items(before)}
    stamp = now()
    for _, item in items(after):
        was = (old.get(str(item.get("id"))) or {}).get("prepared")
        if not isinstance(was, dict):
            continue
        new = item.get("prepared") if isinstance(item.get("prepared"), dict) else {}
        earlier = [e for e in new.get("earlier") or [] if isinstance(e, dict)]
        known = {e.get("text") for e in earlier}
        # What was already kept stays kept, whoever rewrote the block.
        carried = [e for e in was.get("earlier") or []
                   if isinstance(e, dict) and e.get("text") not in known]
        known |= {e.get("text") for e in carried}
        dropped = []
        for field in ("findings", "notes"):
            now_there = new.get(field) or []
            for entry in was.get(field) or []:
                text = _note_text(entry)
                if entry not in now_there and text and text not in known:
                    dropped.append({"at": stamp, "text": text})
                    known.add(text)
        if not dropped and not carried:
            continue
        new = dict(new)
        new["earlier"] = (dropped + earlier + carried)[:EARLIER_KEPT]
        item["prepared"] = new


def items(board: dict) -> list[tuple[dict, dict]]:
    """Every item with the ticket it belongs to."""
    return [(t, i) for t in board.get("tickets", []) for i in t.get("items", [])]


# What each field has to be for the renderers to survive it. The board is written
# by agents rather than by this code, so no amount of typing in Python protects
# it: the check has to happen at the boundary. `prepared.findings` held
# dictionaries once, where every reader expected strings, and the page died with a
# TypeError halfway down a card.
SHAPES: dict[str, type | tuple[type, ...]] = {
    "title": str,
    "why": str,
    "state": str,
    "urgency": str,
    "steps": list,
    "steps_done": dict,
    "done_when": str,
    "progress_note": str,
    "where_it_stands": str,
    "state_note": str,
    "waits_on": dict,
    "hold": dict,
    "draft": dict,
    "drafts": list,
    "prepared": dict,
    "history": list,
    "est_minutes": (int, float),
    "after": (int, str),
}
LISTS_OF_TEXT = ("steps",)
PREP_LISTS = ("issue", "script", "open_questions", "decisions", "pushback", "unknowns")
PREPARED_TEXT = ("findings", "unanswered", "sources", "files")

# The only three urgencies. The week strip reads them to place a job on a day,
# so a fourth spelling is a job that loses its column rather than a tidiness
# problem. `prompts/refresh.md` says the same thing in the same words.
URGENCIES = frozenset({"today", "this-week", "monitor"})

# A chase date, and nothing else. A sentence in this field is a job with no day.
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

MILESTONE_STATUS = ("on-track", "at-risk", "off-track", "done")
PHASE_STATES = ("done", "now", "next", "blocked")


def check(board: dict) -> list[str]:
    """Everything wrong with the shape of this board, in the words of a fix.

    Not the same job as `audit.py`, which asks whether the board is out of date.
    This asks whether it is the shape the renderers expect, which is the class of
    fault that takes the whole page down rather than making one card wrong.
    """
    bad: list[str] = []
    ids: dict[str, str] = {}

    for ticket, item in items(board):
        where = f"item {item.get('id')} on {ticket.get('ref', '?')}"

        ident = str(item.get("id", ""))
        if not ident:
            bad.append(f"{where}: no id, so nothing can refer to it")
        elif ident in ids:
            bad.append(f"item {ident}: used twice, on {ids[ident]} and {ticket.get('ref')}")
        else:
            ids[ident] = str(ticket.get("ref"))

        for field, want in SHAPES.items():
            value = item.get(field)
            if value is not None and not isinstance(value, want):
                names = getattr(want, "__name__", None) or " or ".join(
                    t.__name__ for t in want
                )
                bad.append(
                    f"{where}: {field} is a {type(value).__name__}, should be {names}"
                )

        for field in LISTS_OF_TEXT:
            for n, entry in enumerate(item.get(field) or [], 1):
                if not isinstance(entry, str):
                    bad.append(
                        f"{where}: {field}[{n}] is a {type(entry).__name__}, "
                        f"should be a plain sentence"
                    )

        ticked = item.get("steps_done")
        if isinstance(ticked, dict):
            for text, when in ticked.items():
                if not isinstance(when, str):
                    bad.append(
                        f"{where}: steps_done[{str(text)[:40]!r}] is a "
                        f"{type(when).__name__}, should be the time it was ticked"
                    )

        # `urgency` is a closed set of three words, because the week strip reads
        # them to place a job on a day. A fourth spelling is not a style slip:
        # `this week` with a space sorted as unknown and the job lost its column.
        # Refused here, at the point of writing, rather than found on the page.
        urgency = item.get("urgency")
        if urgency is not None and str(urgency).strip().lower() not in URGENCIES:
            bad.append(
                f"{where}: urgency is {urgency!r}. It has to be one of "
                f"{', '.join(sorted(URGENCIES))} (hyphen, not a space), because "
                f"the week strip reads it to place this job on a day."
            )

        # A chase date has to be a date. A sentence here ("Only if they raise it
        # again") is a job with no day on it, so it never appears on the week the
        # owner plans against. The sentence belongs in waits_on.what.
        waits = item.get("waits_on")
        if isinstance(waits, dict):
            chase = waits.get("chase_on")
            if chase and not ISO_DAY.fullmatch(str(chase).strip()):
                bad.append(
                    f"{where}: waits_on.chase_on is {chase!r}, which is not a "
                    f"date. Use YYYY-MM-DD so the week strip can place it, and "
                    f"put any condition in waits_on.what."
                )

        prepared = item.get("prepared")
        if isinstance(prepared, dict):
            for n, entry in enumerate(prepared.get("earlier") or [], 1):
                if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                    bad.append(f"{where}: prepared.earlier[{n}] should be {{at, text}}")
            for field in PREPARED_TEXT:
                for n, entry in enumerate(prepared.get(field) or [], 1):
                    if not isinstance(entry, (str, dict)):
                        bad.append(
                            f"{where}: prepared.{field}[{n}] is a "
                            f"{type(entry).__name__}"
                        )
                    elif isinstance(entry, dict) and field == "findings":
                        bad.append(
                            f"{where}: prepared.findings[{n}] is a dict. Findings "
                            f"are strings; every reader joins them as prose."
                        )

        # A draft dict need not carry a body: once a message has gone, the draft is a
        # folded record of what went (target, link, posted/sent) and the summary is the
        # point. What must never happen is body text saved under a key no renderer reads,
        # because then the card shows an empty box where the message should be and reads
        # as ready to paste. render_draft reads body_ruby/body_ja/body (ja) and
        # body_en/body_ruby/body (en), so any other body* key is text that will never
        # show. That is the fault that shipped three blank drafts once: a ja body saved
        # under a bare key the ja branch did not read.
        extra = item.get("drafts") if isinstance(item.get("drafts"), list) else []
        for draft in [item.get("draft")] + extra:
            if not isinstance(draft, dict):
                continue
            readable = {"body_ruby", "body_ja", "body", "body_en"}
            if "step" in draft and (not isinstance(draft["step"], int) or isinstance(draft["step"], bool)):
                bad.append(f"{where}: draft step is {draft['step']!r}, should be the step's number (1 is the first)")
            for k, v in draft.items():
                if (
                    k.startswith("body")
                    and k not in readable
                    and str(v or "").strip()
                ):
                    is_ja = draft.get("language") == "ja"
                    home = "body_ruby" if is_ja else "body_en"
                    bad.append(
                        f"{where}: draft body is under {k!r}, which no renderer reads, "
                        f"so the card shows an empty box. Put the text in {home}"
                        + (" (a ja draft carries any readings as {word|reading})." if is_ja else ".")
                    )

        state = item.get("state", "todo")
        if state not in OPEN_STATES + CLOSED_STATES:
            bad.append(
                f"{where}: state is {state!r}, which no renderer knows. "
                f"One of {', '.join(OPEN_STATES + CLOSED_STATES)}."
            )

    # A prep list written as null rather than left out takes both pages down:
    # the renderers read a missing list as empty but iterate whatever is there.
    for ticket in board.get("tickets", []):
        prep = ticket.get("prep")
        if not isinstance(prep, dict):
            continue
        for field in PREP_LISTS:
            if field in prep and not isinstance(prep[field], list):
                bad.append(
                    f"prep on {ticket.get('ref', '?')}: {field} is "
                    f"{type(prep[field]).__name__}, should be a list. Leave it "
                    f"out when there is nothing to say."
                )
        for b_idx, block in enumerate(prep.get("script") or [], 1):
            if not isinstance(block, dict):
                continue
            for l_idx, line in enumerate(block.get("lines") or [], 1):
                if not isinstance(line, dict):
                    continue
                if ("say_ja" in line or "say_en" in line) and not (line.get("ja_ruby") or line.get("en")):
                    bad.append(
                        f"prep on {ticket.get('ref', '?')}: script[{b_idx}].lines[{l_idx}] "
                        f"uses say_ja/say_en, which no renderer reads on script lines. "
                        f"Use ja_ruby and en (say_ja/say_en is for prep.pushback)."
                    )

    milestones = board.get("milestones")
    if milestones is not None and not isinstance(milestones, list):
        bad.append("milestones should be a list, or left out")
        milestones = []
    seen: set[str] = set()
    for n, ms in enumerate(milestones or [], 1):
        if not isinstance(ms, dict) or not ms.get("id") or not ms.get("name"):
            bad.append(f"milestones[{n}] needs an id and a name")
            continue
        where = f"milestone {ms['id']}"
        if ms["id"] in seen:
            bad.append(f"{where}: id used twice")
        seen.add(ms["id"])
        if ms.get("target") and not ISO_DAY.fullmatch(str(ms["target"])):
            bad.append(f"{where}: target {ms['target']!r} is not YYYY-MM-DD; "
                       "put any wording in target_note")
        if ms.get("status") not in (None, *MILESTONE_STATUS):
            bad.append(f"{where}: status {ms['status']!r}, one of {', '.join(MILESTONE_STATUS)}")
        for field in ("phases", "tickets", "items"):
            if ms.get(field) is not None and not isinstance(ms[field], list):
                bad.append(f"{where}: {field} should be a list")
        for p, phase in enumerate(ms.get("phases") or [], 1):
            if not isinstance(phase, dict) or not phase.get("what"):
                bad.append(f"{where}: phases[{p}] needs a 'what'")
            elif phase.get("state", "next") not in PHASE_STATES:
                bad.append(f"{where}: phases[{p}] state {phase['state']!r}, "
                           f"one of {', '.join(PHASE_STATES)}")
            elif phase.get("due") and not ISO_DAY.fullmatch(str(phase["due"])):
                bad.append(f"{where}: phases[{p}] due {phase['due']!r} is not YYYY-MM-DD; "
                           "put any wording in when")
            elif not all(str(i) in ids for i in phase.get("items") or []):
                bad.append(f"{where}: phases[{p}] names an item that is not on the board")
        for p, row in enumerate(ms.get("tickets") or [], 1):
            if not isinstance(row, dict) or not (row.get("ref") or row.get("title")):
                bad.append(f"{where}: tickets[{p}] needs a ref or a title")
        for ident in ms.get("items") or []:
            if str(ident) not in ids:
                bad.append(f"{where}: item {ident} is not on the board")

    prio = board.get("prioritization")
    if prio is not None and not isinstance(prio, dict):
        bad.append("prioritization should be an object, or left out")
        prio = {}
    builds = (prio or {}).get("builds")
    if builds is not None and not isinstance(builds, list):
        bad.append("prioritization.builds should be a list")
        builds = []
    for n, row in enumerate(builds or [], 1):
        if not isinstance(row, dict) or not row.get("kt"):
            bad.append(f"prioritization.builds[{n}] needs a kt")
            continue
        if row.get("due_on") and not ISO_DAY.fullmatch(str(row["due_on"])):
            bad.append(f"prioritization build {row['kt']}: due_on {row['due_on']!r} "
                       "is not YYYY-MM-DD; leave it empty when there is none")
        for field in ("client_order", "status", "size", "wave", "why", "ticket"):
            if isinstance(row.get(field), (dict, list)):
                bad.append(f"prioritization build {row['kt']}: {field} should be text")
    for field in ("ordered", "not_pushed"):
        got = ((prio or {}).get("last_round") or {}).get(field)
        if got is not None and not isinstance(got, list):
            bad.append(f"prioritization.last_round.{field} should be a list of KT numbers")

    highest = max((int(n) for n in ids if n.isdigit()), default=0)
    if board.get("next_id", 0) <= highest:
        bad.append(
            f"next_id is {board.get('next_id')} but item {highest} exists, so the "
            f"next writer collides. Use board.next_id(b)."
        )
    return bad


def by_id(board: dict, item_id: str | int) -> tuple[dict, dict] | tuple[None, None]:
    for ticket, item in items(board):
        if str(item.get("id")) == str(item_id):
            return ticket, item
    return None, None


def drafts(item: dict) -> list[dict]:
    """Every message this job sends, `draft` first, as the dicts on the item.

    The same records, not copies, so a caller can mark one sent in place.
    """
    first = [item["draft"]] if isinstance(item.get("draft"), dict) and item["draft"] else []
    rest = item.get("drafts") if isinstance(item.get("drafts"), list) else []
    return first + [d for d in rest if isinstance(d, dict) and d]


def unsent(draft: dict) -> bool:
    return not (draft.get("sent") or draft.get("posted"))


def prune_ticked(board: dict) -> None:
    """Forget ticks on steps that are no longer there.

    A sweep trims a ticked step, or rewrites it in new words, and the tick goes
    with it. Done here rather than by every writer, because a step's words are
    the key and a patch path cannot name a key with a dot in it.
    """
    for _, item in items(board):
        ticked = item.get("steps_done")
        if not isinstance(ticked, dict):
            continue
        steps = set(s for s in item.get("steps") or [] if isinstance(s, str))
        kept = {s: when for s, when in ticked.items() if s in steps}
        if kept:
            item["steps_done"] = kept
        else:
            item.pop("steps_done", None)


def step_done(item: dict, step: str) -> str:
    """When the owner ticked this step off, or "" while it is still to do."""
    ticked = item.get("steps_done")
    return str(ticked.get(step) or "") if isinstance(ticked, dict) else ""


def open_steps(item: dict) -> list[str]:
    """The steps still to do, in order, for readers that only want what is next."""
    return [s for s in item.get("steps") or [] if isinstance(s, str) and not step_done(item, s)]


# How a step that sends something begins: "Post the reply below", "Send it to
# Alex", "On Mon 26 Oct, nudge the client on the ticket". Only these can be the
# step a draft posts.
SENDS = re.compile(
    r"^\s*(?:(?:on|by|after|before) [^,.]{1,30},\s*)?(post|send|reply|comment|nudge|message|dm|ask|tell|share|answer|chase|"
    r"follow up|raise|put)\b", re.I,
)
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9']{2,}|[^\x00-\x7f]{2,}")
DULL = {"the", "and", "for", "with", "this", "that", "below", "draft", "drafted",
        "reply", "post", "comment", "send", "thread", "ticket", "message", "from",
        "san", "new", "your", "asana", "slack", "ask", "channel", "workflow"}
POINTS = re.compile(r"\b(below|draft|drafted)\b", re.I)
NAMES = re.compile(r"\bdraft (\d+)\b", re.I)


def step_for_draft(item: dict, draft: dict) -> int:
    """Which step posting `draft` finishes, 1-based, or 0 when none is it.

    The draft's own `step` wins. Otherwise the candidates are the open steps that
    send something and share two words with the draft's target, or one and, while
    this is the only draft left to send, point at "the draft below"; the one
    sharing the most words is it, the first on a tie.
    A step that names some other message ticks nothing: leaving a step open is
    a press away from right, and striking the wrong one through is not.
    """
    steps = [s for s in item.get("steps") or [] if isinstance(s, str)]
    named = draft.get("step")
    if isinstance(named, int) and not isinstance(named, bool):
        return named if 1 <= named <= len(steps) else 0
    aim = {w.lower() for w in WORD.findall(str(draft.get("target") or ""))} - DULL
    alone = sum(1 for d in drafts(item) if unsent(d)) <= 1
    mine = next((n for n, d in enumerate(drafts(item), 1) if d is draft), 0)
    best, score = 0, 0
    for k, text in enumerate(steps, 1):
        if step_done(item, text) or not SENDS.match(text):
            continue
        # "with draft 7" is that draft and no other.
        said = {int(n) for n in NAMES.findall(text)}
        if said:
            if mine in said:
                return k
            continue
        shared = len(aim & ({w.lower() for w in WORD.findall(text)} - DULL))
        mark = shared * 2 + (1 if alone and POINTS.search(text) else 0)
        if mark > score:
            best, score = k, mark
    # One shared word on its own is a date or a channel two messages both name.
    return best if score >= 3 else 0


def next_id(board: dict) -> int:
    """Numbers are never reused, so 'do 4' means the same thing next week."""
    used = [int(i.get("id", 0)) for _, i in items(board)]
    nid = max(board.get("next_id", 1), max(used, default=0) + 1)
    board["next_id"] = nid + 1
    return nid


def set_state(item: dict, state: str, note: str = "", who: str = "") -> None:
    stamp = datetime.now().strftime("%H:%M")
    item["state"] = state
    item["state_at"] = stamp
    item["state_note"] = note
    if who:
        # A job that was never with anyone may hold `"waits_on": null`, and
        # setdefault hands that None back rather than a dict to write into.
        if not isinstance(item.get("waits_on"), dict):
            item["waits_on"] = {}
        item["waits_on"]["who"] = who
        item["waits_on"].setdefault("since", stamp)
    if state in CLOSED_STATES:
        item["closed_at"] = now()
    else:
        item.pop("closed_at", None)
    item.setdefault("history", []).append(
        {"at": now(), "state": state, "note": note or (f"with {who}" if who else "")}
    )


def is_open(item: dict) -> bool:
    return item.get("state", "todo") in OPEN_STATES


def closed_today(item: dict) -> bool:
    closed = item.get("closed_at", "")
    return bool(closed) and closed[:10] == datetime.now().strftime("%Y-%m-%d")
