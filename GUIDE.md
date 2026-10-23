# How DANDORI works

DANDORI is your to-do list for one client and one workstream. Every ticket open
in your name in the Asana projects listed in `config.json`, the Slack threads it
is argued out in, what has happened on it, and the numbered work it has left. A
Dock app opens it, a Refresh button brings it up to date, and the numbers stay
put. Its name comes from `config.json` `desk.name` (default "DANDORI"), and the
terminal command from `desk.command` (default `DANDORI`), which is what the examples
below use.

Who DANDORI is for lives in two files that git never sees: `config.json`, the
machine-readable settings (IDs, names, meeting times, timezone, models), and
`me/profile.md`, the owner's playbook in prose (the client, the rooms, the
people, the house style for writing to the client, the standing rules).
`settings.py` is the one module that reads `config.json`, and every headless run
gets both files prepended to its prompt by `settings.prompt()`, so the prompts
themselves stay the same for every owner.

**The board is the product.** `state/board.json` holds the tickets and the
items; `board.py` documents it. An item is with you, with somebody else, or
finished, and it keeps its number until it closes, so "do 4" means the same
thing next week. Everything else in the repo either writes to the board or draws
it.

**One page, two views.** *my work* answers "what do I do". *what I say* answers
"what comes out of my mouth in the next meeting", in the order that meeting walks
the board, with the script in the client's language. On the page they are
labelled with the client's short name (`config.json` `client.short`) in front.
Same tickets, same file, and `1` and `2` switch between them. A ticket that needs
airtime carries a *raise at* pill on the work view and appears under its script;
a ticket in the script links back to its open items. Neither view has a status of
its own, so a script can never contradict the list.

The colour means one thing everywhere: red is with you, amber is blocked, blue is
with somebody else, green is closed. The speaking view runs on warmer paper with a
plum accent, so you can see which view you are in without reading the tab.

Three things put work on the board, and the main standup is the day's forcing
function:

| | When | What it does |
|---|---|---|
| **Refresh** | any time you press it, and it is the only button on the work view | Sweeps every open ticket and thread, moves what changed, and rewrites or deletes the drafts the threads have overtaken. Never rewrites a word of what you say. |
| **Write prep** | on the speaking view, when you sit down before a meeting | The same sweep, then the words for the next session on top of it. It reads **Update prep** once one exists. |
| **Standup fold-in** | when the main standup ends, on its days in `config.json` `meeting` | Reads the meeting note and moves the board. Scheduled, because the note appears while you are still in meetings. |

The words are the reason those are two buttons rather than one, and the split
runs along the tabs: everything on the work view belongs to Refresh, including
the drafts, and prep only ever adds the speaking half. A script is written for
one room on one day, and some of it you have already cut or rehearsed, so a sweep
at 17:00 that rewrote it would throw away the version you fixed at 16:00 and
would redo tomorrow's script because a ticket moved in a way the room does not
care about. A refresh therefore moves the work and leaves the script where it is,
and both views then say so: amber at the top of the speaking view, and **Prep is
older than the board** beside the next room on the work view. Either one means
press **Update prep**, which is on the speaking view where the words are.

**A sweep takes five to eight minutes**, because it reads every open ticket, the
threads behind every open item and the internal build tickets those depend on,
and no model makes that shorter. What it does say while it runs is which of those
it is on, with the minutes counting, and `logs/refresh-<date>.log` keeps a timed
line per step and the total at the end, so a slow sweep can be read rather than
guessed at. The one outcome that is never reported is a false one: a run whose
connection dropped can still exit clean, and a sweep that read nothing is
reported as a failure and retried rather than as **Refreshed**.

Before a standup you have 20 minutes and you spend them reading, so the speaking
view is for preparing. Replies, investigation and ticket updates wait for the
work view afterwards.

The desk is written twice: `output/desk.html` to read, `output/desk.md` to hand
work back from. Open a chat in this repo and say "draft the reply to Sato-san"
or "check the codebase for X", and `AGENTS.md` points the agent at the markdown
(after `config.json` and `me/profile.md`) so it starts with the full picture.

## The script

Press **Write prep** in the app, or run `DANDORI prep`. Nothing writes the script on
a schedule, deliberately: you build it when you sit down, so it is written
against the replies that landed overnight rather than against an early-morning
run.

1. Runs the full refresh first, so the script sits on the newest state.
2. Puts your tickets in the order the cards appear on the meeting's board, and
   says roughly when you are up.
3. Traces what each agreed fix does **not** cover: what falls outside it, what
   piles up because of that, who owns the pile, and what cleanup means. This is
   where the real questions come from, and it is the step that stops the client
   raising something you had not thought about.
4. Writes the words you say out loud, in the client's language and the house
   style in `me/profile.md` `## Writing to the client`, with readings above the
   hard words when the profile asks for them and English underneath, plus the
   questions you need the client to answer.
5. Marks the items that can only be closed in the meeting, so they show on both
   views.

A ticket with nothing outstanding gets a two-line status block and says *status
only*. That is a good thing to report in one breath, and padding it wastes
standup time.

Internal build tickets, story points, refinement status and delivery dates never
reach the script. They stay on the desk side, where they are for you.

## The standup fold-in

1. Finds today's main standup note in Notion (the title in `config.json`
   `meeting.notes_title`) and reads both the summary and the **full
   transcript**. The transcript is the valuable half: pushback, undecided forks
   and quiet takeaways only exist there.
2. Reads the board so it can show what the meeting changed.
3. For each ticket, gathers **every conversation it lives in**: the Asana ticket,
   the internal build ticket, the CE refinement thread, the CE help thread, a DM
   with the prioritization lead. Threads get read in full, because a reversal
   lands in reply 30 and an engineer saying "actually this is harder than I
   thought" outranks anything said in the room.
4. Opens new items for what the meeting created, and moves the ones it answered.
   Existing numbers are reused, never reassigned.
5. Records anything other people owe you on the item it blocks, with a chase
   date, so there is no second list of waits to reconcile.
6. Writes the replies you now owe, each one sitting inside the item it belongs
   to.

**Everything is grouped by ticket.** One block per ticket holds its Asana status,
where it stands, the timeline, the items, the drafts, what is undecided and every
thread it lives in. The only cross-ticket structure is the running order at the
top, which the page derives rather than anyone writing.

The Notion note lands about five minutes after the meeting ends, but meetings
overrun. So each attempt is a cheap agent run that exits immediately when the
note is missing, and the runner retries every five minutes for 45 minutes from
the end of the meeting.

```sh
./run_post.sh            # polls until the note appears
./run_post.sh --force    # fold today's note in again
./run_post.sh --once     # single attempt, fail if the note isn't up yet
```

`POST_MODEL`, `POST_TIMEOUT` (900), `POST_DEADLINE` (the time it gives up) and
`POST_RETRY` (300 seconds) all override.

## Getting at it during the day

Run `./install.sh` once. It puts the DANDORI app, named from `desk.name`, in
`~/Applications`, which you drag to the Dock, and links the `DANDORI` command into
`~/.local/bin`. Both point at this checkout, so pulling changes updates them;
rerun it after any change to the command script in `bin/` or to the meeting
settings in `config.json`.

Clicking the Dock icon opens the desk in its own window, no tabs and no address
bar. Behind it, `serve.py` runs on `127.0.0.1:8787` (`desk.port`, or
`DANDORI_PORT`) and renders the page on every load, so what you see at four in the
afternoon reflects everything you have ticked off. It starts on first use and
stays up; `DANDORI stop` ends it.

That server is also what makes the **Refresh** and **Write prep** buttons in
the header work: the button asks the server, the server runs the agent, the page
reports progress and reloads itself when the work lands. Both take the same lock,
so a terminal run and a button press can never write the board at once.

Before spending anything, the server checks that an agent CLI is installed and
signed in and that the Asana and Slack connections are authorised. When they are
not, the page says which one: **Log in** walks a grant that has merely lapsed,
one browser approval at a time, and a server that was never added sends you to
`./setup-mcp.sh`. That check exists because an agent with no tools does not stop,
it improvises. Opening the HTML file directly still works and simply has no
buttons, since a `file://` page has nothing to send a click to.

```
DANDORI              open the desk, print where everything sits
DANDORI s            status only, costs nothing
DANDORI 4            item 4 is finished
DANDORI 2 -w Alex    sent, now sitting with Alex
DANDORI 2 --mine     they replied, it is yours again
DANDORI refresh      sweep every open ticket and thread, rebuild, open
DANDORI prep         sweep, then write the script for the next session
DANDORI chat         open a chat on this folder to hand work over
DANDORI mcp          say where the Asana and Slack connections stand
DANDORI build        re-render the page from the board, no agent
DANDORI post         fold today's Notion meeting note in again
DANDORI morning      turn the weekday morning sweep on or off
```

`refresh` also works as a single word typed into a chat on this folder.
Both routes run `prompts/refresh.md`, so the answer does not depend on which one
you used: it sweeps every ticket still open in Asana, reads the threads behind
your open items, adds what it finds to each ticket's timeline, and moves the
items those events affect. It never reopens a closed item and never renumbers.

## Keeping the list honest

An item is with you, with somebody else, or finished. Sending a message usually
moves it to the middle one, because the reply comes back on the same number and
the work is not over.

```sh
./tick.py                            # where everything is
./tick.py 4                          # finished, nothing comes back
./tick.py 2 -w "Alex"                # sent, ball is with Alex
./tick.py 2 --mine                   # they replied, it is yours again
./tick.py 5 --dropped -n "the client answered it themselves"
./tick.py 1 --undo                   # forget the state entirely
./tick.py 52 --draft 1               # posted draft 1, the Posted it button
```

The desk opens with **Need to know**, then **To do**: one list, yours at the top,
then what you are not allowed to send yet, then what sits with someone else.
Finished work folds away behind a count. Each line carries how it got there, so a
waiting row says who has it and since when, and the minutes count only what is
still yours. Every row also says *when*: Do now, Held to Wednesday, Chase today,
or "No chase date" when nothing has been set and the thing could sit for ever.

Every job's status is a button. Press it and it lists the moves open from where
the job stands: sent and waiting on who, done, dropped, they replied, open it
again. Each one runs `tick.py`, the same as the terminal, so the move is in the
item's history, and an Undo stays on screen for six seconds. It is a status
rather than a checkbox because sending is not finishing. A page opened from disk
has no server behind it and cannot move anything; close things there with
`tick.py`, or by saying so in chat.

Under every draft, below the words you copy, is **Posted it**. It names what it
will do before you press it (wait on Sato-san, close the job, one more to send)
and does it in one press with the same Undo. The arrow beside it picks someone
else or says nothing comes back. Copying the draft lights it up, so it is waiting
when you come back from Slack or Asana.

## Asking about one job, and changing it

Every job carries **Ask or change** at the foot of its card, every ticket one
under its threads, and every draft a **Rewrite or ask** in its header. It is a
chat box: type, press enter, and the answer lands on the card in a minute or
two. The words travel with what you were looking at, so both halves of the same
conversation work with nothing else said:

> what do you mean by that term in step 2?

> rewrite this with the latest from the refinement thread, and tell Sato-san
> we are still checking

The second one reads the thread, rewrites that draft in the ticket, and says what
it changed. When the thread does not settle the question, the draft says that
politely rather than inventing a date, because a wrong guess to the client cannot
be walked back. The thing that used to stop either question being asked,
explaining which draft you mean to a chat that has never seen it, is gone.

Answers land back on the card and stay there, in `state/asks.json`. A chat window
is gone by tomorrow; why a draft says what it says is worth having next to the
draft, and `output/desk.md` carries the same questions so a later chat does not
contradict an answer you are reading.

**An answer takes a minute or two, and much of that is not the model.** The CLI
spends 15 to 40 seconds starting a session, authenticating and bringing up the
MCP servers before it reads a word, and a cold start on a slow gateway is longer
still, so no ask is ever instant. Asks run on the same heavy model as everything
else here, because half of what comes out of this box is read by the client and
the answer is worth the wait. `config.json` under `ask` pins a faster model if
that trade ever stops being worth it, and `DANDORI_ASK_MODEL` does it for one
question. Either way `logs/ask-<date>.log` records which model answered, how many
seconds it took and how many steps it needed, so the question is settled with
numbers.

While it is thinking, the card says what it is actually doing: the CLI is asked
for its event stream rather than its prose, so every file it opens and every
thread it reads reaches the page as it happens. A wait with a step in it reads
as work, and a wait with nothing in it reads as a hang.

A change edits that item in `state/board.json` and nothing else, and the answer
says in one line what changed. Two things it will not do: mark anything done,
which is `tick.py`'s job alone, and send anything to anybody. `prompts/ask.md` is
the whole contract, and it inherits every hard rule the refresh has, including
holds and what may never reach the client.

The speaking view has the same box at the foot of every card, and a **Rewrite or
ask** in the *Say this* header. There what is on the card is the words, so the
question goes off with the `prep` block and the session it was written for, and
"rewrite this more plainly" or "what do I say if they push on the date" comes
back as the line to read out, in the house style and with readings where the
profile uses them. It is the same thread as the work view: a question asked in
front of the script is on the ticket's card on both tabs.

One box is not on a card. Above the tickets sits the ask for the questions that
belong to no job: what to start on, whether tomorrow is covered, what you have
forgotten. That one goes off with every open job attached and answers in job
numbers, because "start with 8, it is the only thing Alex is waiting on" is an
answer you can act on and "you have a few things pending" is not.

### Pulling on one answer

An answer takes another question. **Ask about this answer** at the foot of one
opens a box inside it, and what you asked and what came back travel with the
follow-up, so `why?` is a whole question there and the same word typed into the
box at the foot of the card is not. Follow-ups nest under the answer they came
off, however deep the digging goes.

Nothing on a card grows without bound. Every exchange shuts to one line, the
question and when you asked it, with only the newest thread open; past two, the
older ones go behind a single **3 earlier questions on this card**. The
**&times;** in a bubble's corner forgets that exchange, and anything asked off
the back of it, out of `state/asks.json` for good. It is the only thing on these
pages that deletes anything, and it is your own record, not the board.

The same ask works from a terminal, and lands on the same card:

```sh
DANDORI ask 8 "is that true about refinement?"
DANDORI ask HOLD-RELEASE "who owns the resolver change on their side?"
DANDORI ask desk "what should I start on this evening?"
```

Without the server, on a page opened from disk, **Copy for a chat** puts the
same question with its job named on the clipboard. `DANDORI chat` is still the right
place for the long ones: work spanning three jobs, or anything where you will be
reading code together.

State lives on the item in `state/board.json`, so rebuilding a page never loses
it and nothing has to be reconciled across days. That file is also how a chat in
this repo knows where you are without you explaining: `AGENTS.md` tells the agent
to run `./tick.py` rather than editing a page by hand.

## Reminders

`POST_REMIND=1` pushes the open items into an Apple Reminders list at the end of
a fold-in run. Anything with you is due that day, anything held or sitting with
someone else is due on its chase date and says so in the title. The note carries
the why, the link and the draft. Finished items are skipped, and re-running
replaces the list rather than duplicating it. `--list` picks the list's name.

```sh
python3 remind.py state/board.json --dry-run
python3 remind.py state/board.json --list "Work"
```

macOS asks for Reminders access the first time, so run it once by hand before
relying on it in the scheduled job.

## Onsites, and standups that are not happening

The next meeting is not always the main standup. Onsites and workshops replace
it, and it is only ever said out loud in the room, so both agents listen for it
and keep `sessions` on the board: what kind of session, when, what it is for, and
which standup it displaces.

An onsite is a day rather than fifteen minutes, so the speaking view carries more
for one: the shape of the day and what to have ready at the top, then per ticket
a decision to land in **What I need back**, up to six script blocks instead of
four, and the answer ready for the pushback you can see coming. The
work view says what is next and, when the script in the app was written for a
different session, says that too rather than letting you walk in with the wrong
one.

A skipped standup stays visible, because any ask that was waiting for it now has
to move into Asana instead. If an agent is not confident about a date it records
nothing and flags it, since a missing warning beats a wrong one.

## Reading the page

- `1` shows your work, `2` what you say, `s` strips everything but the
  client-language script at a larger size, `/` finds anything, `?` explains the
  whole thing. The tab you were on survives a reload, each view remembers where
  you had scrolled to, and the morning of a session opens on the script when one
  has been built and the meeting has not started yet.
- **Jump to** is the first thing on both views, with the sections and every
  ticket on it, and the chip for the card you are looking at lights up as you
  scroll. `/` opens a finder that takes a ticket tag, an item number or any word
  from a title.
- **Need to know** opens with the only line the page writes itself: what needs
  you, which item to start on, how long it all adds up to, and whether the next
  room is close enough to matter. It is counted from the items rather than
  written by an agent, so it cannot editorialise and cannot go stale. Under it,
  the things you cannot act on: the next room, the client news, and, on the rare
  day it is filled, a red **Needs you now** strip for something that wants you
  inside the hour and is not yet an item. The block folds and remembers being
  folded.
- On the work view, **To do** is one list: yours at the top,
  then what you may not send yet, then what sits with someone else, with finished
  work folded away behind a count. Each ticket header carries what Asana currently
  says about it, every value labelled with its field, and links out live on their
  own row underneath. A draft always sits inside the item that needs it.
- **News**, inside Need to know and labelled with the client's short name, is
  things that are not your tickets but move them, each with the route by which
  it reaches you and a link to where it was said. A row shows its first sentence
  and that route; the detail behind it is one press away.
- Inside a card the sections are colour-keyed by what they are for. Red is the
  work, blue is the short answer and the timeline, amber is undecided, and grey
  reference sections (shorthand, threads, a day where nothing moved) start folded.
- **Two things stay open on a card, and everything else folds.** Where it stands,
  and the jobs. The gates still to fall, the timeline, the shorthand and the
  threads all sit behind a summary line that carries the part worth knowing
  daily: *Timeline, 10 moves today*, or *2 of 6 done, next, client confirm the
  bills, with Sato-san*. Answering the question on the fold means most of them
  never need pressing, and a fold you do press stays open on that card until you
  shut it. Why a job exists, what already happened on it and the message it came
  out of fold the same way, under the job, since they are read once and the work
  is read every day.
- **Where it stands** answers for both sides. Under the short answer sits the
  Kraken build the client is waiting on: which of the six stops it has reached,
  why it is not moving, who is on it, and the one line about timing that may be
  repeated to the client. A ticket with no build says that instead, since an
  empty queue is a fact about the ticket too.
- **What is left before this closes** is every gate still to fall, in order,
  including the ones that are nobody's job: an engineer being assigned, a
  release, a feature flag switched on, the client checking the output that came
  out after it. It is why a ticket marked Clear can still show four things
  outstanding. Folded, with the next gate and whose it is on the fold.
- Cards come in the order they should be worked: the ones with something on you
  first, and among those, what you promised a person before what you promised a
  room.
- The speaking view is the same card in a plum key, in the order the meeting
  walks: the issue in 20 seconds, where it stands, **what moved since last
  time**, then **Say this**, then **What I need back**: one list holding the
  decisions to land, the questions to get answered and anything already owed to
  you, each row saying which it is. What moved, scope and pushback all fold
  away, because none of them is what you say when the room turns to you.
- **What moved on this ticket**, on the speaking view, is the whole history
  rather than the gap since you last spoke: one fold per day counting back, with
  a line marking where you last talked about it. "When did you first tell us
  about this" is asked of the same card as "what changed this week", and the
  answer used to be a tab away.
- Timelines group by day, most recent first inside the fold and everything older
  behind the days it covers.
- Tickets where you have done your part but Asana has not closed them, and
  tickets closed on both sides, sit in their own folds under the live ones.
- *what I say* is meant to be read aloud verbatim. The script is in the client's
  language with English underneath, readings above the hard words when the
  profile uses them, and each block copies the script without the markup.
- Any client-language term left standing in an English line gets its meaning in
  brackets after it: `<term> (its plain meaning)`. Once per card, and once more
  in the script, since the script is all that is left in script-only mode. The
  words come from `glossary` on the board, so a term is explained the same way
  everywhere and nobody writes the brackets by hand.
- The amber **Check before you speak** box holds unknowns and hard cautions,
  including anything you must not say to the client.
- Every job carries the steps to do it, in order, before anything else on the
  card. The reason it exists is at the bottom in one line, because it is the one
  question you never ask of your own list.
- Where a job had a part that did not need you, that part is already done and
  sits above the steps in a green **Prepared for you** block. It leads with the
  answer, keeps like-for-like evidence in its table with the row that matters
  marked, and labels requirements or constraints separately. An amber **Still
  unanswered** strip says what the work could not settle, so nothing reads as
  finished when it is not. When the product is a document rather than a message,
  the block hands you the file itself. If it is for a meeting, it links to the
  spoken version under *what I say*. What is left underneath is sending,
  deciding or speaking.
- Work agreed in a thread that Asana has never heard of still gets a card, marked
  **no Asana ticket yet** with what would raise one, because a handover nobody
  wrote down is exactly the thing that waits a month.
- A day in the room shows its running order under **Next room**, with the hours
  that concern you picked out. An onsite that swallows the standup usually still
  holds it, an hour later and in person.
- It is served only on `127.0.0.1`, so another device cannot open it over the
  network. Nothing is hosted, so no client thread or draft leaves the machine.
- It is plain HTML in one file. Keep it, mail it, print it.

## Pieces

| File | What it is |
|---|---|
| `board.py` | The board: what it holds, how state moves, where numbers come from. |
| `digest.py` | The whole board as one readable print, under 27k characters, so a sweep reads it in one call. `--item N` and `--ticket REF` print one record whole. |
| `apply.py` | Applies a sweep's `tmp/patch.json` through `board.save()`. |
| `check.py` | Everything that has broken the page before, checked in under a second. Never writes. |
| `setup_page.py`, `render_setup.py` | The setup page at `/setup`: every step a new owner needs, each a button, and the questions as a form. A fresh install opens on it, and `/` sends there until there is a config and a board. Loopback only. |
| `setup.py` | The same questions in the terminal, into `config.json` and a first `me/profile.md`. |
| `bootstrap.sh` | The one-line install: clone or pull into `~/Projects/DANDORI`, then `./install.sh`. |
| `setup/` | `QUESTIONS.md` (every key explained), `INTERVIEW.md` (a Claude chat that fills the profile), and the two templates. |
| `prompts/refresh.md` | What "refresh" means, for the button, `DANDORI refresh` and a chat alike. |
| `prompts/prep.md` | The script: the sweep, then what goes in `prep`, standup or onsite. |
| `prompts/post.md` | How the standup gets folded into the board. |
| `prompts/ask.md` | One question about one job: what it may answer, what it may change, what it must never do. |
| `AGENTS.md` | How a chat in this repo picks up the list and acts on it. |
| `settings.py` | The one module that reads `config.json`, and `settings.prompt()`, which puts the owner's config and `me/profile.md` above every headless prompt. |
| `config.json` | The owner's settings: who they are, the client, Asana projects, meeting days and time, timezone, Slack hints, models. Not in git; `setup/config.example.json` is the template. |
| `me/profile.md` | The owner's playbook in prose: the client, rooms, people, house style and standing rules. Not in git. |
| `agent.py` | Which CLI runs a job, on which model, and how to read what it says back. The one place `serve.py`, the `DANDORI` command and `run_post.sh` all ask. |
| `setup-mcp.sh` | Adds the MCP servers an agent run needs and walks their logins. `--check` only reports. |
| `render.py` | Shared styling, reading markup, item lifecycle and the common blocks. No network, no LLM. |
| `render_desk.py` | The board into `output/desk.html`, both views in one file. |
| `render_standup.py` | The speaking view inside that file, standups and onsites. |
| `render_desk_md.py` | The board into `output/desk.md`, for handing work back in chat. |
| `tick.py` | Move items along and rebuild. The only way state gets recorded. |
| `make_doc.py` | Turns a document in `docs/` into the PDF that goes to the client. Chrome prints it, because a PDF in a non-Latin script needs an embedded font. |
| `serve.py` | The local page server: renders on every load, runs an agent when a button asks, and walks the sign-in when something is not authorised. Loopback only, keyed. |
| `bin/` | The one command, linked as `DANDORI` (or whatever `desk.command` says). Opens the window, moves items along, asks about a job, starts a run. |
| `install.sh` | Links the command, builds the Dock app, writes the launchd jobs, keeps the server running. Safe to rerun. |
| `launchd.py` | Writes the launchd jobs from `config.json`: the repo path, the timezone, the meeting days and time. Called by `./install.sh` and `DANDORI morning`. |
| `app/` | The icon generator: the Kraken mark over a ticked list, read from a kraken-core checkout at build time. No client mark, so the app fits whoever the work is for. |
| `run_post.sh` | The fold-in entry point. Polls for the Notion note, then folds it in. |
| `docs/` | Documents that leave the desk: the HTML that is edited and the PDF that is sent. The page serves them at `/doc/`, so an item can hand you the file. |
| `output/` | `desk.html` and `desk.md`, both rendered from the board. |
| `state/` | `board.json`, the one durable file, `asks.json`, every question asked from a card, and `history/`, a copy of the board taken before each agent run. |
| `keep.py` | Takes that copy. Every entry point calls it, because `state/` is not in git and an agent that decides a ticket no longer belongs to you can otherwise take a week of numbered work with it. |
| `logs/` | `refresh-<date>.log`, `prep-<date>.log`, `ask-<date>.log` and the fold-in logs. |

## Reading markup

When the profile's language uses readings (`config.json` `language.readings`),
the agents write `{word|reading}` and the renderers turn it into real ruby
annotations above the word. The reading goes on the whole word, not on each
character. When readings are off, nothing is written in that form and the page
shows the words plain.

## Schedule

Two things run on their own, and a third when you turn it on. The fold-in fires
on the main standup's days in `config.json` `meeting`, starting when the meeting
ends, and polls for 45 minutes until the Notion note appears, because that note
lands while you are still in meetings. The page server starts at login and stays
up, so the Dock app always finds it. `DANDORI morning` turns on a weekday sweep at
`desk.morning_sweep`, so the morning brief is written before you sit down, and
`DANDORI morning off` turns it off again. Everything else is a button.

The jobs are labelled `com.DANDORI.serve`, `com.DANDORI.post` and
`com.DANDORI.brief`, and `launchd.py` writes them from `config.json` (the repo
path, the timezone, the meeting days and time) when `./install.sh` or
`DANDORI morning` runs. If the Mac is asleep when one is due, `launchd` runs it as
soon as it wakes.

```sh
launchctl kickstart -k "gui/$UID/com.DANDORI.post"
launchctl list | grep DANDORI
```

To change the days or times, edit `meeting` in `config.json` and rerun
`./install.sh`, which writes the jobs again and reloads them.

## Requirements

- Claude Code on the PATH and able to reach a model. If it or its credential is
  unavailable, the run stops rather than falling back to a less restricted CLI.
- `./core/scripts/setup-mcp.sh` run once, and the Asana and Slack grants approved in the
  browser it opens. `./core/scripts/setup-mcp.sh --check` or `DANDORI mcp` says where they stand.
- `python3`. No third-party packages.

## What an agent opened on this folder can reach

The work needs things it cannot get from the filesystem: the Slack threads, the
Asana projects, the Notion meeting notes, the Miro diagrams and, if you have
one, the client's production data.

Four of those are HTTP servers on Kraken's AI Hub, at
`https://hub.ai.ktl.net/mcp/<name>`: **Slack, Asana, Notion, Miro.** They carry
their own OAuth sign-in, so this repo points at them rather than restating any
credential. `./setup-mcp.sh` adds them at user scope and then walks
`claude mcp login` over whatever still needs approving. In Cursor the same four
come from marketplace plugins under Tools & MCP instead.

**Database access is optional.** If you have a read-only analytics replica for
the client, `config.json` `database.mcp` names its MCP server and
`./setup-mcp.sh` declares it. It is for reading: a hold list or an account's
charges can be checked rather than asked about, and nothing on the desk ever
writes to it. `me/profile.md` `## Data I can reach` says which databases it
covers and what not to query. With no `database.mcp`, the agents say they have
no data rather than guessing, and a single account is read on the client's
support site (`config.json` `client.support_url`).

**Never declare a server a plugin or a host app already provides.** An entry of
the same name shadows it and inherits none of its setup, so a sign-in can fix a
server the chat is not the one using. A Cursor `mcp.json` that copies a
plugin's server without the plugin's own headers is the usual way this happens.
A replica server is safe as long as nothing else claims its name.

Four things follow from how an agent loads all this.

**A desktop app's own servers are not the CLI's.** Claude Desktop injects a rich
set of MCP servers into a chat you open in it, and a headless `claude -p` started
by `serve.py` sees none of them: it reads the user and project config only. So a
chat window having Asana is no evidence that Refresh does. `DANDORI mcp` asks the
CLI itself, which is the only answer that counts.

**A sign-in only reaches sessions started after it.** MCP state is read when a
session starts, so an existing one keeps whatever it had. After approving OAuth,
start a new chat, and check with a real call rather than a dot in a panel.

**A terminal agent cannot sign itself in.** `DANDORI refresh`, `DANDORI prep`, the
fold-in and the Ask box all run a CLI with no browser. `agent.py` refuses the run
instead of letting it improvise, and the page says which connection needs you.
`./setup-mcp.sh` is what opens the browser, and only you can approve what it
opens.

**A local database server is the one a terminal run can reach on its own.** When
the replica's MCP server is a local command (for a Kraken replica, a
`kraken db proxy` riding on the laptop's own credentials), there is no OAuth
window to open. Agent runs here bypass the permission prompt, so it loads
without anyone approving anything. The one thing it cannot do for itself is
renew the session it rides on. When that lapses the queries fail, and the run
should put the login you need to run in `gaps` rather than reporting the data as
empty.

**Cloud agents and the phone read none of this.** They never see this laptop's
config. The four hub servers are HTTP, the transport a cloud VM accepts, with one
catch: the hub sits on the Kraken network and is only reachable from outside it if
it is exposed publicly.

**A local database server is the exception, and it cannot follow.** It is a
local command, not a URL. It starts a proxy on this laptop and authenticates with
this laptop's credentials, so there is nothing a cloud VM or a phone could point
at. Any run that is not on the machine has no database, and should say so in
`gaps` rather than guessing at numbers.

## Any model, any assistant

Nothing here is tied to one model. `config.json` names a default per job, so it
is one file rather than an environment variable somebody has to remember to set.
All four jobs run on Opus 5.5 by default, because everything this desk produces
is read by a CE or heard by the client and none of it gets a second draft once
you have said it in the room. The model ids come from the gateway region in
`config.json` `models.region` (eu, us or apac), giving ids like
`claude-low-carbon-eu-opus-5-5`.

`DANDORI_REFRESH_MODEL`, `DANDORI_PREP_MODEL` and `DANDORI_ASK_MODEL` pin one job,
`DANDORI_MODEL` all of them, `DANDORI_CALENDAR_MODEL` the calendar read, `DANDORI_EFFORT`
the thinking effort, `DANDORI_AGENT` which CLI runs (`cursor-agent`, say), and
`POST_MODEL` the fold-in. `DANDORI_PORT` moves the page server and `DANDORI_REPO`
points the command at another checkout.

```sh
DANDORI_PREP_MODEL=claude-sonnet-5 DANDORI prep
POST_MODEL=claude-sonnet-5 ./run_post.sh --force
```

`agent.py` resolves the name, so the page, a terminal and launchd cannot disagree
about which model a job uses. Every refresh, prep and ask logs which CLI and model
ran, how long it took and how many steps it needed, in `logs/<kind>-<date>.log`.
A sweep is the expensive run here: it reads a lot of thread before it writes
anything, so it costs dollars rather than cents, and `DANDORI` on its own answers
"where am I" for nothing.

The prompts name the Asana, Slack and Notion tools as those MCP servers expose
them today, and both say to use the equivalent if a toolset names them
differently rather than skipping the step. The renderers and `tick.py` are plain
Python with no model involved at all, so the desk can always be rebuilt from the
board.

`CLAUDE.md` and `GEMINI.md` are symlinks to `AGENTS.md`, so an assistant that
looks for its own filename finds the same instructions.

## When it breaks

The page still opens, since it renders from the board and the board is only
written by a run that finished. Most likely causes, in order: the Asana or Slack
connection needing re-auth, no agent CLI signed in, or an agent writing malformed
JSON. For the first, the Log in button, or `./setup-mcp.sh` when the server was
never added. For the last, run it again.

```sh
tail -f logs/refresh-$(date +%F).log
tail -f logs/serve.log
```
