# Bring the board up to date

Go and see what has moved, fold it into `state/board.json`, rebuild the page.
This is the routine behind the word "refresh" in a chat, behind `DANDORI refresh`
in the terminal (the command is `config.json` `desk.command`), and behind the
Refresh button on the page, so all three cost the same.

The block above this prompt, *Who you are working for*, is the owner's settings
and profile. It names the client, the rooms, the people, the house style and the
standing rules. Where it and this prompt disagree about any of those, it wins;
the mechanics below do not change. Below, "the owner" is the person in
`owner.name`, "the client" is `client.name` (short name `client.short`), and
"the profile" is `me/profile.md` as it appears in that block.

Tool names below are indicative. Use whatever equivalents you have for Asana,
Slack and Notion.

## Stop rather than improvise

You need the Asana and Slack tools to do this at all. If they are missing, or
they answer with an authentication error, **stop immediately**, change nothing,
and reply with one line naming what is missing. The desk sees that line and
walks the login itself, so say plainly which of Asana or Slack it was.

Three things are never a workaround, however reasonable they look:

- Calling the Asana, Slack or Notion HTTP APIs yourself, with `curl` or
  anything else. There are no API tokens here, and naming an environment
  variable does not conjure one.
- Starting another agent: not the `Task` tool, not `claude`, not `cursor-agent`,
  not `DANDORI refresh`. You are the refresh. A second one writes the board
  underneath you and can leave it half updated.
- Guessing what a ticket or a thread now says. A source you could not read is a
  source you do not report on.

## The shape of a fast sweep: read once, call in parallel, write once

A sweep is almost all model time, and model time is set by two things: how many
turns you take and how much is in context on each one. So a sweep has exactly
this shape, and a sweep that keeps it finishes in a few minutes:

1. **Turn 1: `./core/digest.py`, once.** It is the whole readable board plus a SWEEP
   PLAN and a WRITE block. Never re-run it, never send it to a file and read it
   back, never open `board.json`.
2. **Turn 2: every independent source call, in one message.** The census, the
   gate, the milestone search, the Slack reads (step S), any client-email check
   (step E), and any unread Gemini or Notion meeting-note checks (steps D, N, R)
   do not depend on each other, so they go out together as parallel tool calls
   in a single message. Then every `get_task`/`get_task_stories`/`gmail_get_message`
   they earn goes out together in the next message. One call per turn is the slow
   shape: each turn re-reads the whole context.
3. **Then write once.** Put every finding of the sweep into one JSON patch with
   the Write tool at `tmp/patch.json`, and apply it with one `./core/apply.py
   tmp/patch.json`. The WRITE block at the end of the digest has every key. If
   apply refuses, it says why and writes nothing: fix that line of the patch and
   run it again.
4. **Then `./core/check.py`, fix what it prints with a second small patch, and report.**

### Delta mode (when triggered by the background scout)

When a note at the end of this prompt says **Delta refresh**, Stage 1
(`./core/scout.py`) has just checked the unfiltered Asana census, the
`modified_at_after` gates, every live Slack thread, your own sends, your
mentions, channel hints, active Slack canvases, client-domain emails, and
post-call Gemini/Notion meeting notes, and wrote only what moved to
`tmp/delta.json`:

1. **Turn 1: `./core/digest.py --delta`, once.** Never run plain `./core/digest.py`
   or `--full` on top of it; `--delta` already prints `DELTA FINDINGS`, `BRIEF`,
   `SCRIPT`, every touched or out-of-date ticket in full, and one-line summaries
   of every unchanged ticket.
2. **Turn 2: fetch only `TO FETCH IN ONE PARALLEL TURN`.** Do **not** re-run the
   Asana census, the Slack thread reads, or the Slack searches already printed
   verbatim in `DELTA FINDINGS`. Only call `get_task_stories`, `get_task`,
   `drive_read_file`, `notion-fetch`, or `gmail_get_message` on the specific
   items listed under `TO FETCH` (plus a `slack_read_thread` only if a new Slack
   search hit links to a thread whose surrounding replies you need). If `TO FETCH`
   is absent because every change was a Slack message already printed in full,
   make zero source calls and write the patch straight away.
3. **Apply every board rule below identically**, write `tmp/patch.json` with
   `"swept": true` (`apply.py` stamps every quiet thread verified by the scout
   automatically), and run `./core/check.py`.
4. **Pre-room speaking sync (when `DELTA FINDINGS` or `SWEEP PLAN` shows `PRE-ROOM SCRIPT SYNC ACTIVE`).**
   When a speaking session is within 90 minutes and today's `script` is already
   built for it, keep `What I say` aligned with `My work` by updating
   `tickets.<ref>.prep` on **every ticket whose state, timeline or asks changed
   in this sweep AND any active cycle/priority ticket listed under `MISSING FROM
   WHAT I SAY` that lacks `prep.script`** (or setting `"prep": null` if a ticket
   is now completed or settled with nothing owed in the room):
   - Follow the profile's `## Writing to the client` and `## Never reaches the client`
     rules: register, spoken flow, exact domain vocabulary, zero internal details
     (no KT numbers, story points, queue position or engineer names), and
     `{word|reading}` on whole words when `config.json` `language.readings` is
     true (`./core/check.py` verifies every spoken line).
   - Keep `prep` to `{order, board_position, gist, issue, why_it_matters, explainer,
     consequences, open_questions, script, client_ask_needed, built_at}` (plus
     `decisions` / `pushback` only for an onsite or weekly review that uses them;
     place `order` in the room's priority order, e.g. `Top Priority -> Priority -> New -> Other`,
     and include `explainer` on newly added tickets).
   - If an open item for a newly added ticket card was temporarily created on
     another ticket before the card existed, re-home it onto its own ticket card
     with `"items": {"<N>": {"ticket": "<ref>"}}` (its item number never changes).
   - Leave other unchanged tickets' `prep` blocks completely untouched. Outside this
     90-minute pre-room window, never touch `prep` or `script`.

Banned outright, because each one is a detour a real sweep has taken:

- **No Python against the board.** No `python3 -c`, no `import board`, no
  `help(board)`, no reading `board.py`, `audit.py` or any other source to learn
  the tools. The WRITE block is the whole of the writing. The `core/board.py`
  docstring is the schema, and you open it at most once and only if stuck.
- **No reading `output/` or any `.py` file for a value.** A record the digest
  clipped (a stale `prepared` block, a draft to rewrite) is one call:
  `./core/digest.py --item 42 52` or `--ticket HOLD-RELEASE`, which prints it whole as
  JSON and is also its shape. Grepping `desk.md` and `board.py` for it cost one
  real sweep a dozen turns.
- **No `git` at all.**
- **No subagents, ever.** Do not use the `Task` tool, do not hand off to a
  subagent, do not spawn `claude` or `cursor-agent`, do not delegate a slice of
  the sweep. You are the whole sweep in one process; a second agent writes the
  board underneath you.
- **No `./core/tick.py --rebuild` from a run.** The page redraws itself from the board
  on every load, and serve.py redraws `desk.md` when the run ends.

## What the board is

The active client's `state/board.json` is the only durable file, and `core/board.py` documents its shape.
It holds every open ticket in the configured Asana projects that is assigned to
the owner, and every item of work on those tickets. Items keep their number until
they are closed, so "do 4" means the same thing next week. Never renumber, never
reuse a number, never rewrite history that has already happened.

**Every write goes through `./core/apply.py`** (or `./core/tick.py` for one item), both of
which save through `board.save()`. Never touch `board.json` with an edit
tool, `sed`, a string replace or `json.dump`. Questions from the page run while
you sweep and write the same file, so `save()` takes a lock and merges what they
wrote with what you wrote, field by field; a write that goes round it overwrites
their answer. Never delete an item: set its `state` to `dropped` with a `note`.
`save()` refuses a board that has lost one.

## 1. Read where things stand

**`./core/digest.py`, once.** Every ticket with where it stands, its open gates, its
last events, and every open item with its first steps, its draft and what it is
queued behind. A ticket with nothing open is one line. `--full` unclips the prose;
a sweep does not need it.

**Everything you read stays in context and is re-read on every later turn**, so
keep source answers small. The gate asks for
`opt_fields=name,modified_at,assignee.name,completed` and nothing else, because
`custom_fields` returns twenty fields per task. Ask for `custom_fields` only on
the census and on the gids you deep-read.

**Work the SWEEP PLAN, do not rebuild it.** It names the census, the gate, the
milestone search, every thread to read with the time to read forward from, and
the build tickets to re-check. Read exactly what it names.

**The `OUT OF DATE` block at the top of the digest is a worklist, not a
warning.** A passed chase date, prepared work older than its ticket, a thread
whose watermark is older than its events: fix every line in this sweep's patch.

Two thread watermarks, never confused:

- `last_at` (with `last_from`) is the **newest message in the thread**. Move it
  only when a genuinely newer message is there. Never stamp it to today to look
  current: that hides real messages.
- `checked` is **when you last looked**. Every thread you open or search goes in
  the patch's `"checked"` key (`{"HOLD-RELEASE": "*"}` for all of a ticket's
  threads, or a list of url fragments), even when nothing was said. A thread with
  a newer message goes in `"threads"` instead, with its `last_at` and
  `last_from`, which stamps `checked` too. A thread read and left unstamped is a
  thread the next sweep reads all over again.

## 2. Sweep every open ticket

Asana first, because the board should match it.

**Two queries, and they answer different questions. Run both, every sweep.**
Confusing them is how a ticket assigned to the owner never reaches the board at
all, which has actually happened: a ticket assigned to them and worked for a day
stayed invisible here because only the incremental gate was run.

1. **The membership census: what is theirs.** A `search_tasks` for **every
   incomplete task assigned to the owner across every project gid in
   `config.json` `asana.projects`, with no `modified_at` filter at all.** This is
   the full list of tickets that belong on the board, and it is not optional and
   not conditional on anything having moved. Compare it against the board's
   tickets by Asana task id: **any assigned, incomplete task not already a ticket
   on the board is added this sweep**, cause named or not. A ticket does not have
   to have moved since the last sweep to be missing: it only has to have been
   created and assigned in a window no earlier gate happened to catch, and then
   it stays invisible forever to a date-filtered query because it never moves
   again. The census is the only thing that catches that, so it runs first and it
   runs unfiltered. **Fetch it with
   `opt_fields=completed,assignee.name,memberships.section.name,custom_fields.name,custom_fields.display_value`**
   so this one call also carries, for every ticket, the fields step 2 writes into
   `asana`: the current Status (the custom field named literally `Status`, read
   from its `display_value`, whatever values the client's project uses; not the
   section, and not any other similarly named field the project carries, which
   the profile names if there is one), the section, the assignee and `completed`.
   Reading them here, unfiltered and every sweep, is what stops a bare status
   change (a ticket moved from in development to implemented with no comment)
   from sitting stale for days, which is the failure this half exists to prevent.

2. **The incremental gate: what to read.** A `search_tasks` with
   `modified_at_after` set to the exact UTC timestamp the SWEEP PLAN prints
   (two minutes before `checked_at`), across `projects_any`, returns only the
   tickets that moved since the last sweep. This decides depth, not membership:
   everything unchanged costs nothing, so you do not call `get_task` or
   `get_task_stories` on a ticket the gate did not return and no thread flagged.
   For the tickets the gate did return, batch their stories into a single call,
   `get_task_stories(task_ids=[...], created_after=<since_utc>)`, so Asana
   returns only the comments and activity created since the last sweep. The
   things that earn a deep read are a ticket the gate returned, a ticket whose
   thread moved in step 4, and **any ticket the census turned up that is not yet
   on the board**: a brand-new ticket is read in full the first time however old
   its last edit is.

The SWEEP PLAN names the incremental gate. It does not replace the census: run
the census even when the plan does not mention it, because the plan is built from
what is already on the board and cannot list a ticket the board has never seen.

**The census is the only door onto the board. A ticket it did not return does not
become one, however important the room made it.** This cuts both ways and the
second way is the one that has actually gone wrong: a sweep read an unassigned
incident that the standup had just made Top Priority, judged it important, and
wrote it on as a ticket with an item asking Kraken to staff it. Nobody had given
it to the owner, their name was nowhere on it in Asana, and the desk is their
to-do list rather than the programme's. So:

- **Assigned to the owner in the census, or it is not a ticket.** An unassigned
  ticket is not theirs even when it is Top Priority, an incident, and the only
  thing the room talked about. Neither is one assigned to somebody else, unless
  it is already on the board with an open item of theirs on it.
- **Important and not theirs is `news`, and that is the whole of it.** One row,
  the route by which it reaches one of their tickets named, and off again when
  that route closes. If the sweep did real work on it, a code trace, a count out
  of prod, the finding goes in the `news` row's `what` so it is not lost. Never
  an item, because an item is something the owner does.
- **Chasing a Kraken queue is never their item.** "Get an engineer onto this" is
  not work the owner owns, it is somebody else's resourcing, and step 6 already
  says so.

Against that combined set:

- The ticket set is every incomplete task assigned to the owner in the projects
  in `config.json` `asana.projects` (the census above, leaving out anything in
  `asana.exclude_projects`), **plus every ticket already on the board that still
  has an open item**. A ticket open in Asana belongs on the board even when
  nothing needs the owner today, and the gate only tells you which of them to
  actually re-read this time: it never decides whether one belongs.
- For **every** ticket on the board, refresh `asana` from the census every sweep,
  not only the ones the gate flagged: `status` (the custom field named `Status`,
  read from its `display_value`), `section`, `priority`, `category`, `severity`,
  `assignee`, `completed`. These are what Asana says, not your reading of it, and
  the page prints them under Asana's own field names. The census already carries
  all of them in one call, so trueing up every ticket's Status costs nothing, and
  a status the page shows must match the ticket the moment the owner opens it, so
  this is not optional and not gated on a comment having appeared.
- **Then say whether that Status is behind what happened.** On a ticket assigned
  to the owner and still open, when the timeline has moved past what Asana says
  (a build released while the ticket still waits for an engineer, an engineer
  building it while it still says waiting for one, the client re-testing while
  it says ready for development), write `asana.status_should`, one of the
  project's Status options exactly as `config.json` `asana.status_field.options`
  spells them, and `asana.status_why`, one short line naming the event and its
  date. Only from an event you can quote, never from a guess at what comes
  next. Never on a ticket assigned to someone else, and never for a CE build's
  own Status, which is the refining engineer's to set. When Asana already
  agrees, take both fields off (`null`). On tickets assigned to the owner,
  DANDORI automatically syncs `asana.status_should` into Asana after the sweep
  (and when a draft carrying `"asana_status": "<Option>"` is marked posted), so
  never tell the owner in a step to manually change the Asana Status dropdown on
  a ticket they own.
- **Always read attached images and files in Asana (`mcp__asana__get_attachments`).**
  Whenever a task's `notes` or a comment contains `get_asset?asset_id=`, an
  `attachment_added` story, or mentions an attached screenshot, rate table or
  spreadsheet, call `mcp__asana__get_attachments(parent="<task_gid>")`, download
  any image, PDF or sheet to the client's `tmp/` folder via `curl -fsSL "<download_url>"`,
  and inspect it with `Read` before drafting any question or reply. Never ask
  the client to confirm prices, tables or error messages they already pasted in
  an attached image.
- **Status follows the build.** `config.json` `asana.status_field.by_stage` says
  which client Status each CE build stage means (an engineer building it is "in
  development", whatever the client's word for that is). After step 3, any
  ticket the owner owns whose build stage maps to a Status later than the one
  Asana shows gets `status_should` set to that Status, and `status_why` naming
  the build event and its date ("engineer picked up the build 3 Nov"), never the
  engineer or the KT number, since the owner may paste it. The digest lists the
  tickets this already applies to under step 3; re-check them against the stage
  you read now. `./core/audit.py` flags one you missed.
- **When an engineer first picks up a bigger build, the client hears that
  development has begun.** On the sweep where a build first reaches `building`
  (no earlier `building` event on the ticket), and the build is 3 story points or
  more, an M t-shirt or larger, or one the client has asked the timing of, raise
  or update an item with a **drafted** Asana comment on the client ticket, in the
  client's language and the house style: development has started, what is being
  built in the client's own words, and what the client will be asked to do next
  (usually test it). Work out when it should be done only from something you can
  quote: a date the engineer gave in a thread, the build task's `due_on`, or the
  release the build is scheduled into. That estimate always goes in `prepared`
  with its source, as a target and never a promise. It goes in the comment too
  only when the profile's `## Never reaches the client` allows delivery dates;
  where it forbids them, the comment says the owner will share the timing and
  the date stays on the desk. With no source at all, add a step to ask the
  engineer for one, following the ask-don't-propose rule. When a comment went out
  saying the timing would follow and a source turns up on a later sweep, draft
  the follow-up comment with the estimate, where the profile allows it, on the
  same item. Nothing internal goes
  in the comment: no engineer's name, no story points, no build ticket. A small
  build gets the Status change alone. Set the draft's `expects_reply` false.
- New tickets since the last sweep get added, with `where_it_stands`, `terms`
  and `threads` filled in the same way the morning prep does it.
- A ticket completed in Asana is **finished**: set `asana.completed` true, move it
  into the closed fold, and from then on leave it alone. On later sweeps do not
  `get_task` it, re-read its threads or refresh its fields, and it never earns a
  prep block. Close or drop any items still open on it unless one is a genuine
  follow-up that outlives the ticket (say why in its note), so a closed ticket
  goes quiet rather than sitting on the active list.
- **A reassignment changes who the client's owner of the ticket is, not whose
  work it is.** When the Asana assignee moves to someone else, write the new name
  into `asana.assignee`, add the reassignment as an event, and address later
  drafts to the new assignee. The card stays and the items keep their numbers.
  Never drop an item because the ticket moved: it goes only when the thing it
  asked for has happened or somebody has actually taken it, and the note has to
  name who. A ticket leaves the board when every item on it is closed or dropped,
  and never before, because the items are how the owner refers to their own work.
- **A board ticket still open here (not already marked `asana.completed`) but
  absent from the census was completed or reassigned: find out which, never leave
  it on the last sweep's values.** The census is every incomplete task still
  assigned to the owner, so a board ticket missing from it has either been
  completed (set `asana.completed` true, move it to the closed fold) or handed to
  someone else (`get_task` it, write the new `asana.assignee`, add the
  reassignment event). A silent reassignment (a ticket quietly moved to a
  colleague) or a silent close reads on the page as still-theirs and still-open,
  which is exactly the stale status this must catch.
- For each ticket with `internal_ticket.url`, refresh `internal_ticket.build`
  from that CE build task. `stage` is the one judgement in it: read Status
  (Kraken Cust), Client Engineering Status and the section together and pick
  `refining`, `queued`, `building`, `review`, `released` or `verified`, where
  `verified` means the client have checked real output produced after the release
  (bills, statements, whatever the workstream makes), not that Asana says Done.
  Fill `kt`, `asana_status`, `engineer` (empty string when nobody is on it),
  `size`, `refined_by`, `refinement`, `feature_flag` and `moved_on` from
  `modified_at`. Rewrite `waiting_on` as one sentence saying why it is not
  moving. All of it is desk-only and must never appear in `prep` or anything
  addressed to the client, `safe_to_say` excepted.
- Then bring `closes_when` up to date on every ticket, since that is what says
  whether a quiet ticket is finished or only quiet. Flip a gate to `done` when
  the sweep found the thing that closes it, and add a gate when something in
  this sweep created one. Keep the gates nobody owns as items: an engineer
  assigned, a release, a feature flag switched on, the client's own verification.
  Cite the item number in `note` rather than restating the item.
- **When a feature flag is switched on, the ticket description has to say so, or
  the client will not know the change is live.** If the client's tickets carry a
  feature-flag block in the description (the profile's `## Lessons from my work`
  describes its layout, typically a test and a production section with one line
  per flag: the settings-page link under `config.json` `client.support_url` and
  its on/off state), that block is where the client read the current state.
  "Released" and "switched on" are two events, so a build can sit behind a flag
  that is off. On any flag-on event a sweep finds, raise or keep an item with a
  **drafted** description edit that sets the matching state to on, and never let
  a ticket head to close with its production flag still showing off or blank
  when it is actually on. The desk never writes to Asana, so this is a draft the
  owner posts, like any other client-facing change.
- **When a feature or bug-fix reaches `review` or `released` and logically has
  testable output, ensure concrete test evidence exists before asking the client
  to sign off or rolling out to production.** Unless the change does not
  logically involve test data or visible output (for example a pure doc/process
  note or a zero-data config cleanup), a plain-text description of an engineer's
  local test run is not enough for the client. Either:
  1. **The owner's desk review**: switch on the feature flag in TEST immediately
     (where the profile allows turning on TEST flags without asking the client
     first), verify the fix on a TEST account (or capture screenshots of the
     result), and attach the screenshots (`prepared.files` with `path` or `how`,
     tagged to the client draft's `draft` number) or cite the TEST account in
     the draft.
  2. **The engineer's evidence**: if setting up a TEST account is too complex or
     needs jobs/data the owner cannot run, draft a message to the engineer on
     the internal build ticket asking them to switch on the flag in TEST and
     provide a TEST account, or share screenshots of the account status from
     their local/test run. Do not draft a text-only test summary to the client
     while concrete evidence is still missing.

For a ticket the gate returned, a bump is not always a comment: a section move, a
status change or a bulk edit stamps `modified_at` too, and several tickets
stamped the same minute is that rather than a conversation. Read the `asana`
fields the gate already gave you against the board first, and pull
`get_task_stories` only when a changed field does not already explain the bump.
That keeps even a moved ticket to one story pull at most.

## 3. Programme-wide meetings when they have run

The workstream sometimes surfaces in the programme-wide meetings, not only the
main standup (`config.json` `meeting`). Those meetings are listed in
`config.json` `meeting.also`, with their days, times and where their notes land
(the profile's `## My rooms` says more). On days you refresh on or after one of
them, read that day's notes and fold any mention of the workstream into `news`
(step 7).

Only content for the workstream matters. Add or update a `news` row when you can
name the route by which it reaches one of the owner's tickets or the build queue
behind them. Do not open items for team-level Kraken action items unless they
land on the owner's tickets. Store what you read in `migration_note`, same shape
as `meeting_note`: `date`, `found`, `url` (the notes page's link, with the day's
anchor where it has one). Do not re-read a programme-meeting note you have
already folded in.

**The standup.** The main standup is the post run's job, but that run can fail
or still be polling when you start, and a sweep after the standup that leaves it
out hands the owner a page that is a morning old. So when the digest has a step
D, the standup has run and `meeting_note` has not caught up: fold it in here.
Read the doc it names with `mcp__google__drive_read_file` in the same message as
the census and the Slack reads, the transcript tab as well as the summary
(the transcript is where the pushback and the quiet promises are). With no doc
named, look where step D says, and when nothing is there either, leave
`meeting_note` alone and say so in `gaps`. When step D says the call has not
ended yet, read nothing and leave `meeting_note` alone: Gemini writes the notes
when the call ends, and the next sweep picks it up.

What was said goes on the owner's tickets the way everything else in this sweep
does: an event at the minute it was said (the transcript has the timestamps),
attributed to the meeting and to the side the speaker is on, with the notes doc
as its `source_url` and the meeting's calendar name at the start of `where`
("Billing Stand Up (Gemini notes)"). That pair is how the week strip ties the
meeting to the tickets it moved, so an event without them is a meeting that
shows as having touched nothing. Then the items it moves,
following steps 6 and 7; a new number only for work the meeting gave them that
no open item already covers; and `news` for what moved around them with a route
to one of their tickets. A topic the standup closed out with nothing owed back
gets its event and no new item. Keep `sessions` true when the meeting said a
room is not happening or something else is happening instead. Then set
`meeting_note` to `{date, found: true, url, gist}`, the gist one line per
ticket it moved.

When step D says the doc sits on a programme-wide room that shares the standup's
Meet call, the one doc is both meetings: fold the programme part in as section 3
says and set `migration_note` to the same url and date too, so step N does not
offer it again.

When the owner points you at a note from some other room, read it the same way
and record it in `other_notes` with `date`, `title`, `url`, `found` and a `gist`
saying what came out of it, so a later sweep knows it has been read. A one-off
session usually reaches a ticket as evidence rather than as work: something
answered there that their ticket has been guessing at. Put it in that item's
`prepared` with the session as the source, not in a new item.

**Rooms the calendar says happened.** The desk reads the owner's Google Calendar,
and when a work meeting ends Meet attaches Gemini's notes to the event. The
digest's step N names every such meeting no note record covers yet, with its doc
link. Read each one with `mcp__google__drive_read_file`, in the same message as
the census and the Slack reads, and treat it exactly like a note the owner
pointed you at: what was said goes onto the tickets it touches, and the meeting
gets its `other_notes` row (or `migration_note` for one of the programme-wide
meetings in `meeting.also`) even when nothing in it reached their work, gist
"nothing for the owner's tickets", so it stops being offered. Gemini's notes are
a machine summary: quote what it says somebody said, attribute it to the meeting
and not to the person, and never let it be the only source for closing an item.
A doc that will not open is a gap, not a guess.

## 4. Then the conversations

- Every Slack read the plan's step S names, all of them, every sweep. Slack has
  no gate: a reply there moves nothing on the board until you read it, so a
  thread is never skipped for looking quiet. The owner's own messages in them
  are events too ("You"), and so is anything the searches turn up.
- Any client email check the plan's step E names (`gmail_search_messages`,
  strictly read-only; read matching threads with `gmail_get_message`).
- New replies or updates in every other linked thread, Notion page, Google Doc,
  or Miro board listed under step 2 (`threads`), marking them in `"checked"`.
- Slack messages that mention the owner and have no reply from them, in the
  channels those threads live in. If one belongs to a ticket on the board, attach
  it as a thread and give it an item. If it belongs to no ticket, still give it
  an item on the closest ticket and say so in the item's `why`.
- The internal build ticket, when the client is waiting on a build.

Do not re-read closed items and do not re-read the meeting note or migration
note you have already folded in.

## 5. Put what you found in the timeline

Add each new event to that ticket's `events`, in time order, with `on`
(YYYY-MM-DD), `at` (HH:MM), `who`, `what`, `so_what`, `where` and `source_url`.
The owner's own messages go in as "You". Leave `so_what` empty unless the event
changes what they do.

## 6. Move the items those events affect

- Someone answered something the owner sent: the item comes back to `todo` on
  the same number. Rewrite its `draft` for the reply and put what already
  happened in `progress_note`. Never open a new number for the next leg of a
  conversation they are already in.
- The thing an item was held for has happened: drop the `hold`, set `todo`.
- The owner sent something and the board did not know: `state` becomes `waiting`
  with `waits_on` filled in, or `sent` when nothing comes back. **Never keep an
  item in `todo` with speculative post-reply steps after the owner sends a
  message that asks someone a question or awaits their sign-off**, and never
  bundle downstream execution steps ("Post draft 4 asking X, then rewrite the 3
  guides") into an item before X has answered. While a decision or answer is
  owed by X, the item is `waiting` on X; downstream steps only become `todo`
  once X replies (or if they do not depend on X's answer at all).
- The owner marked it sent from the page (the digest's sweep plan, step 4): their
  press stands. Find the message in the thread. An answer after it moves the item
  as above; no message found is a `"note"` on the item saying so, and nothing
  else.
- Genuinely new work goes in the patch's `new_items`; apply numbers it.
- Nothing moved on an item: leave it exactly as it is.

End the patch with `"swept": true`, which sets `checked_at` to now.

**When part of an item is done, rewrite the title to what is left.** Writing the
fact into `progress_note` is not the job; `progress_note` is additive and safe,
and adding to it is what a sweep does instead of committing to a change. An
actual example, from a sweep that otherwise ran clean: it read the CE ticket,
correctly noted that the spec had been attached that morning, and left the
item titled "Attach the spec to the CE ticket and correct the account count".
Only the second half was still true. The owner reads titles to decide what to
open, so a title describing finished work sends them to do it again, and they
cannot tell from the list which half is left.

So on every item you touch: if the title names two things and one has happened,
retitle it to the one that has not. Trim the `steps` that are done rather than
annotating them. Cut `est_minutes` to what remains. If everything in the title
has happened, the item is closed, not retitled.

A step the owner ticked on the page is in `steps_done` (keyed by the step's
words) and the digest marks it `[ticked done by the owner]`. That is them saying
the step happened, so trust it: take that step out of `steps` and move on from
the step after it. The tick goes with the step on its own, so leave `steps_done`
alone. Never tick a step yourself; a step you rewrite in new words starts
unticked, which is right only if it is still to do. If every step in `steps` is
ticked in `steps_done` and no unsent draft remains, the owner has finished the
item: close it (`done`).

Whenever you touch an item, leave its `steps` fit to act on: one to four, in
order, verb first, aimed at the owner, naming the place and the thing. A job
that is one action is one step. Going to the place is part of the action done
there, never a step before it: "Open the canvas, section 3" then "Paste the entry
below" is one step, "In the canvas, section 3 (link), paste the entry below".
Never split a step to fill a count; if you merge steps, move the draft's `step`
to the number it now has.
Commentary is not a step and belongs in `why`. Leave a `done_when` saying what
the finished thing is. If the steps tell the owner to ask, tell, confirm or
reply, there is a `draft`; if they tell them to read or compare, say so in a step
and leave the draft out. An item with no steps renders as a warning on the page,
so never leave one behind.

**Steps are only what the owner does, and status is not a step.** Write them for
someone who opens the card cold, with none of the context in your head:

- **Status goes in `where_it_stands`**, one to three plain sentences drawn above
  the steps under that label: what is known, what has not happened yet, who is
  holding it. "The new hold has been on since Tuesday" is status, never a step.
- **Each step is a verb, a place, and what to do with the result.** "Read the
  thread" is not a step; "Read Alex's reply in the DM. If they say the fix is
  live, do step 3; if not, ask them for a date" is. A step that ends with nothing
  to decide or write is not finished.
- **Prepare it rather than describe it.** Lists of accounts, settings or counts
  never go in a step. Put them in a `prepared` note as a table (one row per
  group, with what it should show and what it shows now) and the step says
  "Check the accounts table in prepared". If you can run the query, run it and
  put the answer in the table, so the step is "compare", not "go and find".
  When diagnosing a specific client account and `clients/<client>/scripts/`
  provides an account diagnostic script (named in the profile's `## Data I can reach`),
  run that script rather than writing partial ad-hoc SQL so you never miss
  another fuel's agreement, a statement/manual hold, or an integrity resolver.
  Production reads go through the read-only database MCP named in `config.json`
  `database.mcp`, only when it is set; a single account is usually faster on the
  support site (`config.json` `client.support_url`). Never write to either.
- **No shorthand the owner has to decode.** Never "the 40", "both codes", "stage
  one" or a bare item number. Name the thing the first time on the card: "the 40
  accounts the client listed in their second batch", "the old hold (OLD-HOLD-CODE)
  and the new one (NEW-HOLD-CODE)". Name another job by its title, not "see 17".
- **One action per step, under about 200 characters.** If it needs more, the
  rest is a table in `prepared` or a second step.
- **Only what is due now.** Something that waits on another person or a later
  date is a `done_when` condition or a later step that says what it waits for,
  never an instruction to do it today. Never write a release-announcement or
  canvas draft with placeholder dates (`MM/__`, `TBD`, `（_）`) before a real
  release date is agreed; keep that requirement in `closes_when` or `done_when`
  until the date is known, and use the exact workspace URL from the profile.

`./audit.py` lists steps that break this ("Steps that are not steps"); fix them
before finishing.

**No step moves the job.** "Then `./tick.py 52` once it is posted" is a terminal
command on a page the owner reads in a browser. Every draft carries a **Posted
it** button under its words that marks it sent and moves the job, so the steps
stop at the posting, and the draft says what happens after. Set `reply_from` to
who answers it ("Sato-san, client") when that is not the person the `target`
names, and `"expects_reply": false` when nothing comes back, so the button closes
the job instead of waiting on somebody. The same press ticks the step that said
to post it: set the draft's `step` to that step's number (1 is the first) when
the step does not name the draft's recipient or say "draft N" itself.
`audit.py` flags a step that names `tick.py`.

**A draft is either the message the owner would send now, or it is gone.** The
work view is only worth trusting if nothing on it is left over, so read every
`draft` still on the ticket against what you have just found. If the thread
moved, rewrite the draft into the message they would send this minute and say in
`progress_note` what changed and why. Format every draft so it pastes cleanly into Slack and Asana (the Copy button copies both rich HTML and plain text): use `- ` (or `・` in Japanese) on its own line for each bullet (never `•`, and never multiple `・` items on one line, which Asana collapses into a run-on line), `1. ` `2. ` on its own line for numbered lists, `**bold**` with a blank line above for section headings (never `*single asterisks*`), full uncompressed `https://...` URLs labelled before the URL when Asana's `...` truncation would hide the query tail, and zero `(paste image here)` notes inside draft text (Asana stacks pasted images at the bottom). Never imply immediate unilateral PROD deployment when sharing TEST results; ask the client to review TEST and align on PROD timing. If the reason for the message has gone,
delete the draft and the step that asked for it. A draft written yesterday
morning is the most dangerous thing on the page, because it reads as ready to
paste and it is addressed to the client.

**Before drafting a build commitment to the client on an edge-case or incident
ticket, weigh whether an operational route makes more sense (`prompts/post.md`,
"Claim only what you can stand behind").** Push back toward existing operations
when, and only when, there is an obvious low-burden alternative: the case is
rare or temporary (such as a post-migration edge case), existing checks, holds
or patrol already catch every instance, manual handling is light, or changing
shared logic carries regression risk. Admit the bug honestly, give the count,
and propose the operational path first while asking the client's view. When no
clean operational alternative exists and a fix is warranted, do not push back
for its own sake, and phrase the proposal in measured terms ("check the fix
approach and impact with engineering") rather than promising an unconditional
build before refinement.

One exception: before clearing a draft, check whether it was already sent. A
draft with `"sent": true` was marked by the owner's own press of Posted it, so
leave it where it is: the page already shows it as what they sent.
If the `progress_note` or a recent event records that the draft's message was
actually posted, copy the sent text into the item's `history` as
`{"at": "<ISO timestamp>", "state": "sent", "note": "<sent draft body>"}` before
wiping the `draft` field. A sent draft is part of the record and must not be
lost even when the item comes back to `todo`.

Rewrite a prepared block, never append to it. A finding that starts
`[30 Sep] Still true after re-reading` or `[30 Sep] Correction:` is a sweep's
working notes left on the owner's page: twelve of them on one card and the
original owner said they could not tell what mattered or what to act on. When
something new is learned, fold it into the finding it changes and correct the
wrong one in place, so the block reads as what is true now, four findings at
most, each leading with its claim in one sentence and the evidence after it. The
page shows that first sentence as the line and folds the rest, so a first
sentence that is not the claim hides it.

Condensing never loses anything. `board.save()` compares the block with what was
on disk and moves every finding or note you took out into `prepared.earlier`,
dated, where the card shows it under a fold. So cut freely, and never paste
dropped detail back into `findings` to keep it safe: that is the twelve-finding
card again. Never write `prepared.earlier` yourself either; it is the record of
what was cut, and only `save()` keeps it.

Prune what was prepared the same way. Take an `unanswered` question off once it
has an answer, and drop a `prepared` note whose finding has been overtaken,
rather than letting a card grow a history. Nothing stays because it was true
once.

**An item is work somebody is waiting for, not a thought you had.** Before you
write one, name who is blocked without it and what they do with it. Three tests
kill most candidates:

- **Already answered elsewhere.** If the client's own Asana field, the ticket's
  status or their own linked ticket already says it, telling them again is not
  work.
- **The owner's answer already landed.** A question answered in a room, and not
  argued back, does not need a written repeat. Put the answer in `progress_note`
  and set the item to `monitor`, so it only comes back if they reopen it.
- **Somebody else's job.** The client run some checks and recoveries themselves
  (the profile says which). Confirming to them what they did themselves is noise.
  So is chasing a Kraken queue position, which is not the owner's to move.

**If the next move is someone else's, the item is `waiting` on them, not a
`todo`.** A `todo` means the owner can act on it this minute. The moment the ball
is with another person (an engineer has picked up the build and opened a thread
but not yet answered, CE has the cause and a PR is coming, a task has been handed
to another team who will report back when done) the item is `waiting` on that
named person (or `monitor` if there is no single owner), and the sweep's only job
on it is to scout for their reply. Leaving it `todo` puts work at the front of
the owner's list that they cannot actually start, which is the noise this desk
exists to remove. Flip it back to `todo` only when they come back or the next
concrete step becomes the owner's. This is the normal reading of a ticket, not a
special case: most of what sits on the board at any time is genuinely waiting on
somebody, and the front of the list should be only the handful of things that
are truly the owner's to do now.

**A waiting item gets a chase date, and a CE build gets a real one.** Every
`waiting` item needs `waits_on.chase_on` or it sits forever (the audit flags a
missing one). A conditional chase ("at the next session if they stay quiet") is
fine for a question sitting with a person, as long as the condition goes in
`waits_on.what` and `chase_on` still carries a date. But when the item waits on a
**CE build** (an engineer building it, a PR promised, a fix queued), it has to
land inside the cycle, so give it a **concrete date, on or before the cycle
end**. Where the client's project names the current cycle's dates in its section
(a section such as "Cycle 9 (5/4-5/15)" gives 15 May; the
profile says whether yours does), a build with no other deadline is chased by
then, sooner if something was promised sooner. In the patch that is
`"items": {"<n>": {"state": "waiting", "who": "<who>", "chase_on": "<date>"}}`,
or just `"chase_on"` on an item already waiting.

Two things the client need in one place go in one comment, on the older number,
not two comments an hour apart. Drop the number that got folded in with a note
saying where it went. A shorter list that is all real is the point; five items
the owner believes beats nine they have to filter.

**Check this every sweep, not only when you write a new item.** Two items whose
drafts are addressed to the same person on the same ticket are one item, however
they got there, and the way to find them is to read the `target` of every open
draft on a ticket together. Three numbers that end in one Asana comment cost the
owner three readings and a decision about ordering that does not exist.
Likewise, **never create or keep a numbered item that duplicates a dedicated
desk feature**:
- Moving a client ticket's Asana `Status` field belongs in `asana.status_should`
  + `asana.status_why` (the one-click `Set in Asana` button on the ticket
  header), never a standalone item ("Set <ref> Status to ... in Asana").
- Raising refined CE builds for the recurring prioritization round belongs in
  `prioritization.builds` (the `Prioritization` card, its drafted note to the
  lead, and the calendar's `by Raise builds` entry), never a numbered item on a
  ticket ("Raise KT-... with <lead> for the prioritization round").
- Never fold work for two different tickets into one ticket's item.

**Then chain what is genuinely sequential, with `after`.** Most of what is left
on a ticket is a queue rather than a menu: the scope cannot be confirmed before
the sheet exists, the reply cannot go before the answer arrives. Set `after` to
the number it follows and the page stops offering it: it folds under the item in
front of it, saying which number that is, and becomes a job by itself the moment
that one closes. The test is whether doing it first would be wrong or impossible.
Two items that merely sit on the same ticket are two items, and two that end in
the same message are one item, not a chain. An `after` pointing at something
already closed is ignored, so nothing has to tidy up after itself.

Between the two, the front of the list should only ever hold work the owner could
start this minute. That is the whole purpose of the view: they read the top, and
it is true.

**Sort by what has to happen first.** `urgency` is what orders the page inside
each group, so a `today` that is really next week is worse than no flag. Reserve
`today` for work with a person or a room waiting on it, and use `monitor` for
anything that only needs watching.

**`urgency` is one of exactly three words: `today`, `this-week`, `monitor`.**
Hyphen, not a space. Nothing else is a value, and this is not a style
preference: the page reads these three to place a job on the week strip, so
`this week` with a space is a job that loses its column. `./check.py` fails a
board that carries anything else, and `./tick.py` will not save one, so a
mistake here stops the sweep rather than quietly mis-sorting the page.

**Every job the owner can act on needs a day, one way or the other.** The week
strip is how they plan, and it gets a job's day from one of two places:

- Work sitting with **somebody else** carries `waits_on.chase_on`, an ISO date:
  the day their silence becomes the owner's problem again. Always a real date,
  never a sentence: `"chase_on": "Only if they raise it again"` is a job with no
  day, and it is invisible on the strip. Put that sentence in `waits_on.what` and
  give `chase_on` a date, or leave it out and set `urgency: monitor`.
- Work sitting with **the owner** carries `urgency`, and the strip reads it:
  `today` lands on today, `this-week` on the next working day, `monitor` stays
  off. So an actionable job marked `monitor` is a job they will not see coming,
  and that is the one mistake to avoid here.
- Work of the owner's that has **a deadline** rather than a start ("by Fri 6 Nov",
  the working day before a prioritization round, a date the client set) also
  carries `due_by`, an ISO date. Without it the job reads "Do now" a week early;
  with it the card reads "By 6 Nov", the strip draws it on that day, and it turns
  "Due today" when the day comes. Keep `urgency` on it too.
- **Rarely, a phone reminder.** `remind: {"on": "YYYY-MM-DD", "at": "HH:MM",
  "why": "..."}` puts the job in Apple Reminders as well, and that is a ping on
  the owner's phone, so it is for the job where missing the moment does real
  harm: a fixed production window the owner runs, a deadline promised to the
  client, a cut-off nobody else will catch. Never for a status comment, a chase
  or anything the page already shows in time. Most sweeps add none; the board
  holds three at most. Set `at` a little before the moment, and remove `remind`
  (`null`) once it has passed or no longer applies. Closing the job clears it.

A job is therefore either somebody else's with a real `chase_on`, or the owner's
with an honest `urgency` and, when it has a deadline, a `due_by`. Anything else
disappears from the week they plan against.

**`at_standup` follows `sessions`, not habit.** The main standup runs on the
days in `config.json` `meeting.days`, so check the next live session before you
leave a flag on. If the next room is days away and the item is due today, or it
is a session where the owner does not walk their tickets, take the flag off and
put the written route in the steps instead: no standup means it goes on the
ticket. Correct `sessions` itself when you find a room that is not happening or
one that nobody put there, and drop entries whose date has passed. Never touch
`prep` or `script` unless `DELTA FINDINGS` explicitly marks `PRE-ROOM SCRIPT SYNC ACTIVE`
for a moved ticket: outside that 90-minute pre-room window, `prompts/prep.md` owns them.

**Do the part of the item you can do.** If a step could be followed without
judgement, following it was your job, not the owner's: read the comments and
pull the conditions out, read the Kraken Core checkout the profile names (under
`## Data I can reach`, if it names one) for what the code does, compare the two
and mark the difference, count the cases. The product goes in `prepared` with
its sources, and the steps that remain start after it. `prompts/post.md` has the
full rule and the shape. What is left for the owner is what needs them: sending,
deciding, speaking.

Write the product to be read once. The `conclusion` is one plain sentence they
could act on without reading on; findings are one fact each, so-what first, four
at most; plain words and file:line rather than adjectives. The section of
`prompts/post.md` on writing it to be read once is the standard. It changes how the work reads,
never what is in it.

Then say what you could not settle, in `unanswered`, with why it is open. A
prepared block that shows no gaps is read as a finished answer and repeated in a
room on that basis, so an honest gap is worth more than a confident paragraph.
Keep table cells to a phrase and mark the row that is the point with a toned
cell. When the product is a document the client will read rather than words the
owner will say, write it into the client's `docs/` folder as HTML, run `python3 core/make_doc.py`, and
point `prepared.files` at the HTML and the PDF, with `draft` set to the draft
that sends them. Only what goes out or what the owner opens is a file; an export
or script made on the way is not. `./audit.py` flags a block over four findings
or six files, and the fix is to rewrite it whole: `save()` keeps what you cut.

A draft that claims or asks for evidence, or that would otherwise pack dates, rows or multi-account numbers into prose, carries an image instead. Generate or save the screenshot or query table into the client's `docs/` (using the workspace's portal/database evidence scripts in `clients/<client>/scripts/` when available) and put it in `prepared.files` with `"draft": K` and `"where"` saying exactly where to paste or attach it (for example `"Attach to the Slack reply"` or `"Paste right under section 2"`), so the page shows the thumbnail and its placement inside that message's box. Crop or scrub all customer PII before any screenshot is saved. Evidence only the owner can make, a screen behind a login you cannot reach, a test record to create, is an entry with `label`, `how`, `where` and `draft` and no `path`: in `how`, the page or query to open, what to do, what the result must show, and the `docs/` path to save it as. Never write "attached" in a draft that has nothing attached and no `how` saying how to get it. Likewise,
when a draft reports feature or bug-fix test results to the client for sign-off
or rollout (and the change logically involves test data, calculations or UI
output), never rely on a text-only summary of an engineer's local run: pair the
draft with a TEST account ID the client can inspect, a screenshot in
`prepared.files` (`path` or `how`), or a step/draft asking the engineer on the
build ticket to provide a TEST account or local screenshots first.

## 6b. A quiet client ticket gets a status draft, and a stalled build gets a chase

The client expects to see, on every open ticket on the board, what is happening
on it. A ticket whose last word from Kraken is a week old reads to them as nobody
working on it, and they will ask in the room, in front of everyone. So on every
sweep, for **every open ticket assigned to the owner**:

1. **Find the last thing the client could see from Kraken** on the client
   ticket: a comment by the owner or any Kraken colleague. A status-field change
   is not one, and neither is something said in a meeting. Read the ticket's
   comments if the gate skipped it.
2. **Never wait seven days when something moved internally, entered a Wave, got a
   client reply, or was promised in a meeting.** Raise or update a `todo` item
   immediately (with `urgency: "today"` when promised in a session or replying
   to the client, or `"this-week"` for proactive status updates) carrying a
   ready-to-paste client-language Asana comment draft whenever **any** of these
   is true:
   - **Scheduled in a Wave / given a target window**: a linked CE build has a
     `wave` or `due_on` and no Kraken comment on the client ticket has yet told
     the client the expected cycle or target date window (where the profile
     allows sourced estimates, state the cycle/month window as `〜頃目処 / 予定`,
     never naming internal `Wave` numbers, story points, or `KT-...` IDs).
   - **Internal stage moved after the last client comment**: refinement finished
     (`Ready to build`), an engineer picked up the build, or a PR merged after
     the newest Kraken comment on the client ticket.
   - **Client commented or agreed**: the client replied on the ticket confirming
     scope, sharing patrol counts, or asking Kraken to proceed, and Kraken has
     not posted a short acknowledgment + current status on the ticket.
   - **Promise at a standup**: the owner said at the meeting they would check
     and comment on the ticket today.
   - **Seven days with nothing visible**: even if nothing changed internally,
     a week of silence still earns a short status comment (`エンジニアアサイン待ち`
     included).
   Write the draft in the client's language and house style, conclusion first,
   three to five lines: where it stands, the expected cycle/window (when sourced
   and allowed), and what comes next. Nothing internal goes in it (AGENTS.md
   hard rule 3). The comment is read inside the ticket, under its title and its
   earlier comments, so it never opens by naming the ticket or what it is about
   ("〇〇の件ですが", "Regarding the X"), and never retells an earlier comment
   there; point at one in a few words when you lean on it. Open with the news,
   under the @mentions of the people who need to act or know (whoever asked,
   the client-side owner, CC the others on the last exchange).
   Only a comment you build on that is weeks old earns one clause of context
   back. `./core/audit.py` flags a draft that opens on the ticket's subject.
3. **Skip only** a ticket completed in Asana, one whose open `todo`/`hold` item
   already carries an unsent client Asana comment draft (refresh that draft
   instead of adding a second), or one that Kraken updated on the ticket within
   the last few days with no internal or client event since.

**Check every build behind those tickets for a slip in the same pass**, because a
quiet client ticket usually sits on a quiet build. Read each `internal_ticket` in
Asana, and when one of these is true, add an item with a draft to whoever can
unblock it:

- refined (size and refinement status set) but its status never moved on to
  ready to build, so no prioritization round can pick it up;
- ready to build through more than one round with no order or wave;
- in progress with no engineer, or no activity for a week;
- an engineer's question or request to the owner sitting unanswered in a thread,
  such as an IE asking for a fresh request or a reviewer asking for a file. This
  one hides best: the board says "waiting on the engineer" while the engineer is
  waiting on the owner. Read the thread, not the item's `waits_on`.

Name each slip, by ticket ref, in the first lines of the report.

## 7. Keep the news honest

`news` is what is moving around the owner that is not one of their tickets: a
priority that changed, an outage upstream, a decision on somebody else's ticket
that will be quoted back at one of theirs. Add a row only when you can name the
route by which it reaches them, and take a row off once that route has closed.
Five at the outside. Each row carries `topic`, `what`, `why`, `on` and
`source_url`.

## 7b. Keep the milestone current

`milestones` is a programme the owner leads, drawn like a Jira epic: a target
date, a status, the phases from here to done, and the tickets under it. It owns
no work of its own; the owner's jobs on it are items on ticket cards, listed by
number in its `items`.

- The SWEEP PLAN's `M.` search finds its child tickets in Asana. Bring the
  milestone's `tickets` rows up to date from it: `ref`, `title`, `asana_url`,
  `assignee`, and `state` from the Status field. The digest prints each row as
  `tickets.<n> gid=...`: match a search hit by gid and write
  `"milestones.<id>.tickets.<n>.state"`; append a new child with
  `"milestones.<id>.tickets+": [{...}]`. A row with no ref has none; `none` in
  the digest is not a value to copy.
- **A child assigned to the owner in the census becomes a ticket card**
  (`new_tickets`, with its first items), and its row stays on the milestone
  pointing at it. A child assigned to anyone else, or nobody, stays a row and
  never becomes a card or an item: the board is assigned-only.
- Move a phase's `state` (`done`, `now`, `next`, `blocked`) only on an event you
  can quote, and say what in its `note`. Address phases by position:
  `"board": {"milestones.<id>.phases.2.state": "done"}`. Add any item that is
  work on the milestone with `"milestones.<id>.items+": [<n>]`.
- **Each phase has one owner and reads as one thing.** `who` is one party, never
  "the client, then Kraken": when a handover has happened, it is a `done` phase
  ("the client hand over the definition sheet", with the date) and the work after
  it is the next phase, owned by whoever does it. A `now` or `blocked` phase names
  the items that move it in `items` and, when it has a date to land by, `due`
  (ISO). The card's Moving now box is built from exactly those fields, so a live
  phase without them shows "No job on the board for this phase" in red.
- `status` is `on-track` unless something you can quote puts the target at
  risk (`at-risk`) or past saving (`off-track`). Never mark it `done` until
  every phase is.
- **Step H (`TEAM HUDDLE TODAY`):** When the digest's `SWEEP PLAN` prints step
  `H`, today runs the internal team huddle (`meeting.also` `kind: huddle`) and
  `board.huddle.for_date` is still on a past week. Fetch the huddle's Notion
  page and query its milestone and blocker databases, then write `"board":
  {"huddle": {"for_date": "<today>", "at": "<at>", "built_at": "<checked_at>",
  "milestones": [...], "blockers": [...], "plate": [...]}}` in `tmp/patch.json`
  (only milestones with `in_huddle` not `false` go in `huddle.milestones`;
  internal projects with `in_huddle: false` go in `huddle.plate`).

## 7c. Keep the prioritization current

**Only if `config.json` has a `prioritization` block.** With no block, the digest
prints no step P, there is no card, and this section does not apply: skip it.

When it does: a recurring build prioritization round is run by
`prioritization.who`, who posts the result in `prioritization.channel` (id
`prioritization.channel_id`) at `prioritization.thread_at`, every
`prioritization.every_days` days from `prioritization.anchor`; and a build
nobody raised before it waits for the next round. `./prioritize.py` holds the
dates (from `config.json`); the board's `prioritization` block holds the owner's
builds, and the desk card and the note to the prioritization lead are drawn from
it. Your part is to keep that block true. The profile's `## Kraken routes` says
more about how the round works.

- The digest prints the current `PRIORITIZATION` list (`builds.<n>`), and the
  SWEEP PLAN's `P.` searches the CE projects (`prioritization.ce_projects`) with
  `modified_at_after` from `prioritization.checked_at` so unchanged builds cost
  nothing to re-read. **Keep a build only if its CDL-Owner (the field
  `prioritization.cdl_owner_field`) is the owner, `owner.name`.** Asana ignores a
  CDL-Owner filter on search, so check the field on every hit. Another CDL's
  build never goes in, even one on the same client ticket as the owner's.
- Update changed fields by position (`"board": {"prioritization.builds.0.status": "..."}`)
  or write the whole list as `"board": {"prioritization.builds": [...]}`, one
  record per build: `kt`, `gid`, `url`, `name`, `ticket` (the board ref it
  serves, or ""), `status` (Status (Kraken Cust)), `refinement` (🟢 etc.),
  `size` ("3 SP"), `client_order` (the client's own order field on the build, as
  Asana shows it), `wave`, `committed_on` (ISO date from Committed Date /
  Committed on, or ""), `squad`, `due_on` (ISO or ""), `engineer`, `push`
  (`true` or `false`), `why`, and optional `hold_why`. Drop a build that is
  completed. Keep the order the owner would raise them in: the one with a client
  customer waiting on it first.
- **Check `wave` first: a build already in a `Wave` (or carrying a `client_order`
  or assigned `engineer`) is already scheduled into a delivery cycle and never
  needs raising.** Always copy Asana's `Wave` and `Committed Date` (`committed_on`)
  onto the row so `prioritize.py` marks it `In Wave` rather than `Raise`.
- **Every build to raise must earn a short 1-sentence motivation (`why`), or it
  does not get raised.** Write `why` as **one concise sentence** (under ~150
  characters) stating the concrete customer or operational pain that justifies
  pushing this build in a crowded round (never a story point, engineer name or
  guess). **If you cannot motivate why CE must prioritize it now** (for example,
  a defensive retry follow-up sequenced after an unbuilt primary fix, or a minor
  cleanup already covered cleanly by operations), set `"push": false`, leave
  `"why": ""`, and put a one-line explanation in `"hold_why"` so the card holds
  it back instead of padding the prioritization thread.
- When `P.` says a round has run, read the lead's thread and set
  `prioritization.last_round` to `{date, url, ordered: [KT...], not_pushed:
  [KT...]}`, the owner's builds only. A build ordered there gets its client order
  (`client_order`) from Asana on the next read; do not write one Asana does not show.
- When the owner has already posted the prioritization note in `prioritization.channel`
  for the upcoming round `next_round.date`, set `prioritization.raised` to
  `{round: "<YYYY-MM-DD>", at: "<YYYY-MM-DD HH:MM>", url: "<Slack URL>", kts: ["KT-..."]}`
  so the Prioritization card marks those builds `Raised` and the calendar hides
  `by Raise builds`.
- Set `prioritization.checked_at` to now whenever you wrote the list.
- A CE build's Status is CE's to set: the engineer who finished refinement
  moves it on. A 🟢 build still reading New or Solution Refinement is not a fix
  for the owner and never becomes an item or a step of theirs.

## 8. Clear the alert, or leave it alone

`alert` is the red strip at the top of the page, for something that needs the
owner inside the hour and is not an item. If the board carries one, decide
whether it is still true: the moment somebody has answered, or the thing has
become an item with a number, take it off. Do not write a new one to fill the
space. The line above it, what needs the owner and which item to start on, is
counted from the board by the renderer, so never write a headline or a summary of
the day anywhere.

## 8b. Write the morning brief

`brief` feeds the `This morning` radar card at the top of the work view. The
renderer already computes live external replies since the last working day,
unblocked `todo` items, due chases, Asana Status drift, today's upcoming room
readiness, approaching 7-day client-comment windows, imminent milestone phases,
and prioritization cutoffs straight off the board.

So **never write prose that merely repeats a card, a meeting time, an old `news`
row, or an item already in `To do`.** Only write 1 to 3 lines that add context
the deterministic renderer cannot read off a status field:

- `watch`: a human or external clock the owner should keep in mind today, such
  as a key client or colleague being out of office (`不在` / OOO in Slack or a
  thread), a release freeze window, or a dependency about to land.
- `do`: **only when two or more `todo` items are open with the owner**, one
  short sentence naming which item number to start on first and why that order
  matters. Leave `do` out when zero or one `todo` item is open.
- `big`: only when something genuinely new (today or yesterday) across the
  client or sister programme is bigger than any single ticket. Never copy a
  multi-day-old `news` row into `big`.

Each line is `{"kind", "what", "items", "ref", "source_url", "at"}`, one plain
sentence under 20 words, active voice, zero internal shorthand. Set `for_date`
to today and `built_at` to now, and rewrite `brief` whole every sweep (an empty
`lines: []` is completely fine on a quiet morning because the live radar rows
still draw from the board).

## 9. Keep the shorthand current

`terms` is the vocabulary of the ticket, and it has to carry the words the client
actually say, in their own language (`config.json` `client.language`), because
that is what the owner hears in the room and has to place inside two seconds.
Each row is `term` in English, `say` in the client's language (with readings as
`{word|reading}` on whole words when `language.readings` is on and the profile's
`## Writing to the client` asks for them), and `means` in one or two sentences
that open with what the thing is. A term nobody has used in a fortnight comes
off. Add one the moment the client use a word twice and it is not on the list:
their codes, their words for an interim and a permanent fix, whatever they are
calling the hold this week. The profile's `## Glossary seeds` is a starting set.

`glossary` is the same vocabulary in two or three words, board-wide, and it is
what puts the English meaning in brackets after a client-language term in every
English line on the page. **Every client-language term you write into an English
sentence must be in it.** Write the English plainly and let the page add the
meaning: putting the brackets in by hand is how a term ends up explained twice in
one sentence. Add them in the patch's `"glossary"` key. One missed term is a line
the owner reads without knowing what it says.

## 10. Check your own work

```
./core/check.py
```

The audit, and around it the checks that catch a board no renderer can survive.
**Fix everything it prints with a second small patch and run it again.** A sweep
is not finished while it prints anything, and "nothing new in Asana" is not a
reason to leave it, because none of what it catches comes from Asana. If a line
is genuinely wrong, the check is wrong and belongs in `audit.py`; say so in the
report. `FAIL` rather than `note` is a broken board, and it is yours.

Do not rebuild and do not open the page: the runner does both.

## 11. Say what changed, briefly

Always end with this, even when the answer is dull. A run that finishes silently
is indistinguishable from a run that died, and the log is the only place the
owner can check afterwards.

A few lines on what moved and what it means for the list. When nothing moved,
one line saying so and the time you swept to. Never restate the whole board back
to them.

The rules in `AGENTS.md` and the profile apply throughout, particularly: never
send anything, nothing internal reaches the client, and never draft around a
hold.
