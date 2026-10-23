# AGENTS.md

Guidance for AI agents working in this repository.

## Read these two files first

**Before anything else, run `python3 core/settings.py` and read the active client's
`config.json` and `profile.md` in `clients/<client>/`.** DANDORI separates its
universal engine (`core/`) from each client workspace (`clients/<client>/`,
pointed to by `clients/active`). Those two files say whose DANDORI this is: the
owner (a Kraken Client Delivery Lead), the client, the workstream, the rooms, the
people, the house style and the standing rules. This file says how DANDORI Core
works; those say for whom. Where the profile and this file disagree about a
person, a room or a style, the profile wins.

If neither exists, nobody has set DANDORI up yet. Say so, and offer to follow
`core/setup/INTERVIEW.md` with the owner, or point them at the setup page
(`DANDORI setup`). `python3 core/settings.py` prints what DANDORI thinks it knows
and the exact paths of the active client workspace.

Headless runs (the buttons, `DANDORI refresh`, the schedule) get both files and
their workspace paths injected above their prompt by `settings.prompt()`, so the
prompts never need to name a person. A chat does not, which is why it reads them
itself.

## Repository layout (`core/` + `clients/` + `packaging/`)

- **`core/`**: DANDORI Core. The board engine (`board.py`, `digest.py`, `apply.py`,
  `tick.py`, `audit.py`, `check.py`), agent runner (`agent.py`, `compact.py`),
  local server (`serve.py`, `launchd.py`, `setup.py`, `setup_page.py`), renderers
  (`render*.py`), headless prompts (`core/prompts/`), static assets (`core/static/`),
  and setup templates (`core/setup/`).
- **`clients/<client>/`**: The client plugin and workspace layer, modeled on
  Kraken's `plugins/clients/`:
  - `config.json` and `profile.md`: client settings and house-style playbook.
  - `plugin.py`: optional client-specific Python hooks (`ACCOUNT_RE`, `CHECKS`,
    `AUDIT_CHECKS`), loaded dynamically by `core/plugins.py`.
  - `scripts/`: client-specific workflow scripts (`DANDORI scripts`,
    `DANDORI run <script>`).
  - `docs/`: client working documents, handovers, sheets and PDFs.
  - `state/`, `output/`, `logs/`, `tmp/`: the client's `board.json`, `asks.json`,
    `calendar.json`, `prefs.json`, rendered `desk.html` and `desk.md`, and run logs.
- **`packaging/`**: macOS application icon builder (`make_icon.py`) and
  one-command distributable packager (`package_mac.sh` / `DANDORI package`),
  which builds `dist/DANDORI.app`, `dist/DANDORI-<ver>.dmg`,
  `dist/DANDORI-<ver>.pkg` and `dist/DANDORI-<ver>.zip`.

## What this repo is

The owner's to-do list for their client work. Every ticket open in their name in
the client's Asana projects (`config.json` `asana.projects`) lives here, with the
Slack threads it is discussed in, what has happened on it, and the numbered items
of work it has left. The standup is one of the things that moves it, not the
reason it exists.

`state/board.json` is the one durable file, and `board.py` documents it. Days
come and go, the standup happens or it does not, but an item keeps its number
until it is closed. Everything in `output/` is rendered from the board.

The page has two views off that one file. *My work* is what they do; *what I say*
is the words for the next session, from each ticket's `prep` block. Neither
carries its own status, so a script cannot contradict the list.

`news` on the board is the one part that is not their work: what moved around
them that will reach one of their tickets. Add a row only with the route named,
and take it off once that route closes.

**A ticket goes on the board because Asana assigns it to the owner, never because
it matters.** The board is their to-do list, not the programme's status. So a
ticket nobody has given them stays off it even when it is top priority, handled
as an incident, and the only thing the standup discussed: that is a `news` row,
with the route by which it reaches one of their tickets named, and nothing more.
If work in a chat turned up a real finding on it, a code trace or a count out of
production, the finding goes in that row rather than becoming an item, because an
item is something they do. Chasing who Kraken assign to a build is not their work
and never gets a number. The one exception is a ticket already on the board with
an open item of theirs on it: that stays until the item closes, however Asana's
assignee field moves.

`sessions` on the board is every room they still have to speak in, soonest
first, and `script.for_date` says which one the current script was written for.
That session may be an onsite rather than the main standup (`config.json`
`meeting`), which changes how much preparation a ticket needs, so read it before
writing anything for a meeting.

## When the owner asks you to act on their work

**Read the active client's `output/desk.md` (`clients/<client>/output/desk.md`)
first, before answering anything.** That is the board in
markdown: every ticket, its Asana status, its threads, its timeline and its
items, with the drafts already written. Say plainly when `checked_at` is hours
old rather than answering from a stale page.

Requests will usually be one of these, and they arrive without much context
because the context is in the file:

- **"Draft the reply to X"**: find the matching item, check whether it already
  has a `draft`, and improve it rather than starting over. Show the draft in chat
  and wait for approval. Never post it.
- **"Do 3"**: item numbers are permanent and unique across the board, so resolve
  the number from `state/board.json` and confirm which one you mean before doing
  work. Then do the part of it that does not need them, put the product in the
  item's `prepared` block with its sources, and leave them only the steps that
  need judgement, a person or a room. An item is not written until everything
  that could be done for them has been.
- **"Check the codebase for X"**: the profile's `## Data I can reach` names the
  checkout, if there is one. Read that repo's own AGENTS.md before touching it.
- **"What does the data say"**: the production data this desk can reach is the
  read-only replica MCP named in `config.json` `database.mcp`, set up by
  `./setup-mcp.sh`. If that key is empty, there is none: say so. Read only: a
  `SELECT` to see how many accounts a problem covers or what a charge actually
  did is the point, and nothing here ever writes to it. The profile says what
  the replica does and does not cover; do not build a query that needs what it
  lacks. A single account is usually faster to read on the support site,
  `config.json` `client.support_url`. Say which query or page an answer came
  from, and if the server is not there, say that instead of guessing. A missing
  server usually means a lapsed login only the owner can fix, so put it in `gaps`.
- **"What's the status of X"**: answer from the ticket's block, and follow the
  `threads` links if the answer is not there. Say plainly when the file is stale
  rather than guessing.

## A question asked from the page

Every job and every ticket on the desk carries an **Ask or change** box, and
`prompts/ask.md` is what runs behind it. The question arrives with the ticket, the
item, its draft and everything already asked about it attached, and the answer
goes back onto that card, in `state/asks.json`. Asked from inside an answer it is
a follow-up, and that exchange arrives above it, so "why" means the last thing
said and not the job. Half of what arrives there is an instruction rather than a
question, "rewrite this with the latest from the thread" most often, so it edits
the item and reports what it changed.

Two things follow for a chat working in this repo. `output/desk.md` carries those
questions and answers under the jobs they were asked about, so **read them before
contradicting one**: the owner is looking at that answer on the card. And when
they ask the same kind of thing in a chat, answer it the same way `prompts/ask.md`
says to, because the answer should not depend on where they asked.

The same box sits on the speaking view, where the card is the words rather than
the work. An ask from there arrives with the ticket's `prep` block and the session
it was written for, and a change means rewriting `prep.script` or `prep.pushback`
in full, in the house style, since the owner reads it out to the client.

One ask is not about a job. The box above the tickets asks about the day itself,
what to start on, whether tomorrow is covered, and it arrives with every open job
attached. Answer those in item numbers, and read the board rather than the titles
it hands you.

An ask may change the item it was asked about, and it may move where that job
stands when the owner tells it to: "close this", "this is with Alex now". It does
that by running `./tick.py`, which is still the only thing that writes state, so
the move is timestamped into the item's history like any other. What it may not
do is infer the move. Concluding from a thread that something looks finished is
not the owner saying it is, and an ask may never send anything.

## "refresh"

One word, in a chat opened on this folder. **Read `core/prompts/refresh.md` and follow
it exactly.** It is the same routine `DANDORI refresh` runs from the terminal, so the
answer should not depend on which one they used.

In short: sweep every open ticket in Asana, read the threads behind the open
items, put what you find in each ticket's timeline, move the items those events
affect, rebuild both files, and report only what changed. Closed items stay
closed and numbers never change.

`./core/tick.py` on its own prints the same status without spending a single token,
so use that when they only want to know where they are. `./core/digest.py` prints the
whole readable board in one call, which is how a sweep should read it rather than
opening `board.json` a dozen times. It holds itself under 27k characters
so it stays inline, and `--item N` / `--ticket REF` print one record whole. A
sweep writes everything it found as one `tmp/patch.json` and applies it with
`./core/apply.py`, which goes through `board.save()`; the runner rebuilds afterwards.
`./core/tick.py --rebuild` redraws both pages without moving anything.

**Slack has no gate.** Asana's `modified_at` tells a sweep which tickets moved;
a Slack reply moves nothing on the board until a sweep reads it. So the digest's
step S names every Slack thread on a ticket with open work that spoke in the last
month, read from its recorded `last_at`, plus searches for the owner's own
messages, their mentions, and the channels of older threads. Never add a "quiet,
skip" rule for Slack: one such rule once missed two replies on an open item for
days.

**Learning something in a chat is not a reason to leave the board stale.**
Whenever work in a chat turns up something that belongs on the page, a ticket
that moved, a draft that is now wrong, work you did on the owner's behalf, put it
on the board and run `./tick.py --rebuild` in the same turn, without being asked.
They read the page, not the chat, and a chat that knows more than the page is the
one failure this repo exists to prevent. That does not mean a full sweep every
time: write the thing you learned, rebuild, and say in one line what changed.

The rebuild half of that is for a chat and a terminal only. `serve.py` renders
both pages on every load, so an answer to the **Ask or change** box that spends a
step on `--rebuild` is a step the owner watches a spinner through for a page that
was going to be redrawn anyway. `prompts/ask.md` says so where it matters.

## "prep" or "build the script"

**Read `core/prompts/prep.md` and follow it exactly.** Same routine as the Write prep
button and `DANDORI prep`: the refresh sweep first, then the speaking half. It
writes each ticket's `prep` block, `sessions` and `script`, and nothing else, so
item states stay where the sweep left them. Check which session you are writing
for before you start: an onsite needs decisions to land and answers to pushback,
a standup needs two sentences per ticket.

## When the owner says they have done something

Run `./core/tick.py` and nothing else. It records the state on the item in
the client's `state/board.json` and rebuilds both files. Never hand-edit a page to mark
something done.

```
./core/tick.py                  # where everything is
./core/tick.py 4                # finished, nothing comes back
./core/tick.py 2 -w "Alex"      # sent, ball is with Alex
./core/tick.py 2 --mine         # they replied, it is the owner's again
./core/tick.py 5 --dropped -n "the client answered it themselves"
./core/tick.py 1 --undo         # forget the state entirely
./core/tick.py 52 --draft 1     # posted draft 1; moves once its last draft has gone
```

**Sending a message is not finishing an item.** If a reply is expected, the item
goes to `-w <who>`, so the page shows it sitting with them rather than pretending
it is closed. Use plain `./tick.py <n>` only when nothing comes back.

On the page that is the **Posted it** button under every draft, which runs
`--draft K`: it waits on the draft's `reply_from` or the person its `target`
names, closes the job as sent when the draft has `"expects_reply": false`, and
keeps the job with the owner while another of its drafts is still to send. So a
step never tells them to run `./tick.py`; set those two fields instead, and
`audit.py` flags a step that forgets.

When the reply arrives, the follow-up stays on the same number: run `--mine`,
then edit that item on the board with the new draft and a `progress_note` saying
what already happened. Never add a new number for the next leg of a conversation
they are already in, because the numbers are how they refer to their work.

Read the board before answering "what's left". If the owner mentions doing
something that is not on it, say so rather than inventing a number. The pages
show status but cannot change it; the board is the only truth about what is
closed.

## Hard rules

1. **Never send anything.** Every message to Slack, Asana, Notion or email is
   drafted in chat and waits for the owner's explicit approval, even when they
   say "handle it". This applies to replies you were asked to write.
2. **Language follows the reader.** The client's language (`config.json`
   `client.language`) to the client. English to Kraken colleagues, including
   CE, Markets, Core and IE.
3. **Nothing internal reaches the client.** Story points, t-shirt sizes,
   refinement status, build-queue position, internal Asana ticket links and
   Kraken engineer names never appear in anything addressed to the client. Refer
   to the team the way the profile's `## Never reaches the client` says. The
   profile may add more.
4. **Write for what the reader can see.** Kraken engineers do not attend the
   client standup and do not read the client-shared Asana projects. Never cite a
   client ticket, a client comment or "the standup" to them as if they can look
   it up, and never use the client's own case numbering without saying what it
   means unless that wording is already in the same thread. Say what was decided
   and what is still pending.
5. **Writing to the client follows the house style** in the profile's
   `## Writing to the client`: register, sentence length, the client's own
   vocabulary rather than simplified substitutes. When `config.json`
   `language.readings` is on, hard words carry a reading as `{word|reading}`,
   on the whole word, never per character.
6. **Never invent a source.** If you cannot quote the message something came
   from, say you could not find it.
7. **A client-language term in an English line carries its meaning.** The page
   adds it from the board's `glossary`, once per card and once again in the
   script, so write the English plainly and add any missing term to `glossary`
   rather than writing the brackets by hand. An answer in a chat or on a card is
   not glossed for you, so gloss it yourself there: *term (what it means)*.
8. **Ask, don't propose, to engineers when AI did the reading.** A draft or
   ticket for CE, IE or any technical reader asks each technical point as a
   question from what the client said, what the owner saw or what an engineer
   already wrote. Function, list and file names and code-derived answers go in
   `prepared`, not the message. Follow `.claude/skills/ask-dont-propose/SKILL.md`.
   A new message to a channel in `config.json` `slack.form_channels` is a
   request in that workflow form's fields, never a free post.
9. **Respect a hold.** When an item says "do not send this yet", do not draft
   around it or send it because the owner asked casually. Say what it is waiting
   on and confirm they want to override.

## Changing what lands on the pages

Edit the prompts, not the output. `prompts/refresh.md` is the sweep behind
"refresh", `prompts/prep.md` writes the script on top of that sweep,
`prompts/post.md` folds the meeting note in, and `prompts/ask.md` answers one
question about one job. None of them names a person or a client: that is what
the profile is for, so a lesson about this client goes in `me/profile.md` and a
lesson about how the desk works goes in the prompt. The shape in `board.py` is
the contract the renderers expect, so if you add a field, update
`render_desk.py`, `render_standup.py` and `render_desk_md.py` in the same change.

**`settings.py` is the only thing that reads `config.json`.** Everything else
asks it (`settings.client_short()`, `settings.tz()`, `settings.meeting()`), so a
missing or half-written config is handled in one place and the page still draws
for someone who has not finished the setup. Never hard-code a name, a client, a
timezone or a meeting time in code; add a getter with a neutral default instead.
Environment overrides are `DANDORI_*`.

The page is drawn by five modules, split so one job lives in one file:
`render_desk.py` (the shell, the ticket card, the top of the page),
`render_path.py` (the path to closing a ticket, its jobs, drafts and steps),
`render_week.py` (the week strip), `render_dialogs.py` (help and settings) and
`render_standup.py` (the speaking view). `render.py` holds what more than one of
them needs. `render_desk.render()` is still the entry point everything calls.

The setup page at `/setup` is apart from the board: `render_setup.py` draws it
from `config.json`, `setup_page.py` answers its buttons, and `static/setup.css`
and `static/setup.js` style and run it. It writes only what `./setup.py` writes,
through `setup.save()`, so a question added to one belongs in the other in the
same change. It touches a launchd agent only when `setup_page.ours()` says this
checkout owns it, so a second copy of DANDORI on the same Mac never takes over
the first one's jobs.

**Every job needs a day, or it is invisible on the week strip.** Two fields, and
which one depends on who is holding the work:

- The owner's own work carries `urgency`, which is **exactly one of `today`,
  `this-week`, `monitor`**, hyphen, never a space. The strip reads it as a day:
  `today` is today, `this-week` is the next working day, `monitor` is off the
  strip. So an actionable job marked `monitor` is one they will not see coming.
- Work with somebody else carries `waits_on.chase_on`, an **ISO date and never a
  sentence**. A condition ("only if they raise it again") goes in
  `waits_on.what`; a sentence in `chase_on` is a job with no day.

`board.save()` refuses anything else, so a wrong value is an exception where it
was written rather than a job quietly missing from the page an hour later.
`./check.py` also asserts that everything the desk labels **Do now** has a place
on the strip.

**How the page looks and behaves lives in `static/`, not in the Python.**
`base.css` and `desk.css` and `standup.css` are the styles, `base.js` runs the
buttons and the live status, `desk.js` runs the ask boxes. The renderers read
them at render time and inline them, so a change to a stylesheet shows up on the
next page load with no restart, and `node --check` and any formatter can see the
JavaScript the way they could not while it was a Python string.

Do not hand-edit files in `output/`. They are regenerated from the board on
every page load.

**The open page reloads itself when the board moves.** `render.stamp()` is the
size and modification time of `state/board.json`, `state/asks.json`,
`state/calendar.json` and everything in `static/`, the page carries the one it
was drawn from in `data-stamp`, and `/api/status` reports the current one every
couple of seconds. So `./tick.py 26` in a terminal, a sweep on a schedule and an
ask answered in another tab all reach the page on their own. If you add another
file the page is drawn from, add it to `SOURCES` or the page will not notice it
changing.

It holds the reload back for one thing only: something half-written in a
composer, where taking the words away would be worse than the staleness. Then it
shows the amber Board changed button and waits, and for an Undo still on screen
after a status was moved. `tests/reload.js` is what keeps that true, and
`./check.py` runs it.

**A prioritization round comes from `prioritize.py`, if there is one.** Some
programmes have a lead who posts the build order on a fixed cycle. When
`config.json` has a `prioritization` block, the round falls every
`prioritization.every_days` from `anchor`, posted by `who` in `channel` at
`thread_at`; a round on a holiday runs the next working day, and `moved` records
any other move. The day that matters is the working day before, the last chance
to raise a build. The board's `prioritization` block is the owner's CE builds by
CDL-Owner, nobody else's, and a sweep rewrites it at step P. The desk draws it
as a card under the milestone with the note to the lead ready to copy, and the
week strip marks both days. `./prioritize.py` prints the same; `--dates` lists
the rounds ahead. With no block, none of this appears and step P is skipped.

**Meetings come from `calendar_sync.py`, not the board.** It reads Google
Calendar through a one-tool headless Haiku run and writes `state/calendar.json`:
every meeting in the four weeks the strip flips through, with its Meet link and
any notes docs attached. A meeting is a fact about the week, not a job, so it
never goes on the board and a sweep never writes it. `serve.py` reads it hourly
in working hours and again just after a work meeting ends, and
`POST /api/calendar` reads it now. Nothing ever writes to the calendar. When a
work meeting ends with Gemini notes that no `meeting_note`, `migration_note` or
`other_notes` row covers, `calendar_sync.unread_notes()` names it, the digest
lists it as step N, and the next refresh reads the doc and records it. A chat
asked about the day reads the Meetings section of `output/desk.md`.

## Many writers, one board

A sweep, a prep, several asks from the page and a terminal `./tick.py` can all
be running at once, and they all write `state/board.json`. **`board.save()` is
the only safe way to write it.** It takes a lock on `state/board.lock`, reads
what is on disk, and merges field by field against the copy `board.load()` handed
out, so an ask that finished mid-sweep keeps its answer and the sweep keeps its
events. Items and tickets merge by id, `events`, `history` and `earlier` keep
both sides' additions, and two writers that each made a new item get two
numbers. So never edit the file with an edit tool, `sed` or a string replace,
and never `json.dump` it: that is the one write the merge cannot see, and it is
how good work used to vanish from the page.

Two more things `save()` holds for every model, whichever one is running:

- **An item never disappears.** A save that loses an item from a ticket still on
  the board raises. Close it with `./tick.py <n> --dropped -n "why"` instead.
- **Condensing never loses detail.** A finding or note taken out of a
  `prepared` block moves to `prepared.earlier`, dated, and the card shows it
  under an *Earlier detail* fold. Nobody writes `earlier` by hand.

Only sweeps exclude each other (`state/refresh.lock`). Asks run alongside a
sweep, four at a time, each with its own live progress line.

## The status, and what the page remembers

Every job's status on the page is a button, Jira's idea rather than Todoist's:
a checkbox can only say done, and half of these jobs end in "sent, now waiting
on somebody". Pressing it lists the moves open from where the job stands (with
you: sent and waiting on who, done, with someone else, dropped; waiting: they
replied, done, handed on, dropped; held: start it now; closed: open it again),
and each one posts `/api/tick`, which runs `./tick.py` like the terminal does.
That is the owner saying the job moved, so it is allowed; an agent inferring it
is still not. Undo puts back exactly the fields the tick moved. `j` `k` `x` `u`
and Enter work the To do list from the keyboard, `x` opening the status.

Theme, motion and which folds are open live in `state/prefs.json`, written by
`/api/prefs` and embedded in the page, so the desk looks the same in every
browser and on every port. localStorage is only a cache of it. `prefs.json` is
deliberately not in `render.SOURCES`: opening a fold should not reload the page.

## Which agent runs a button

`core/agent.py` is the only place that knows how Claude Code runs a job, on which
model, and how to read what it says back. `core/serve.py`, `bin/DANDORI` and
`core/scripts/run_post.sh` all ask it rather than hard-coding a binary, so a
refresh pressed on the page, typed in a terminal or fired by launchd is the same
run. Claude Code is the only CLI; if it or its credential is unavailable, the
job stops rather than falling back to a less restricted runner. Refresh and prep
default to Sonnet 5.5, while ask and post default to Opus 5.5. Models are Kraken
LiteLLM ids built from `config.json` `models.region`, overridable per job in
`config.json` or with `DANDORI_REFRESH_MODEL` and friends. Prompts go in on
stdin, with the owner's settings and profile above them, because
`core/prompts/post.md` alone is over 50KB and that is no business of the command
line.

A sweep is worth nothing without Asana and Slack, so `agent.blockers()` checks
them before a token is spent. A lapsed grant is not a refusal: the Refresh and
Write prep buttons and `DANDORI refresh` walk the login first and only then refuse,
and a run that stops mid-way on a dead connection starts the login too.
`./setup-mcp.sh` adds the servers and walks the logins, `./setup-mcp.sh --check`
and `DANDORI mcp` report where they stand. Only the owner can approve a grant in a
browser, so a lapsed one goes in `gaps` rather than being worked around.

Headless Claude Code has a real `Task` tool, which `cursor-agent` did not, and a
sweep that hands itself to a subagent loses the board. `prompts/refresh.md` and
`prompts/prep.md` forbid it by name; keep that wording if you touch them.

The scheduled jobs (the page server, the post-standup fold-in, the optional
morning sweep) are launchd agents written by `launchd.py` from `config.json`,
labelled `com.DANDORI.*`. Run `./install.sh` again after changing the meeting
or the timezone.

## Before you say you are done

```
./core/check.py
```

Both languages parse, every `fetch` handles a failure, the page still reloads
itself without eating their typing, the board is a shape the renderers can
survive, both pages render, and anything out of date is listed. It takes under a
second, it never writes, and it fails loudly on the two faults that have
actually taken this page down: a promise chain with no `.catch()`, and a board
field holding the wrong type.

`board.save()` refuses a board that would not render, so a shape error is an
exception at the point of writing rather than a broken page an hour later. Fix
the item and save again; the file on disk is untouched until the board is sound.

## House style for this repo

Python is standard library only, no third-party packages, and the front end has
no build step or framework for the same reason: the whole thing has to still run
in two years with nothing installed. Python 3.9 is the floor, because that is
what a Mac ships. `node` is used by `./check.py` to parse the JavaScript if it
happens to be there, and the check says so and carries on if it is not. Shell is
zsh. Commit messages follow the "Prior to this change / This change" structure
used in `git log`, and use an `Assistant-model:` trailer rather than
`Co-authored-by:` for AI assistance.

Nothing personal is ever committed: `config.json`, `me/`, `state/`, `output/`,
`logs/` and `tmp/` are all in `.gitignore`, and `./check.py` verifies that
nothing from `config.json` has leaked into a tracked file. A lesson about one
client goes in that owner's `me/profile.md`; a lesson about the desk goes in
the code or the prompts, written so it holds for any CDL. `settings.VERSION`
and `settings.VERSION_NAME` name the current release (`DANDORI version`).
