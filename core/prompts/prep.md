# Write the prep for the next session

The owner presses **Write prep** when they sit down, and this is what runs. It has
two halves: bring the board up to date, then write what they say out loud into it.

The block above this prompt, *Who you are working for*, is the owner's settings
and profile. It names the client, the rooms, the people, the house style and the
standing rules. Where it and this prompt disagree about any of those, it wins; the
mechanics below do not change. Below, "the profile" means `me/profile.md` as that
block quotes it, and a key such as `client.short` or `meeting` means that key in
the `config.json` it quotes.

The owner has a few minutes with what you produce, and will be speaking to the
client, in the language `config.json` `language.client` names, within the hour.
Everything you write is for someone who needs to sound informed and specific,
out loud, without rehearsing.

## Stop rather than improvise

You need the Asana and Slack tools. If they are missing, or answer with an
authentication error, **stop immediately**, change nothing, and reply with one
line naming what is missing. Never call those HTTP APIs yourself with `curl`,
never invent an API token, and never start another agent: not the `Task` tool,
not `claude`, not `cursor-agent`, not `DANDORI prep` (or whatever `config.json`
`desk.command` names). A source you could not read is a source you do not write
about.

## The shape of a fast prep: read once, call in parallel, write once

1. **Turn 1: `./core/digest.py --full`, once.** The whole board, unclipped, with the
   SWEEP PLAN and the WRITE block. It is the only read of the board for both
   halves. Never re-run it, never read `desk.md` or `board.json` on
   top of it, never read `core/prompts/refresh.md` (the run cannot, and does not need
   to: the sweep is below).
2. **Turn 2: every source call the plan names, in one message**, as parallel tool
   calls: they do not depend on each other. Then the deep reads they earn,
   together, in the next.
3. **Write once.** The sweep's findings and every `prep` block go in one JSON
   patch, written with the Write tool to `tmp/patch.json` and applied with one
   `./core/apply.py tmp/patch.json`. A `prep` block is
   `"tickets": {"<ref>": {"prep": {...}}}`; `script`, `sessions`, `xws` and
   `huddle` go under `"board"`. If apply refuses, it says why and writes nothing:
   fix that part of the patch and run it again.
4. **`./core/check.py`, a second small patch for what it prints, then report.**

A record you need whole (a `prepared` block, a draft) is
`./core/digest.py --item <n>` or `--ticket <ref>`, never a read of `output/` or a `.py`.
Banned outright: Python against the board (`python3 -c`, `import board`,
`help(board)`, reading any source), `git`, `./core/tick.py --rebuild` (the runner
redraws both pages when the run ends), and subagents: no `Task` tool, no
`claude`, no `cursor-agent`, no handing a slice of the work to anyone.

## Half one: bring the board up to date

**If `checked_at` is within the last ~15 minutes, a sweep just ran.** Run only
the gate (`modified_at.after=checked_at`) and the threads the plan still lists,
fold in what they turn up, and go to the script.

Otherwise sweep, briefly, in the same turns as above:

- **Census and gate together.** The census is every incomplete task assigned to
  the owner (`config.json` `owner.asana_user_gid`) in every project in
  `config.json` `asana.projects`, no date filter, with
  `opt_fields=completed,assignee.name,memberships.section.name,custom_fields.name,custom_fields.display_value`.
  It decides membership and refreshes every card's `asana` fields (Status is the
  custom field the profile's `## Where my work lives` names as the status, `Status`
  unless it says otherwise). An assigned gid not on the board is added as a
  ticket; nothing unassigned ever is, however important (that is a `news` row).
  A board ticket missing from the census was completed or reassigned: find out
  which. The gate (`modified_at.after`, `opt_fields=name,modified_at,assignee.name,completed`)
  decides which tickets earn `get_task_stories`.
- **The milestone search** (`M.` in the plan): bring its child-ticket rows and
  phase states up to date, a child assigned to the owner becomes a card, and
  nothing else does.
- **The threads the plan names**, forward from the time it names. Every thread
  you open goes in the patch's `"checked"`; one with a newer message goes in
  `"threads"` with `last_at` and `last_from`.
- **What you find** goes in as `events` (`on`, `at`, `who`, `what`, `so_what`,
  `where`, `source_url`; the owner is "You"), and items move only on an event you
  can quote: a reply brings an item back to `todo` on the same number with its
  draft rewritten; something they sent makes it `waiting` on a named person with
  a `chase_on` date. New work goes in `new_items`. Every job needs a day:
  `urgency` exactly `today`, `this-week` or `monitor`, or an ISO `chase_on`.
- Fix every line of the digest's `OUT OF DATE` block, update `news` (route named,
  five at most) and `brief`, and end the patch with `"swept": true`.

That gives you a current board: every open ticket in the owner's workstream
assigned to them, every item of work on it, and the timeline of how each got
there.

## Half two: what they say

Only the speaking half is yours to write now. Do not move item states beyond
what the sweep did, and do not rewrite `events` or `where_it_stands` beyond it.
Reading the board per ticket to compose is the flail that runs prep past its
budget: compose from the one `--full` read.

A milestone the owner leads may carry `say`: one or two client-language lines in
the profile's house style for the room on where the programme stands, written
only when the session will discuss it
(`"board": {"milestones.<id>.say": ["..."]}`). Nothing internal: no ticket links,
sizes or engineer names.

The Asana workspace, the project GIDs, the owner's user GID and the Slack channel
hints are in the `config.json` quoted above (`asana`, `owner`, `slack`).

### Which room are you writing for

Read `sessions` on the board before anything else and write for the soonest one
that is not `skipped`. Set `script.for_date` to its date. Everything below is
written for a standup unless the session says otherwise, and an onsite is not a
longer standup.

Keep `sessions` true as you go. The last meeting note or a Slack announcement may
have moved something, added an onsite, or cancelled a standup. When you learn of
a session, write it in with its `kind`, `date`, `at` when known, a `focus`
sentence saying what the session is actually for, and, for anything that is not a
standup, the `agenda` and what the owner has to `bring`. A standup that is not
running stays in the list with `skipped` and a `reason`, because "no standup
Wednesday" changes what has to move into Asana instead.

**A day can have two rooms.** If the profile's `## My rooms` (or `config.json`
`meeting.also`) names a weekly review with the client on the same day as the
main standup in `config.json` `meeting`, it says which tickets each room walks:
typically the standup takes the top-priority and new tickets, and the review
(`kind: weekly`) takes the rest, which may be where most of the owner's are
actually discussed. Once the standup's time has passed, write for the review, and
set `script.at` to the review's time so the page picks it over the standup on the
same date. A weekly gets onsite depth for a contested ticket (`decisions`,
`pushback`), but it is an hour, not a day: three or four blocks at most, and a
quiet ticket still gets one where-it-stands block. A ticket the morning standup
did not reach is not settled by it; carry its question into the review unchanged.
With no such room in the profile, there is one room a day and this paragraph does
not apply.

**When the session is an onsite**, four things change:

1. **Depth.** A standup is a short slot for the whole board (`config.json`
   `meeting.minutes`) and the owner gets two sentences per ticket. An onsite is
   a day, the client is in the room, and thin preparation shows. Up to six script
   blocks per ticket, and cover the ground properly.
2. **`prep.decisions`.** What has to be settled before people leave the room, why
   it cannot wait, and the fallback if the client will not settle it today. These
   print in **What I need back** alongside the open questions and anything already
   owed to the owner, one list per ticket, so a decision written vaguely reads as
   a question and loses its force. This is the point of an onsite: decisions that
   die in a standup because there is no time.
3. **`prep.pushback`.** The objection you can see coming, and the client-language
   sentence that answers it. Take these from what the client has actually said in
   the threads, not from imagination. Two or three per contested ticket, none for
   the quiet ones.
4. **What to bring.** Anything the owner promised to have ready, or that the
   argument cannot be had without, goes in the session's `bring` list. Check
   their own commitments in the threads: promising to bring something and
   arriving without it is the failure mode.

### The meeting rollups, each only on its own day, and only if the owner has one

The standup walk (below) is written every prep run. The meeting **rollups** are
not. A rollup exists only if the owner has the meeting it is for: the extra
meetings listed in `config.json` `meeting.also`, described in the profile's
`## My rooms`. With no such meeting, never write the block. When there is one,
it is written only when prep runs on **that meeting's day**, so pressing Write
prep on a Tuesday does not churn a Friday rollup. Check today's weekday and touch
only the rollups that fall on it:

- **`xws`**: a programme-wide rollup, where the whole programme (several
  workstreams) meets in one room and only the biggest items from the owner's
  workstream are ever taken up. Write or update it only on that meeting's day;
  on any other day leave the `xws` block exactly as it is.
- **`huddle`**: an internal team huddle, a short Kraken-internal update in
  English. Write or update it only on that meeting's day; otherwise leave the
  `huddle` block as it is.

For a daily meeting the day before is also acceptable; for a weekly one, only the
day itself. On any other day, write just the standup walk and leave every rollup
untouched.

The standup is the room the owner walks ticket by ticket; a rollup is the whole
programme (`xws`) or their own status line (`huddle`) in one room, not a second
copy of the walk. Set `script.for_date` to the **standup** and write the
per-ticket `prep` for it as always. Do **not** put a rollup in `sessions`: each
lives in its own block, so the full walk stays pointed at the standup and each
renders as its own card.

Into `xws` put its `for_date`, `at` (the meeting's time from `meeting.also`),
`built_at`, a one-line `headline` saying what the owner raises at the rollup this
week, and `raised`: the shortlist. A ticket earns a place in `raised` only if the
whole programme needs it, which is a high bar. It clears the bar when it is a
customer-visible incident resolved or still live, a production release the
programme has to hear about (the profile may say where production releases are
pre-announced), or a decision stuck long enough that it needs escalating above
the standup. Most tickets never appear. Two or three items is a normal week; zero
is a real answer, and `headline` then says so ("nothing from the workstream needs
the room this week") with `raised` empty.

Each `raised` entry is `{ref, why, say}`: `why` is one line on why it is big
enough for the room, and `say` is one or two lines the owner actually says,
higher altitude than a standup line, in the same `{ja_ruby, en}` shape, with
readings when the profile asks for them. The hard constraint below holds here
too whenever the client sits in that meeting, which the profile says: then
nothing internal reaches the room, even though it is a programme meeting.

The **`huddle`** block is the team huddle update, and it is **English and
internal** (Kraken side, the client is not in the room), so no reading markup and
the "nothing internal reaches the client" rule does not apply. Keep it as short as
the owner writes it themselves: `for_date`, `at` (the meeting's time),
`built_at`, `plate` (a terse one-line-per-item list of what is on their plate
right now, client-language ticket names fine), and `blockers`. **Be very
selective on `blockers`:** only a genuine blocker belongs there, something
actually stuck on another person or an unmade decision; most weeks it is one line
or none, and "No blockers" is the honest answer far more often than a list. Match
the bullet style of the owner's latest written update, wherever the profile says
they keep it. Each entry in `plate` and `blockers` is a plain string.

### The order the meeting walks

The standup works down the client's board in the order the cards appear on
screen, and the owner follows along live. If the profile's
`## Where my work lives` names the Asana section the standup walks (for example a
cycle board's current-priority section), call `get_tasks` with that `section` and
`limit: 60`, which returns tasks in board order. Set each ticket's `prep.order` to
1, 2, 3 by that position, and `prep.board_position` to something like
`"5 of 12 on the board"` so the owner knows roughly when they are up.

If that section GID has moved because a new cycle started, find the section of
the same name on the project the profile names and use that. With no section
named, order by the board's own priority and leave `board_position` out.

### What moved since the last meeting

The prep page prints each ticket's recent `events` under "What moved since last
time", so the paragraph the owner needs before the meeting is already there if the
timeline is honest. Two things follow from that. Every event since the last
session must be on the ticket, the owner's own messages included, and `so_what`
must be filled on any event that changed what happens next, because that line is
what they read out. Nothing is written twice: do not repeat the timeline inside
`where_it_stands`, which stays one short paragraph on the current position.

### Do not re-script what the last meeting already settled

**A ticket completed in Asana is finished: give it no `prep` block, whatever its
timeline shows.** A closed client ticket is done, not a speaking item, so it never
gets a script line and needs no update when the script is written. It stays in
the closed fold as a record.

A ticket earns a place in the script only when the **next** room has something
still to do with it: a status the client has not heard, a question to ask, a
decision to land. If it was talked through at the last session and came out with
nothing owed back from the client, only an action for the owner to carry out
afterwards (post the agreed update to the ticket, attach evidence once a run
confirms it), then it is **finished as a speaking item**. Give it no `prep` block
at all. It stays on the desk as the item it is, and the speaking view is silent on
it.

The timeline is how you tell, not the ticket's status. An event like "gave the
update at the standup, agreed to post the evidence in the ticket" means the room
is done with it, even though the ticket is still open and the action is still
the owner's to do. So read what the owner actually said last time before you
write a where-it-stands line, not just where the work stands.

Re-reading out a matter the room already closed is worse than leaving it off: it
reads as unresolved, invites the same discussion a second time, and buries the
one or two tickets that genuinely need the room.

This is different from a ticket sitting quietly with Kraken engineering, which
still earns its one-line where-it-stands block because the client has a live
interest in hearing it is in hand and has not been told since. The test is whether
anything is still owed **in the room**: nothing owed, and last time already
covered it, means no block.

**It reopens only when something new happens.** If the recovery fails, the
evidence is contested, the client raises a fresh question, or a promised date
slips, it earns a slot again with the new fact as its where-it-stands block. A
settled item is silent on the speaking view, not deleted from the board.

### Trace what the fix does not cover

This is the step that separates a useful script from a status readout, so do it
for every ticket, properly. Most fixes are scoped: they fix one case out of
several. Reading the ticket tells you what it covers. Your job is what happens to
everything it does not, because that is what the owner gets asked.

Write the chain into `prep.consequences`:

1. **What does the agreed fix cover?** Name the cases in the team's own numbering.
2. **What falls outside it?** Name those too.
3. **What accumulates?** If a hold or a manual step keeps firing on the uncovered
   cases, accounts pile up somewhere. Say where, and say whether the pile grows
   or is a fixed legacy set. The client will make that distinction.
4. **Who owns the pile?** Somebody reviews or clears it. If the ticket does not
   say, that is a real question and a good one.
5. **What does done mean for the uncovered cases?** If a cleanup has been asked
   for, pin down which accounts, which cases, who executes.
6. **What decision is genuinely still open?** Not "we should investigate", but a
   fork with two named options and nobody assigned to choose.

Anything only the client can answer becomes an `open_questions` entry. Anything
the owner can answer themselves becomes an `unknowns` entry.

### The script itself

Every ticket gets `prep.script`: the actual sentences the owner will say, in the
client's language. This is speech, not a translation of your English summary.

The blocks, in order, each skipped when it does not apply:

1. where it stands (`heading_en` "Where it stands"),
2. what we found since last time,
3. what Kraken proposes,
4. what the owner needs from the client,
5. what the owner needs answered.

`heading` is the block's name in the client's language, as the profile gives it,
and `heading_en` is the English. For a Japanese-speaking client the page's own
conventions are `現状`, `わかったこと`, `提案`, `お願い` and `質問`: the speaking
view recognises a `質問` block as the spoken questions, and `./check.py` treats a
block with no heading as `現状`. Use those names unless the profile says
otherwise.

**At a standup the default is one where-it-stands block, and most tickets should
stay there.** The owner is walking a whole board in a short slot, so what they
need per ticket is where it stands right now, in one or two sentences, and then
whatever they have to ask or tell the client. A ticket is not a presentation to be
given: it is a line of status, plus an ask when there genuinely is one. The full
run of blocks above is a ceiling for a contested ticket, not a shape to fill for
every one. If you find yourself writing a findings block and a proposal block for
a ticket nobody is arguing about, you are presenting where you should be
reporting.

**A ticket sitting with Kraken engineering is one or two lines, no more.** When
the latest is that the requirements are agreed and Kraken is building it, that is
the whole of the where-it-stands block: one sentence saying the requirements are
agreed and Kraken is working on it, in the client's language, and stop. That one
line is still a line the owner reads out, so it follows the profile's house style
and carries its readings like any other (see below); a quiet ticket is the one the
markup is most often dropped on. Add an approximate timing **only if a date has
already been agreed and shared with the client** (`prep.estimate`); if none
exists, say nothing about when, rather than hedging. Never reach for the build
ticket, the queue position, the engineer or the size to pad it out: those never
reach the client (see the hard constraint below), and the honest one-liner is the
right answer, not a thin one.

Style: follow `## Writing to the client` in the profile for language, register,
sentence length and house vocabulary. What holds whatever the language:

- **Register stays plain-polite and spoken rather than stiff written prose.** These are working
  conversations with people the owner speaks to most days. Stay polite, but cut
  the padding and stiff report structures that make a line hard to say aloud. This is register only, and never
  touches the vocabulary rule below: the technical terms are kept exactly, never
  simplified. The profile gives the forms to prefer and to avoid.
- **Connect clauses into natural spoken sentences, straight to the ticket content.**
  Do not spend a separate sentence just announcing the topic ("About X.") and then
  chop the update into three disconnected lines. Fold the topic into the opening
  clause and link related thoughts naturally the way a person speaks aloud, so a
  standup update is one or two flowing sentences that get straight to the point.
- **Make spoken questions self-contained.** Anyone in the room who does not have
  the ticket memorized should immediately understand what behavior or decision is
  being asked about before the question lands.
- **Use the exact vocabulary the client and the team already use.** Never
  simplify a term into something more basic: the client has to recognise what the
  owner is talking about. Keep every term the profile's house vocabulary names and
  anything else lifted from the ticket or the thread.
- Cut padding, not substance. Drop repeated greetings. Never write a line that
  only restates the one before it.
- One to three connected lines per block. Four blocks per ticket at a standup and six at an
  onsite are ceilings for a contested ticket, not targets: a quiet ticket is one
  where-it-stands block, often a single line.

**Readings, only when the profile asks for them** (`config.json`
`language.readings` true). Then a word the owner may not read at sight is written
as `{word|reading}`, reading on the whole word, never per character, and the
profile's `## Writing to the client` says which words need one and which the owner
knows. This holds for **every** `ja_ruby` and `say_ja` you write, the one-line
quiet where-it-stands block as much as a six-block onsite, and for the standing
vocabulary the profile names: each takes its reading when it lands in a spoken
line. The bald compound is the failure this desk has seen most, so `./check.py`
fails on a run of three or more un-annotated kanji in a spoken field: if it flags
a line, the reading is missing, not the check wrong.

Correct: one pair over the whole compound, `{word|reading}`.
Wrong: one pair per character.

**Only the script that needs it.** Never wrap text that already reads itself (for
Japanese: katakana, hiragana or latin text). A reading printed above it is noise
on a line the owner is speaking at speed. The renderer drops these, so writing
them only wastes your output.

**Get the reading right.** A wrong reading is worse than no reading, because the
owner will say it out loud. If you are not certain of a word, leave it unwrapped.

With readings off, write the plain text and no markup at all.

The client-language line goes in `ja_ruby` (and `say_ja` in pushback) whatever
the language: those are field names, not a statement that the client speaks
Japanese. Every line needs a natural English translation in `en`. Translate the
meaning, not the grammar.

### Asks: earn them, do not invent them and do not miss them

Before deciding a ticket needs nothing from the client, check your consequences
chain. If the fix is scoped and you have not established who owns the uncovered
cases or what cleanup means, there **is** an ask and you have not found it yet.

Set `client_ask_needed` (the field name for "the client is asked something", whatever
the client) to `false` only when the chain came back genuinely clean: the fix
covers everything, or the leftovers have a named owner and an agreed definition of
done. Then give the ticket a where-it-stands block of one or two lines and stop: a
ticket sitting with Kraken engineering with nothing outstanding is a good thing to
report in one breath, not a section to expand.

Never invent a question to fill the questions block. A weak question wastes
standup time. A missing one is worse: the client raises it instead and the owner
answers cold on their own ticket.

**Do not say the same thing twice.** A question the owner asks the client out loud
belongs in the questions block and nowhere else. `open_questions` is only for asks
that do not belong in the spoken script: something a Kraken colleague owes the
owner, or something they have to settle themselves. The page hides a client
question that is already in the script, so writing it in both places only wastes
your output.

Where a delivery estimate exists **and has already been shared with the client**,
put it in `prep.estimate`. If none has been agreed, leave it out rather than
hedging about timing.

### What has to be spoken rather than only done

Some items on the board are only closable in the meeting: a question the client
owes an answer to, something the owner promised to raise, a decision that needs
both sides in the room. Set `at_standup: true` on those items, with a short
`at_standup_note` saying what the owner needs out of it. They then show on both
views, and the standup view lists them under the ticket. Leave every other item
alone.

When an item has `prepared.meeting_use`, its evidence stays on the desk and its
meeting-ready conclusion, ask and response to pushback go in this ticket's
`prep`. The item links to that version. Never leave the owner to translate an
analysis table into spoken words in the room, and never mix a stakeholder's
desired outcome into a table of query conditions.

### Hard constraint: nothing internal reaches the client

Internal Kraken build tickets, story points, t-shirt sizes, refinement status,
build-queue position, engineer names and delivery estimates must **never** appear
in `prep.script`, in an `open_questions` entry aimed at the client, or in any
draft targeted at the client. The client cannot see those tickets and does not get
told what is queued. The profile's `## Never reaches the client` adds whatever
else the owner's programme holds back, and wins where it says more.

What the owner may say is the shape of the work: requirements are agreed, Kraken
is working on it, here is what will change. Nothing about when or how big. Keep
the internal detail on the desk side, in item `why` and `steps`, which is for the
owner.

## What you write into the board

Per ticket, one `prep` object, replacing whatever was there:

```json
"prep": {
  "order": 1,
  "board_position": "5 of 12 on the board",
  "issue": ["Two or three lines of plain English. No jargon, no client-language terms.",
            "Someone who has never seen this ticket understands it here."],
  "why_it_matters": "One sentence on the customer or business impact.",
  "consequences": {
    "fix_covers": "", "falls_outside": "", "accumulates": "",
    "who_owns_it": "", "done_means": "", "still_open": ""
  },
  "open_questions": [
    {"en": "The question in English.",
     "ja_ruby": "The question in the client's language, with {word|reading} markup when readings are on.",
     "who": "the client's short name (client.short) | Kraken CE | the owner"}
  ],
  "script": [
    {"heading": "Where it stands, in the client's language (現状 for a Japanese client)",
     "heading_en": "Where it stands",
     "lines": [{"ja_ruby": "...", "en": "..."}]}
  ],
  "decisions": [
    {"need": "What has to be agreed today.",
     "why": "Why it cannot wait for the next standup.",
     "fallback": "What the owner asks for instead if they will not settle it."}
  ],
  "pushback": [
    {"they_say": "The objection, in English, taken from what they have said before.",
     "say_ja": "The answer in the client's language, with {word|reading} markup when readings are on.",
     "say_en": "The same answer in English."}
  ],
  "client_ask_needed": true,
  "estimate": "Only if already agreed with the client. Otherwise omit.",
  "unknowns": ["What the owner should check before they speak. Also anything they must not say."],
  "built_at": "ISO 8601 with the offset of config.json timezone"
}
```

`decisions` and `pushback` are for an onsite. Leave them out for a standup.

And once at board level:

```json
"script": {
  "for_date": "YYYY-MM-DD, the session you wrote for",
  "at": "the session's time, meeting.time_local for the main standup",
  "built_at": "ISO 8601 with the offset of config.json timezone",
  "headline": "One sentence naming what this room has to produce. It sits under the session name on the prep tab, so it must read to someone who has opened nothing else: name the ticket and name the decision. Never a recap of what changed, and never a verdict on how big the problem turned out to be."
},
"sessions": [
  {"kind": "onsite", "date": "YYYY-MM-DD", "at": "", "title": "Workstream onsite",
   "place": "", "focus": "What this session is for, in one or two sentences.",
   "agenda": [{"topic": "", "why": "", "owner": ""}],
   "bring": ["Anything the owner promised to have ready."]},
  {"kind": "standup", "date": "YYYY-MM-DD", "at": "meeting.time_local",
   "skipped": true, "reason": "Why not, if it is not running."}
],
"xws": {
  "for_date": "YYYY-MM-DD, the rollup's day",
  "at": "the rollup's time from meeting.also",
  "built_at": "ISO 8601 with the offset of config.json timezone",
  "headline": "What the owner raises at the rollup this week, or that nothing is big enough.",
  "raised": [
    {"ref": "the ticket tag",
     "why": "One line: why this is big enough for the whole programme.",
     "say": [{"ja_ruby": "One or two rollup-level lines, with {word|reading} markup when readings are on.",
              "en": "..."}]}
  ]
},
"huddle": {
  "for_date": "YYYY-MM-DD, the huddle's day",
  "at": "the huddle's time from meeting.also",
  "built_at": "ISO 8601 with the offset of config.json timezone",
  "plate": ["one terse line per thing on the owner's plate now"],
  "blockers": ["only a genuine blocker; usually one line or none"]
}
```

`xws` and `huddle` are written only when the owner has those meetings, and only on
their day. Neither is in `sessions` on purpose: each is a rollup card, not a walk.
Leave `raised` empty when nothing clears the bar, and let `headline` carry the
week.

Do not write `where_it_stands` inside `prep`. The speaking view reads it from the
ticket, so there is one status and the script can never contradict the desk.

Every `en` line under a client-language one is what tells the owner what they are
about to say, so a client-language term left sitting in it has not been
translated. **Any term you leave in an English line must be in the board's
`glossary`**, two or three words, and the page puts the meaning in brackets after
its first mention in the script. Add what is missing rather than writing the
brackets yourself, and never gloss inside `ja_ruby` or `say_ja`: the owner reads
those out to the client. The profile's `## Glossary seeds` lists terms the owner
already knows the client uses.

## Then check and report

Run `./core/check.py` and fix what it prints with a second patch. Do not rebuild: the
runner redraws both pages when the run ends.

Finish with a few lines: which session you wrote for, which tickets have an ask
on the table, which are status only, and anything the owner should check before
they speak. Always write this, even when the answer is dull, because a silent run
cannot be told apart from a dead one. Never restate the whole script back to them.

The rules in `AGENTS.md` apply throughout, particularly: never send anything, and
never draft around a hold.
