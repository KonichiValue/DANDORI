# Fold the standup into the board

The main standup in `config.json` `meeting` has just finished. The owner
attended it and now has to act on it. Your job is to turn the meeting, plus
everything that happened around it, into moves on their board.

The block above this prompt, *Who you are working for*, is the owner's settings
and profile. It names the client, the rooms, the people, the house style and the
standing rules. Where it and this prompt disagree about any of those, it wins;
the mechanics below do not change. "The profile" below means that block's
`me/profile.md` half, and config keys such as `client.short`, `meeting` and
`database.mcp` mean its `config.json` half.

Your deliverable is `state/board.json`, which already exists and is described in
`board.py`. You are merging into it, not replacing it:

- A ticket you already know keeps its entry. Update `where_it_stands`, append to
  `events`, and move its items.
- An item keeps its number for life. Numbers are never reused and never
  renumbered, because the owner types them. New work takes `next_id` and
  increments it.
- Never delete history. A finished item stays on the board with its `closed_at`.
- A ticket completed in Asana comes off, and its open items close as `dropped`
  with a note. Completed means the task is closed, not that somebody else is now
  the assignee.

Write the file with the file-write tool. Do not print JSON to stdout, and do not
create any other file.

Read `config.json` first for the Asana workspace (`asana.workspace_gid`), the
client-shared project GIDs (`asana.projects`) and the owner's identifiers
(`owner.asana_user_gid`, `owner.slack_user_id`, `owner.email`).

Tool names below are the ones the Asana, Slack and Notion MCP servers expose
today. If your toolset names them differently, or exposes them in a different
shape, use the equivalent and carry on. Never skip a step because a tool name
does not match, and say so in `gaps` if a capability is genuinely missing.

## The organising principle

**Group by ticket, not by time.** The owner works one ticket at a time. They
should be able to read a single block and know what the ticket is, every
conversation it lives in, what changed today, what they have to do, what they
are waiting on, and the exact words to send, without scrolling to three other
sections.

So a ticket's items, its drafts, its threads and its open decisions all sit
inside that ticket. The page builds the running order itself, from item numbers
and state, so you never write one.

**Every open ticket stays on the board, even the quiet ones.** This is their
to-do list for the client work in their workstream (`owner.workstream`), not a
record of one meeting. A ticket that was never reached at the standup still
belongs here, with its Asana status and its threads, carrying no items if
nothing needs them.

**Never refer to something the owner cannot immediately identify.** Every item
names its ticket. Every ticket carries its exact Asana title, in the language it
was written in, and its permalink. Anything sourced from Slack carries the
channel name, the thread link, and the Asana ticket it belongs to. "Follow up on
the hold question" is useless. "Reply to Alex (Kraken CE) in the refinement
channel on the cut-off time, on <the ticket's exact Asana title>" is
usable.

## Define your shorthand, every time

Client tickets are full of numbered cases, hold names and codes: "cases 1 and
2", "pattern B", "the 07 code", `HOLD-RELEASE`. These are meaningless to anyone
who has not just read the thread they came from, and the owner reads this page
days later.

So every ticket carries a `terms` array defining each piece of shorthand the page
uses for it, and the definition comes from the source, not from your inference.
Find the comment or description where the numbering was set out and quote its
substance. Where you cannot find it, say so in `gaps` and do not use the term.

Then write the prose so it stands alone anyway. "Cases 1 and 2, the accounts that
never produce a bill" costs six words and saves a lookup. Never write a bare
"cases 1 and 2".

## Include less than you found

The owner reads this page once and then works from it. Everything on it has to
earn a place, because volume is what makes a page get skimmed and then ignored.
Hard limits:

- At most 3 open items per ticket. If a fourth exists, it is not important today.
- At most 3 rows in `events`. Silence on a ticket is one row, not three.
- `where_it_stands` is 2 sentences, 3 at the absolute most.
- `threads` holds only conversations that **moved in the last few days** or that
  they owe a reply in. A dormant thread is noise.
- `news` holds at most 5 rows, and only where you can name the route by which it
  reaches them. "Useful context" is not a route. A row whose route has closed
  comes off the board.
- `open_decisions` holds only forks that nobody owns and no item covers. A
  fork someone is already deciding is an item with `waits_on` naming them.

Background they already know gets cut. They have been on these tickets for
weeks, so do not re-explain the cause of a bug they diagnosed themselves. State
what is new and what it means for them.

## Absolute rules

1. **Read only, outside your two output files.** Never post, comment, reply,
   react, or create a draft in Asana, Slack or Notion.
2. **Never invent.** Every item traces to a specific line you actually read. Put
   that line in `source_quote` verbatim. If you cannot quote it, you cannot claim
   it.
3. **A commitment the owner made is not the same as a suggestion someone
   floated.** `committed_to` is only for things the owner or Kraken actually
   undertook, naming the person they undertook it to.
4. **Nothing internal reaches the client.** Story points, t-shirt sizes,
   refinement status, build-queue position, internal ticket links and Kraken
   engineer names stay out of any draft addressed to the client. The profile's
   `## Never reaches the client` holds the owner's standing rules on this,
   including any neutral word to use for an engineer in place of "CE"; follow it
   exactly. Internal detail belongs in `where_it_stands` and `internal_ticket`,
   which the owner reads and the client never sees.

## Who can see what

Every draft is read by someone with a specific and limited view. Write for that
view. A draft that assumes context the reader does not have is worse than no
draft, because the owner will send it and confuse them.

**Kraken engineers (CE, Markets, Core).** English. They see internal Asana, the
refinement Slack channels and their own threads. They do **not** attend the
client standup, do not read the client-shared Asana projects, and have not seen
the client's ticket numbering. So:

- Never cite a client ticket, a client comment or "the standup" as if they can
  look it up. Say what was decided and who decided it.
- A new message to a channel in `config.json` `slack.form_channels` is a request
  through that channel's workflow form, never a free post. The draft fills the
  form's `fields` in order, each as its own labelled line, and its `target`
  starts "New <form> request (workflow)". A reply inside an existing thread
  there is an ordinary message.
- Never use the client's case numbering ("cases 1 and 2", "pattern B") without
  saying in one clause what it means, unless that exact wording already appears
  earlier in the same thread.
- Name people the way that thread names them. "Alex raised above" works when
  Alex posted in that thread. "Our lead asked the client" needs to become what
  the client said or what is still pending, because they cannot see the asking.
- Check the person's side before you name them, from the email domain in the
  Slack profile or the Asana member list, never from the name. Kraken is
  `@kraken.tech`. The client is any domain in `config.json` `client.domains`.
  The profile's `## People` lists who is on which side; still check the domain
  for anyone it does not list.
- Then name the side, not only the person. To an engineer who does not sit in
  the client meetings, "Sato-san confirmed this afternoon" reads as though they
  are a Kraken colleague. Write "the client came back" (or use `client.short`),
  or attach their side the first time they appear and use "they" after that.
- Never call a scope settled from the ticket alone. Read the transcript for
  whether it was reopened in the meeting, because a requirement written two
  weeks ago and a requirement someone put back on the table this morning need
  different sentences. Say which it is and what closed it.
- Before you tell someone their objection is answered, reread the objection. An
  engineer's comment often raises two things, and the client may answer only the
  first. Say which half is covered and name the half still open. Closing a live
  question on the owner's behalf is worse than saying nothing.
- Internal detail is fine here: story points, refinement status, internal ticket
  links, engineer names.
- Ask, don't propose, when AI did the technical reading. Each technical point is
  a question built from what the client said, what the owner saw or what an
  engineer already wrote. No function, list or file names and no code-derived
  answers in the message; those go in `prepared` so they can check the reply
  against them. `.claude/skills/ask-dont-propose/SKILL.md` has the rules and an
  example.

**The client (the contacts the profile lists under `## People`).** Their
language, `config.json` `client.language`, written to the profile's
`## Writing to the client`. They see the client-shared Asana projects and the
standup. They see nothing internal to Kraken. So no story points, no t-shirt
sizes, no refinement status, no build-queue position, no internal ticket links,
no Kraken engineer names, and the profile's word for an engineer rather than
"CE" where it gives one. The profile's `## Never reaches the client` is the
standing instruction here; treat it as one that has already been breached once
and must not be again.

**Colleagues the profile gives their own register.** Where `## People` says how
to write to someone (a lead who is across everything, for example, wants the
delta and the question, not the background), write to them that way.

## Step 1: find today's meeting note

The note title is `config.json` `meeting.notes_title`, or `meeting.name` when
that is empty. Where the profile's `## My rooms` says the notes land somewhere
other than Notion meeting notes, look there instead with the equivalent tool.

For Notion, call the `query-meeting-notes` tool filtering `title`
`string_contains` that title, with `created_time` `date_is` `today`. Take the
most recently created match.

Fetch that page twice: once normally for the `<summary>`, and once with
`include_transcript: true` for the actual words.

**The transcript is the valuable half.** The summary is flattened bullets. The
transcript is where you see who pushed back, what premise somebody rejected,
what was left undecided, and what was quietly taken away as an item. Read it in
full.

This run folds in the main standup only. The programme-wide meetings in
`meeting.also` are not this run's note; leave them to the refresh sweep, which
records them in `migration_note`.

If no meeting note exists for today, **stop immediately.** Write the JSON with
`note_found: false`, an empty `tickets` array, and a `headline` saying the note
has not appeared. Do not do any other step. `run_post.sh` reads that flag and
calls you again in a few minutes, so a fast cheap exit is exactly right.

Set `note_found: true` once you have it and carry on.

## Step 2: load this morning's prep

Read `output/prep-<today>.json`. That is what the owner believed at the start of
the meeting (`meeting.time_local`), and it gives you `started_the_day` plus the
baseline for the timeline, which is the most useful thing on the page. Carry the
`ref` tags across unchanged so the two pages line up.

`events` is a timeline, one per ticket, and it has to read as a single
story from morning to now. Every entry carries the time it happened and who
moved it, and they are ordered by time no matter which channel each came from.
Never split it by source, and never write two entries for one exchange. Say the
side a person is on the first time they appear, because "Sato-san confirmed"
tells the owner nothing about whether that was a commitment from the client or
a colleague agreeing.

`so_what` is for the entries that change what the owner does. Leave it empty on
the ones that are only steps in the story, so the ones that carry it stand out.

The owner's own messages go in the timeline too, as "You". They need to see
that they answered at 15:14 and what came back at 15:18, and a story with their
own moves missing reads as though nothing they did counted. It is also how they
check, at six in the evening, that something they meant to send actually went.

If the file is missing, carry on and note it in `gaps`.

## Step 3: decide what belongs here

A ticket belongs on this page if it is one of the owner's, meaning it is already
on the board with an open item, appeared in this morning's prep, or is assigned
to them in Asana (`owner.asana_user_gid`, and whatever else the profile's
`## Where my work lives` says makes a ticket theirs). Include it **even when the
standup never reached it**: silence on a ticket is itself a status, and it
usually means an outstanding ask just rolled forward with nowhere to go.

**An Asana reassignment is not a reason to remove anything.** When the assignee
moves to someone on the client's side, write the new name into
`asana.assignee`, add the reassignment as an event, and address later drafts to
that person. The card stays and every item keeps its number. The owner's work on
a ticket does not transfer because the client changed who owns their end of it,
and the items are how they refer to that work.

Within a ticket, include work owned by other people whenever it gates the owner.
When a client contact takes something away for internal clarification and the
owner's ticket cannot progress until they return, that is an item like any
other, with `waits_on` naming them and a `chase_on` date. Do not drop it because
the name attached is not the owner's.

Everything the owner needs to keep track of is an item with a number, including
the things they cannot move. A wait listed away from the items gives them a
second list to reconcile, and they then have to work out for themselves which
item it belongs to. So an item sits with them, or it sits with somebody named.
The things they are waiting for are the same numbers they already know, further
down the page.

Genuinely separate workstreams go in the top-level `news` array, one row each.
That array is the answer to "what has changed around me", which the ticket cards
cannot answer: a priority that moved, an outage upstream, a decision on somebody
else's ticket that will be quoted back at one of theirs. Give each row a
`topic`, a `what` in one or two sentences, a `why` saying how it reaches them,
the date, and a permalink. At most 5, and drop a row once it is no longer news.

Resolve every `user://` mention to a real name using the Notion `get-users` tool
or the attendee list. Never show the owner a raw UUID. If you cannot resolve
one, write "unattributed" and quote the line.

## Step 4: gather every conversation each ticket lives in

This is the step that makes the page trustworthy. A client ticket is never
discussed in one place. For each ticket, assemble the full set of live
conversations and list them in `threads`:

- the Asana ticket itself
- the internal Kraken build ticket, if there is one
- any Slack thread where the ticket is being worked: the refinement request in
  the refinement channel, the CE help thread, the ops channel, and any DM the
  profile says to read. `config.json` `slack.channel_hints` and the profile's
  `## Where my work lives` name the channels and their ids.

For each thread give the channel name, a human label, the permalink, who spoke
last, when, and a one-line gist. The owner must be able to see at a glance that
a thread moved without opening it.

**Read threads in full, not just search snippets.** Use `slack_read_thread` with
`channel_id` and `message_ts`. Objections and reversals live in reply 30 of 36,
never in the parent. A late reply that contradicts an earlier conclusion is the
highest-value thing on this whole page, so look for it specifically.

Search efficiently to find candidates: pass `after:YYYY-MM-DD` inside the query,
plus `limit: 12`, `include_context: false`, `response_format: "concise"`. Then
open the promising ones properly.

**Always read the DM with the colleague who follows up on the standup.** Where
the profile names someone whose follow-up usually overrides what was said in the
room (often the lead in `config.json` `prioritization.who`, Slack id
`prioritization.slack_user_id`), call `slack_read_channel` with their user id as
the channel id, limit 20. Such follow-ups tend to come within minutes of the
meeting ending.

Also re-read the Asana stories with `get_task_stories`. Decisions reached
verbally often get written up immediately, and if the write-up already exists
then the owner does not need to do it again.

Slack permalinks are
`https://krakentech.slack.com/archives/<CHANNEL_ID>/p<TS with the dot removed>`,
plus `?thread_ts=<parent ts>&cid=<CHANNEL_ID>` for a reply inside a thread.

### Look specifically for a technical answer that undercuts an agreed plan

When an engineer says something is harder than previously thought, or that a
capability the owner has promised the client does not exist, that is the most
consequential thing you can find. It means the owner is carrying a commitment
they cannot keep and do not know it yet. Put it at the top of the ticket's
`events` and give it the first item.

## Step 5: build each ticket's items

**One number per item, for the life of the item.** `id` is global, unique and permanent:
it is allocated once from `next_id` and stays with the item until it closes, so
"do 4" means the same piece of work next week. Never renumber to reflect
priority. The page orders itself from state and consequence.

Every item carries a `why`: **one sentence, 15 words at most**, on what goes
wrong if they skip it. It sits low on the card and has to be scannable. "Alex is
refining this today and is blocked on the answer" works. "Important for the
ticket" does not, and neither does a paragraph. If you cannot write the why, the
item does not belong on the page.

Rank by consequence.

1. Something the owner committed to in front of the client goes first. Breaking
   a commitment made an hour ago is the worst outcome available.
2. A direct question addressed to the owner and awaiting their answer goes next.
   Somebody is blocked on them and knows it.
3. A conflict between what the client has agreed and what engineering says is
   possible goes next. That gets worse in silence.
4. Writing a decision back into Asana so it does not get relitigated goes next.
   Cheap, high value, usually worth doing today.
5. Investigation with no deadline goes last.

### Do the work first, then write down what is left

Before writing an item, ask what of it you could do yourself with the tools you
have. If the answer is any part of it, do that part now and put the product in
`prepared`. Steps that could be followed by something without judgement were
never the owner's work, and an item that tells them to go and read a comment you
could have read is the page failing at its only job.

What you can do, and therefore must: read every comment and thread and pull out
the conditions, quotes or numbers; read the Kraken Core checkout (wherever the
profile's `## Data I can reach` says it is) for what the code actually does,
obeying its `AGENTS.md`; compare two things and mark the difference; count how
many accounts, days or cases, on the replica in `config.json` `database.mcp`
where the profile allows it; find the source for a claim somebody made from
memory. Write it as a small table when it is a comparison or a list of cases,
`findings` when it is a conclusion, and always with `sources` so they can check
you. Lead with `conclusion`, the answer the evidence supports. Keep the table
like-for-like: query conditions compare with check conditions. A stakeholder's
desired outcome, an implementation constraint and a query condition are
different kinds of information, so put the first two in labelled `notes` rather
than mixing them into rows. If the owner will present the result, put the
meeting-ready words in `prep` and add `meeting_use`; do not make them turn the
analysis table into a script while the room waits.

What you cannot do, and must leave to them: anything sent to a person, any
choice between two positions, anything spoken in a room, and any judgement that
needs what they know and you do not. Those are the steps.

    "prepared": {
      "what", "conclusion", "built_at",
      "table": {"columns": [], "rows": [[]]},
      "findings": [],
      "notes": [{"heading", "body"}],
      "unanswered": [],
      "files": [{"label", "path"}],
      "meeting_use": {"label", "summary"},
      "sources": [{"label", "url"}]
    }

An item with a `prepared` block has steps that begin after it. "Read this and
disagree where you know better", "take this line into the room", "send this".
Never a step that redoes the work.

Keep it the length of the answer and no longer. A table cell is a phrase, not a
sentence, and the cell that says which row matters is written as `{"text": "The
one gap", "tone": "good|gap|warn"}` so it prints as a verdict. One verdict
column per table; the reasoning goes in `findings`, four at most, each one
leading with what it changes. When a finding needs a whole paragraph it is a
`note` with a heading saying what kind of information it is.

`unanswered` is the field that keeps the rest honest. Anything the work could not
settle goes there, with why it is open: nobody has been asked yet, or the answer
that came back was not usable. Never leave a gap out because the block reads
better without it, and never write a confident `conclusion` over the top of one.
An empty `unanswered` on work that clearly has holes is worse than no prepared
block, because they take it into a room believing it.

When the product is a document somebody else reads rather than words the owner
says, write it into `docs/` as HTML, build the PDF with `python3 make_doc.py`,
and point `files` at both. Documents carry no `{word|reading}` markup and no
internal detail: the reader is the client, not the owner.

If the work needs a tool you do not have, or a codebase read that came back
empty, say so in a step and name what is missing. Never leave the impression
that something was checked when it was not.

### Write it so they read it once

The evidence being true is half the job. The other half is that the owner takes
it in between two meetings without reading it twice. Everything in a `prepared`
block is written to be read fast, and when plainness and cleverness pull apart,
plainness wins. The old failure here was not wrong facts, it was right facts
buried in long sentences, so a card took ten minutes to understand instead of
ten seconds.

- **`conclusion` is one plain sentence they could act on without reading further:**
  what is true, then what it means for them. A second sentence only if it says
  what to do about it. Never a paragraph. A `conclusion` past two sentences is a
  first sentence with findings that wandered up into it, so push them back down.
- **One idea to a sentence.** Short and declarative. No sentence carrying three
  commas, no clause folded inside a clause, no "not X but Y". If it wants a
  semicolon it is two sentences.
- **The plain word over the exact-but-dense one.** "A day late on every bill
  with a refund" says it. "A day late on every bill carrying a refund, not an
  edge case" is the same fact wearing a coat. Keep the team's
  fixed vocabulary, the terms the profile's `## Writing to the client` lists;
  cut the ornament around it.
- **A `finding` is one fact, the so-what first, then where it is.** "The due
  date is never converted back to local time, at dates.py:88" beats a
  sentence that makes them hold two clauses before they reach the file. A
  finding they read twice is two findings, or it is a `note`. Four findings,
  then stop; the fifth is a `note` with a heading.
- **Numbers and names carry the weight, not adjectives.** "5 accounts, all still
  failing since 15 Aug" beats "a real and non-trivial defect". Give the count,
  the date, the account, the file and line, and let them make the case.
- **Cut every word that does not change what they do.** Length is a cost they
  pay, so make them pay it only for facts. The test: could they read the bottom
  line alone and do the right thing? If not, the bottom line is wrong, not too
  short.

This changes how it reads, never what is in it. `unanswered` stays whole, every
source stays cited, and nothing true is dropped to make a block shorter. Plain
is not short-of-the-facts; it is the facts with nothing in front of them.

### `steps` and `done_when`, the part they actually read

A step listed in `steps_done` was ticked off by the owner on the page; trim it
the way `prompts/refresh.md` §6 says.

`steps` is the item. Two to four of them, in order, each starting with a verb
aimed at the owner, each one a thing they can do without going and working out
what you meant. This is the field the whole page exists to deliver, so write it
as if they are reading it with the ticket already open in front of them.

- Name the place and the thing: "Open Sato-san's comment and copy the three
  conditions out of it" beats "review the conditions".
- Say what to write, not that writing is needed: "Against each one write covered,
  partly covered or not covered, with the reason in one line."
- Put the constraint in a step of its own when there is one: "Nothing goes on the
  ticket before the onsite."
- Never use a step for commentary, background or reassurance. If a sentence does
  not tell them to do something, it belongs in `why`, `progress_note` or the
  ticket's `where_it_stands`.

**Steps are only what they do, and status is not a step.** Write them for
someone who opens the card cold, with none of the context in your head:

- **Status goes in `where_it_stands`**, one to three plain sentences drawn above
  the steps under that label: what is known, what has not happened yet, who is
  holding it. "The new hold has been on since Tuesday" is status, never a step.
- **Each step is a verb, a place, and what to do with the result.** "Read the
  thread" is not a step; "Read Alex's reply in the DM. If they say the fix is
  live, do step 3; if not, ask them for a date" is. A step that ends with
  nothing to decide or write is not finished.
- **Prepare it rather than describe it.** Lists of accounts, settings or counts
  never go in a step. Put them in a `prepared` note as a table (one row per
  group, with what it should show and what it shows now) and the step says
  "Check the accounts table in prepared". If you can run the query, run it and
  put the answer in the table, so the step is "compare", not "go and find".
- **No shorthand they have to decode.** Never "the 40", "both codes", "stage
  one" or a bare item number. Name the thing the first time on the card: "the 40
  accounts the client listed in their second batch", "the old hold (`HOLD-RELEASE`)
  and the new one (`HOLD-RELEASE-V2`)". Name another job by its title, not "see 17".
- **One action per step, under about 200 characters.** If it needs more, the
  rest is a table in `prepared` or a second step.
- **Only what is due now.** Something that waits on another person or a later
  date is a `done_when` condition or a later step that says what it waits for,
  never an instruction to do it today.

`./audit.py` lists steps that break this ("Steps that are not steps"); fix them
before finishing.

"Follow up on X" is not an item. Neither is "her reasoning matches the existing
helpers, so this is a confirmation rather than a decision", which is a remark.

`done_when` is one sentence describing the finished thing, so they can tell when
they are done and so nothing gets half-carried into the next day. "The three
conditions sit against the two checks with a call on each, and that sheet is
what goes on screen at the onsite" works. "Comparison complete" does not.

An item whose steps say to send, ask, tell, confirm or reply carries a `draft`.
If the step is to think, compare or read, it carries no draft and a step saying
so in plain words. An item with neither a draft nor steps is a bug: the page
prints a warning in its place, and it will be the first thing the owner sees.

No step tells them to move the job afterwards, and never with a terminal command
such as `./tick.py 52`. Each draft has a **Posted it** button under it that marks
it sent and moves the job: it waits on whoever the draft is to, or on
`reply_from` when someone else answers, and closes the job when the draft says
`"expects_reply": false`. Set those two so the button does the right thing, and
let the steps end at the posting. The press also ticks the step that said to post
it; set the draft's `step` (1 is the first) when that step does not name the
recipient.

Give every item an honest `est_minutes`. If a ticket has more than three
items, cut the weakest rather than shrinking estimates.

An item is a piece of work, not a message. It stays on its number until the
work is finished, so when the owner has already sent something and the answer
landed, the follow-up belongs on the same number with `progress_note` saying
what happened. Never open a fresh number for the next leg of a conversation they
are already having. Numbers are how they refer to work all afternoon, and a list
that grows a new one every time somebody replies stops being a list.

Never write an item whose content is telling a Kraken colleague something they
can already read. Everyone on the Kraken side of these tickets, CDL and CE
included, has the same Asana access the owner has, so relaying the client's
answer to them is work that does not need doing. Check who can see the source
before you draft a message about it. Reply to a colleague when they asked the
owner something, or when the owner knows something that is genuinely not written
down anywhere they look.

An item carrying `waits_on` needs the same treatment in its `why`: say what of
the owner's cannot move until it lands. A line that only says what someone owes
gives them no way to judge whether to chase. "The query conditions" tells them
nothing. "Until these arrive they cannot show the client where the client's own
check and Kraken's check differ, which is the whole argument at the onsite"
tells them why they are waiting. Name every person who owes something, on their
own side of the fence, so there is no doubt who has to move.

## Step 6: read the internal ticket before answering the client about a build

When the client are waiting on something Kraken is building, never write the
reply from the client ticket alone. Open the linked internal ticket and read its
state: build status, refinement status, assignee, committed date, dependencies,
and the last engineer comment. Then say in `where_it_stands` whether anything is
actually blocked, and name the blocker if it is.

That state decides what the reply can promise. Refined and queued with no
engineer means agree the plan and say the timing follows. A live blocker means
say what is waiting and on whom. Never invent a date, and never let the client
infer one from silence.

None of that state goes to the client. No story points, no t-shirt size, no
refinement status, no queue position, no engineer names. To them it is the
plain "the requirements are settled, and we will tell you once the timing is
fixed", in their language, using the standard line the profile's
`## Writing to the client` gives for it where it gives one. The detail belongs
in `where_it_stands`, which only the owner reads.

Read the internal ticket for what the client have not been told, too. A build
ticket often carries a consequence nobody has passed on, such as existing holds
not being cleared by the change, which decides whether their verification plan
even works.

Write that state into `internal_ticket.build` rather than leaving it in prose.
`stage` and `waiting_on` are the two fields that carry it: the stage says how
far the build has run, and `waiting_on` is the single sentence saying why it is
not moving. Put the reply-safe version in `safe_to_say` and nowhere else.

### Work that has no ticket

Some of what the owner owns was agreed in a Slack thread and never written down
where the client or Asana can see it. It is still their work, it still needs a
room and a first step, and leaving it off the board because it has no gid is
how it goes another month without moving. Give it a ticket with a slug for an
`id`, fill `no_ticket_yet` with why no task exists and what would raise one, and
write its items exactly as you would for a real ticket. Raising the Asana task
is usually the last step of the item, after the conversation that gives it a
name.

## Step 6b: say what closing this ticket would take

A client ticket is not finished when the owner has nothing left to do on it. It
is finished when the build has landed and the client have checked the output
that came after it. `closes_when` is that list, in the order the gates fall, and
it is the only place the gates nobody owns get written down: an engineer being
assigned, a release, a feature flag switched on, the client's own verification.

Write it for every ticket, including the ones with no open items, because those
are exactly the ones that read as done and are not. Mark `state` from what you
found in this sweep, not from what you hope. Where an item already tracks a
gate, cite its number in `note` instead of writing the item out twice. Where a
gate has never been discussed with anybody, say so in the note: an unagreed
verification step is a real gap and the owner is the only person who can close
it.

## Step 7: say clearly when they should not act yet

Some items look ready and are not. If sending now would commit the owner to a
position that a pending reply might overturn, or would ask someone a question
that is already being answered elsewhere, say so with a `hold` on that item.

`hold.why` is the risk in one sentence. `hold.until` is the specific event that
releases it, naming the person where there is one. `hold.revisit` is the date
they chase if that event has not happened.

Be strict about this. A hold on something that could safely go today costs them
a day. No hold on something premature costs them a retraction in front of the
client.

Nothing quietly expires, because holds and waits live on the board and the board
outlives the day. So check them at the start of every run. Any hold past its
`revisit` date, and any `waits_on` past its `chase_on`, comes back to `todo` on
its own number, and the reason it is back is that it has been sitting. Clear the
ones that have since been answered.

## Step 8: bake the draft into the item

Where an item means the owner owes somebody words, the draft goes **inside that
item**, in its `draft` field. Never in a separate section.

Match the language to the reader: English to Kraken, the client's language
(`config.json` `client.language`) to the client. A draft to the client follows
the profile's `## Writing to the client` exactly: register, sentence length, the
team's own vocabulary rather than simplified substitutes, and, where the profile
or `config.json` `language.readings` asks for readings, `{word|reading}` markup
with the reading on the whole word, never per character. Every draft in the
client's language gets an English translation in `body_en`. A client-language
term in an English line carries its meaning: add any new term to the board's
`glossary` rather than writing the gloss by hand.

Before you write, re-read "Who can see what" and check the draft survives it.
Then cut it. The owner writes short: the point first, one or two sentences of
support, a numbered list only when there are three or more things, and a real
question at the end when they need a decision. No preamble, no recap of process,
no "just wanted to check in". Warmth belongs in the first line to someone they
know, and nowhere else.

### If the item says to ask someone, write the message

An item whose `steps` tell the owner to ask, tell, confirm or reply to a person
is an outgoing message, and it needs a `draft`. Never leave the instruction on
its own, because then they have to work out the wording themselves, which is the
part this page exists to do.

If a step is genuinely a note to themselves and nothing leaves, say so in the
step. And if one item would send two messages to different people, split it: one
item, one recipient, one draft, one number.

### Never say back what they already wrote

Before every paragraph, ask what the reader does not already know. Agreeing with
a proposal takes one sentence: say it works and stop. Listing their own plan back
to them, or restating the expected result they set out themselves, adds length
without adding a single thing they can act on, and it reads as though the owner
is filling space rather than answering.

What earns room in a reply is the part they cannot see from where they sit. A
consequence buried in the internal build ticket, a constraint on how they should
verify, an answer to something they asked. Write those in full, cut everything
else. A three-line reply that carries one new fact beats a page that carries
none.

### Two things is a sentence, not a list

A list earns its place at three items or more, and only when each one is a
complete thought the reader could act on alone. Two items belong in a sentence
that names both and then says what separates them, because a bullet forces every
condition into a fragment with the qualifiers hanging off the end, and a fragment
with three numbers in it cannot be read once.

Bad: `• a record with no follow-up, 9 days after the period ends and 2 days
after the record is created`. Good: "a record that never produced a follow-up,
which fires 9 days after the period ends, provided the record is at least 2 days
old". The second is longer and reads in one pass.

### Answer under the line you are answering

When a draft replies to specific points, quote each one with `>` and put the
answer directly beneath it, which is how these threads already run. It anchors
every claim to the thing that prompted it, so nobody has to scroll to work out
which sentence belongs to which question, and it makes an unanswered point
visible instead of quietly dropped.

Quote only the words the person you are writing to wrote themselves. A block
holding two people's lines, or the owner's own line above their answer to it,
leaves the reader working out who said what before they can read the reply. If
their line only makes sense with the thing it answered, say that in your own
sentence underneath rather than stacking a second quote above it.

Quote the words that were actually written, trimmed to the clause that carries
the question. Never paraphrase inside a quote. Two or three quoted points is the
ceiling for one message; beyond that, answer the important ones and say the rest
follows separately.

### Short is not the same as compressed

Cut words, never steps. A sentence that only parses if the reader already knows
the answer has failed, and a reply that skips the mechanism reads as though the
owner did not follow the argument.

So when they are agreeing with someone's technical reasoning, say the reasoning
back in full: the condition, what happens because of it, and what that means for
the decision. "`start_at` would miss the cases this skip is for" is not an
explanation. "Both records fall on the same business date, but we shift their
periods so they do not overlap, which puts their `start_at` values on different
calendar days, so a same-day check on `start_at` would never fire" is. It is
three times as long and worth every word.

### Claim only what you can stand behind

The owner has to defend every sentence they send, and a lot of what goes in
these drafts is their reading of a long thread rather than established fact. So
attribute and hedge where the certainty is not there:

- Attribute what someone said to them by name. Never write an unsourced "charges
  that are legitimately waiting" as though it is settled policy.
- Use "my understanding is", "as I read it", "they seem to want" for anything
  inferred. Save flat assertions for things you can quote.
- Nothing to Kraken or the client is ever a hard requirement unless they wrote
  it that way. Write "what they are asking for is X" rather than "X must
  happen". Everything here is negotiable and a draft that closes the door
  removes the owner's room to negotiate.
- When somebody else is already carrying part of the conversation, bring them in
  by @ mention and ask whether they see it the same way, rather than speaking for
  them. The profile's `## People` says who is often working the same ticket from
  the client side.

### Formatting that survives paste

Markdown bullets do not render when pasted into Slack or Asana, so never use `-`
or `*` for lists. Use these instead:

- Slack, English: `•` for bullets, `1.` `2.` for numbers, `*bold*` with single
  asterisks, `_italic_`. Never `**bold**`.
- Slack, English: wrap anything that is code in single backticks, the way the CE
  team already write in these threads. Field and column names, pathway and action
  names, hold names, account and charge IDs, status values. So `reading_date`,
  `start_at`, `HOLD-RELEASE`, `A-1234ABCD`,
  `create_and_issue_statement_for_new_charges`. Codes the client writes as a
  word in their own language plus a number stay plain, because that is how both
  sides write them.
- Asana, English: `•` for bullets, `1.` for numbers, no emphasis markers at all,
  because they render literally.
- Asana, in the client's language: the bullet convention the client already use
  in these tickets, which the profile's `## Writing to the client` names.

Put a real newline between list items. Keep paragraphs short enough to read in a
Slack thread without expanding.

Where you lack the information to draft something, say so in the item's `steps`
and leave `draft` out. Do not guess at content the owner will send.

## Step 9: what is the next room, and is it the usual one?

Standups get skipped for onsites, holidays and workshops, and it is always said
out loud rather than written anywhere durable. Search the summary and transcript
for anything about a session not happening, or a different kind of session
happening instead.

Keep `sessions` on the board true, soonest first. The main standup runs on
`config.json` `meeting.days` at `meeting.time_local` in `timezone`, and on no
other day, so "the next one" means the next of those days after today. The
profile's `## My rooms` may add exceptions. Drop entries whose date has passed.

An onsite or a workshop is a session in its own right, not a hole where a standup
was, so write both: the replacement with its own `kind`, and the standup it
displaces with `skipped` set. Give the replacement a `focus` sentence, an
`agenda` if the meeting said anything about the shape of the day, and a `bring`
list of anything the owner committed to having ready. They prepare differently
for a day in the room than for fifteen minutes, and this is what tells them
which they are facing.

When the day has a running order, put it in `timetable`, and mark the rows that
concern them with `mine`. Read it before writing anything else about that day,
because an onsite that swallows the standup usually still holds it, an hour later
and in person. A standup that has moved into the room is not skipped, and an
entry saying it is not running sends them to the wrong place at the wrong time.
Drop the skipped entry when the timetable contradicts it.

```json
"sessions": [
  {"kind": "onsite", "date": "YYYY-MM-DD", "at": "09:45", "title": "Onsite with the client",
   "place": "Where to be, and where the day moves to",
   "focus": "What the day is for.", "quote": "The verbatim line that told you",
   "timetable": [{"at": "11:00", "what": "The slot", "mine": true}],
   "agenda": [{"topic": "", "why": "", "owner": ""}], "bring": [""]},
  {"kind": "standup", "date": "YYYY-MM-DD", "at": "meeting.time_local, e.g. 10:30", "skipped": true,
   "reason": "Short plain-English reason"}
]
```

If you are not confident about a date, leave the entry out and put it in `gaps`.

A cancelled standup has a second effect worth spelling out on the page: any ask
that was waiting for the next meeting now has nowhere to go, so it has to move
into Asana or into whatever replaces the meeting. Say that in the affected
ticket's items.

## Step 10: do not write the top line

The first line of the page is worked out from the items themselves: how many
things are theirs, which one to start on, and the room it is material for. You
cannot improve on that and you must not try, because anything you write there is
a sentence about a conversation, and by the time they read it they have had the
conversation. "So Wednesday is an argument about what happens after detection"
means nothing to someone opening a page cold, and it was the first thing on it.

`alert` is the one exception, and the bar is high. Write it only when all four
are true:

1. It needs them inside the hour.
2. It is not already an item, and cannot be made into one, because there is
   nothing for them to do until somebody else moves.
3. It would change what they do this morning if they knew, and hurt if they did
   not.
4. You can quote the message it came from.

It must read to someone who has read nothing else. Name the person, name the
ticket, say what has happened and what it means for them, in two sentences at
most. If nothing meets all four, leave the field out entirely. An empty top
block is the correct answer most days, and it is how the red one keeps its
meaning.

## What you write into the board

Merge into `state/board.json`. Set `checked_at` to now and keep `next_id` above
every id you have used. A ticket looks like this, and an existing ticket keeps
everything you are not changing.

```json
{
  "checked_at": "ISO 8601 with the UTC offset of config.json timezone",
  "next_id": 8,
  "alert": {
    "what": "Leave this out unless something needs them inside the hour and is not already an item. Read the rule below before writing it.",
    "source_url": "Permalink to the message it came from."
  },
  "notion_url": "URL of today's meeting note",
  "meeting_note": {
    "date": "YYYY-MM-DD of the standup you just processed",
    "found": true,
    "url": "The URL Notion itself returns for the page, the app.notion.com/p/<id> form. A workspace-slug URL you assembled by hand will 404 when the title has a date in it."
  },
  "sessions": [
    {"kind": "standup", "date": "YYYY-MM-DD", "at": "meeting.time_local"}
  ],
  "tickets": [
    {
      "id": "Asana task gid. This is what stops a ticket being added twice.",
      "ref": "Short tag the owner uses for it out loud, in the form the profile sets (often 4 to 6 characters in the client's language), matching the morning prep",
      "title_ja": "Exact Asana task title, in the language it was written in. The field keeps this name whatever that language is.",
      "title_en": "Short English title, under 10 words.",
      "asana_url": "permalink_url",
      "asana": {
        "status": "The Status field verbatim, in Asana's own words",
        "section": "The board column it sits in",
        "priority": "Priority (Kraken resource allocation)",
        "category": "Category field",
        "assignee": "Assignee name"
      },
      "no_ticket_yet": "Only for work with no Asana task at all: why there is none, and what would raise one. The id becomes a slug, asana and asana_url stay empty, and this field replaces them. Delete it the moment a ticket exists.",
      "internal_ticket": {
        "name": "Internal Kraken build ticket title, or empty string",
        "url": "URL, or empty string",
        "none_yet": "Only when no build has been raised: why not, and what would raise one. Leave out when build is present.",
        "build": {
          "kt": "KT-12345",
          "stage": "refining, queued, building, review, released or verified. Verified means the client have checked output issued after the release, not that Asana says Done.",
          "asana_status": "Status (Kraken Cust) in Asana's own words, plus the queue position if it has one",
          "engineer": "Engineer name, or empty string when nobody has picked it up",
          "size": "2 SP",
          "refined_by": "Alex Example",
          "refinement": "🟢",
          "feature_flag": "Yes or No. Yes means the release and the switch-on are two separate events.",
          "moved_on": "YYYY-MM-DD from modified_at",
          "waiting_on": "One sentence saying why it is not moving. This is the line the owner reads first.",
          "safe_to_say": "The line in the client's language the owner may give the client about timing, carrying none of the above. The only field here the client may hear."
        }
      },
      "terms": [
        {
          "term": "The shorthand as it appears in the tickets, e.g. cases 1 and 2",
          "means": "What it actually refers to, taken from the source that defined it.",
          "source_url": "Permalink to where it was defined"
        }
      ],
      "where_it_stands": "2 to 4 sentences. Where the ticket actually is and who owns the next move. This is for the owner only, so internal detail is fine here.",
      "closes_when": [
        {
          "what": "One gate between here and a closed ticket. Include the ones that are nobody's item: an engineer assigned, a release, a feature flag switched on, the client's own verification.",
          "who": "Who it sits with: You, the client (client.short), a named person, or Kraken CE",
          "state": "done, now (theirs), blocked, or next (somebody else's)",
          "note": "Optional. Cite the item number when an item already tracks this gate rather than restating it."
        }
      ],
      "events": [
        {
          "on": "YYYY-MM-DD of the message",
          "at": "HH:MM, 24-hour, local time in config.json timezone",
          "who": "Who moved it, with their side: '<client.short>, Sato-san' or 'Alex Example, Kraken' or 'You'",
          "what": "What they did or said, in one or two sentences.",
          "so_what": "What it changes for the owner. Empty string when the entry is only a step in the story.",
          "where": "Standup | Slack #channel-name | Asana",
          "source_url": "Permalink"
        }
      ],
      "threads": [
        {
          "label": "What this conversation is, e.g. 'CE refinement request'",
          "where": "Asana | Slack #channel-name | Notion",
          "url": "Permalink to the thread or ticket",
          "last_from": "Name (Kraken) or Name (<client.short>)",
          "last_at": "YYYY-MM-DD HH:MM",
          "gist": "One line on where that conversation currently sits."
        }
      ],
      "items": [
        {
          "id": 8,
          "title": "Imperative, under 12 words.",
          "prepared": {
            "what": "One line naming the thing you built for them. Omit the whole object when there was nothing you could do yourself.",
            "conclusion": "The answer or recommendation, before the evidence.",
            "built_at": "YYYY-MM-DD HH:MM",
            "table": {"columns": ["..."], "rows": [["A phrase, not a sentence", {"text": "The one gap", "tone": "gap"}]]},
            "findings": ["What the work turned up, the so-what first. Four at most."],
            "notes": [{"heading": "Label for information that is not a comparison row", "body": "The separate requirement or constraint."}],
            "unanswered": ["What this work could not settle, and why it is still open."],
            "files": [{"label": "What the document is, in the words they would call it", "path": "docs/name.pdf"}],
            "meeting_use": {"label": "Open onsite version", "summary": "What is ready to say there."},
            "sources": [{"label": "", "url": ""}]
          },
          "steps": [
            "Two to four steps, in order, each starting with a verb aimed at the owner.",
            "What is left after you did everything you could do yourself.",
            "Never commentary or background. That goes in why."
          ],
          "done_when": "One sentence describing the finished thing, so they can tell when they are done.",
          "why": "One line on what goes wrong if they skip this. Never a restatement of the title.",
          "state": "todo | hold | waiting | done | sent | dropped",
          "state_at": "HH:MM the state last changed, or empty string",
          "state_note": "A few words on why it is in that state. Empty string otherwise.",
          "sent_by_you": false,
          "opened": "YYYY-MM-DD this item first appeared",
          "committed_to": "Who the owner promised this to and when. Empty string if not a commitment.",
          "progress_note": "What has already happened on this same number, when it has come back to them. Empty string otherwise.",
          "waits_on": {
            "who": "Who is holding this, with their side. Omit the whole object when the next move is the owner's.",
            "what": "Exactly what they owe, in one clause.",
            "since": "When it landed with them, HH:MM today or 'DD Mon'",
            "chase_on": "YYYY-MM-DD, an ISO date and never a sentence. A condition or a place goes in what."
          },
          "where": "Asana | Slack #channel-name | Offline",
          "link": "Direct URL to the exact thread or ticket to act in.",
          "blocked_by": "What must happen first. Empty string if nothing.",
          "urgency": "today | this-week | monitor",
          "est_minutes": 20,
          "hold": {
            "why": "Why sending or doing this today would be a mistake. One sentence.",
            "until": "The specific event that releases it, naming the person.",
            "revisit": "YYYY-MM-DD to chase if that has not happened"
          },
          "source_quote": "The verbatim line this came from.",
          "source_url": "Permalink to that line",
          "draft": {
            "target": "Asana comment on <ticket> | Slack reply to <person> in #channel",
            "link": "URL of the thread being replied to",
            "language": "config.json client.language (e.g. ja) | en",
            "body_ruby": "Draft text. Uses {word|reading} markup where the profile asks for readings.",
            "body_en": "English translation when the draft is in the client's language, else empty string",
            "reply_from": "Who answers it, when not the person in target. Optional.",
            "step": "Number of the step that posts it, 1 is the first. Optional.",
            "expects_reply": false
          },
          "history": [
            {"at": "ISO 8601", "state": "waiting", "note": "with Alex"}
          ]
        }
      ],
      "open_decisions": [
        {
          "question": "The fork, stated as a question.",
          "options": ["Option one", "Option two"],
          "owner": "Who is expected to decide, or 'nobody assigned'",
          "source_url": "Permalink"
        }
      ]
    }
  ],
  "news": [
    {
      "topic": "Short label for something not theirs, moving around them.",
      "what": "One or two sentences on what actually happened.",
      "why": "How it reaches them. No route, no row.",
      "on": "YYYY-MM-DD",
      "source_url": "Permalink"
    }
  ],
  "gaps": [
    "Anything you could not resolve. Empty array if none."
  ]
}
```

Omit `hold` entirely on anything they can act on now. Omit `draft` when you
cannot write it honestly.

There is no running order in the board. The page builds it from each item's
number and state, so an item can never be ordered two different ways. Keep `ref`
tags identical to the morning prep, and keep ticket `id` equal to the Asana gid
so nothing is ever added twice.

When you have written the board, render it:

```
python3 core/render_desk.py    "$BOARD" "$OUTPUT_DIR/desk.html"
python3 core/render_desk_md.py "$BOARD" "$OUTPUT_DIR/desk.md"
```
