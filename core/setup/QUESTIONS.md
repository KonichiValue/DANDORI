# The setup questions

The setup page asks these as a form (`DANDORI setup` opens it), and `./setup.py`
asks the same ones in the terminal. Both write the answers to `config.json`.
This page says what each one is for, so you can answer it well or edit
`config.json` by hand later. Running `./setup.py` again keeps every answer you
already gave: Enter accepts the one in brackets.

The prose half, how you write to the client and who your people are, goes in
`me/profile.md`. `./setup.py` starts it from `setup/profile.example.md`. The
quickest way to fill it is a Claude chat on this folder: say *"follow
setup/INTERVIEW.md"* and it looks up your Asana and Slack ids, finds your
meetings, and interviews you for the rest.

Both files are in `.gitignore`. Nothing you answer is ever committed.

## 1. You (`owner`)

| Key | What it is for |
|---|---|
| `name` | Your name as Asana and Slack show it. The sweep finds *your* tickets and *your* messages by it. |
| `email` | Your Kraken email. The calendar reader uses it to tell your meetings from shared calendars. |
| `role` | Shown to the agent so it writes as you. |
| `workstream` | What your tickets are about (billing, payments, migration). It also marks a calendar meeting as work. |
| `asana_user_gid` | Your Asana user id: Profile, the number in the URL. Optional; the first refresh looks it up. |
| `slack_user_id` | Your Slack member id (starts with U): Profile, the three dots, Copy member ID. Used to find your own sent messages, which is how the desk notices you already did something. |

## 2. The client (`client`)

| Key | What it is for |
|---|---|
| `name` | The client's name. |
| `short` | Two to four letters for the page tabs, *"EE my work"*, and for the holder of a job sitting with the client. |
| `domains` | Their email domains, so a meeting with them counts as work and as a room you speak in. |
| `language` | The language you write to them in (`en`, `ja`, ...). Drafts to the client are in it; drafts to Kraken are in English. |
| `teams` | Optional. The client's sub-teams as they appear in names (`EE Payments`), so a person is filed under the client rather than read as a team of their own. |
| `support_url` | Their Kraken support site account page, with `<account>` where the number goes. The agent opens one account there rather than querying. |

## 3. Where and when

| Key | What it is for |
|---|---|
| `timezone` | Your working timezone (`Asia/Tokyo`, `Europe/London`). Every date on the page, every "today", and the schedule all use it. |
| `models.region` | Which Kraken LiteLLM region your models run in: `eu`, `us` or `apac`. The desk builds ids like `claude-low-carbon-eu-opus-5-5`. See hub.ai.ktl.net/models. |

## 4. Asana (`asana`)

| Key | What it is for |
|---|---|
| `workspace_gid` | The Asana workspace. Optional. |
| `projects` | The projects your tickets live in, each `{gid, name}`. Paste the project URL; the setup keeps the number. Every sweep runs an *assigned to me* search across these, so a ticket newly given to you appears on the board. |
| `exclude_projects` | Projects to ignore even when a ticket of yours is in them (an onboarding list, a personal list). |

A ticket goes on the board because Asana assigns it to you, never because it
looks important. That rule is the desk's, not a setting.

## 5. Your main standup (`meeting`)

| Key | What it is for |
|---|---|
| `name` | The meeting as your calendar names it. The desk writes the script for it, and the post-standup run folds its notes in. |
| `time_local`, `minutes`, `days` | When it is. The fold-in starts as it ends and keeps looking for the notes for 45 minutes. |
| `notes_title` | The title the notes are saved under (Notion meeting notes, a Gemini doc), if not the meeting name. |
| `also` | Other recurring meetings you speak in, each `{name, time_local, days, kind}`. They appear on the week strip and in the sessions list; their notes are read by the next refresh. |

Each `also` entry takes a `kind`, which decides how the desk treats it:

| `kind` | What it is |
|---|---|
| `weekly` | A weekly review with the client. It gets its own tab on the speaking view. |
| `rollup` | A programme-wide meeting (a migration daily, a cross-workstream rollup) where you report a line or two. |
| `huddle` | The Kraken-side team huddle. |
| anything else | A room of its own, named by `name`. |

`record` says which board record proves its notes were read. Leave it out and a
refresh reads the notes into the programme record (`migration_note`); set it to
`""` for a room whose notes you never need folded in. `tab` and `what` override
the short label and the description the page uses.

## 6. Slack (`slack`)

| Key | What it is for |
|---|---|
| `channel_hints` | Channels where your tickets are discussed. A sweep reads every thread already on a ticket wherever it is; these are where it searches for new ones. |
| `search_scope` | Leave as `all`. |
| `form_channels` | Channels that only take requests through a Slack workflow form, for example an engineering team's intake channel. Each is `{"channel": "C0...", "name": "team-intake", "form": "Ask the team", "fields": ["What is this about?", "Priority:"]}`. A new message there is drafted in the form's fields, and `./audit.py` lists any draft that skips one. A reply in an existing thread is an ordinary message. Leave empty if none. |

## 7. Writing to the client (`language`)

Only asked when the client language is not English.

| Key | What it is for |
|---|---|
| `level` | Your level in it (N2 spoken, B2). Scripts you read aloud are pitched for it. |
| `register` | The register, in your words (ですます, plain not stiff keigo). |
| `readings` | Whether lines carry `{漢字|かんじ}` reading markup the page draws as ruby. `./check.py` checks hard words have one only when this is on. |
| `reading_terms`, `plain_runs` | For the reading check: words that must always carry a reading, and runs that never need one. Fill these in as the check complains. |

## 8. Production data (`database`)

Optional. If you have a read-only replica MCP for the client's production
database, the desk can count how many accounts a problem touches.

| Key | What it is for |
|---|---|
| `mcp` | The MCP server's name. Empty means none, and nothing asks for it. |
| `command` | The binary that serves it. `./setup-mcp.sh` adds it to Claude Code. |
| `conn` | Its connection name, passed as `KTDB_CONN`. |

## 9. The app (`desk`)

| Key | What it is for |
|---|---|
| `name` | The app's name in the Dock and the page title. |
| `command` | The terminal command, linked beside `DANDORI`. |
| `port` | The local port the page runs on. Change it only if 8787 is taken. |
| `morning_sweep` | When the optional weekday morning sweep runs (`DANDORI morning` turns it on). |

## 10. Prioritisation round (`prioritization`)

Optional. Some programmes have a lead who posts the build order on a fixed
cycle, and the working day before it is the last chance to raise a build. With
this block the desk draws that round on the week strip and keeps a note to the
lead ready to copy. Without it, nothing about rounds appears.

| Key | What it is for |
|---|---|
| `who`, `slack_user_id` | The lead who posts the order. |
| `channel`, `channel_id` | Where it is posted. |
| `anchor`, `every_days`, `thread_at` | One round's date, the cycle length, and the time it is posted. |
| `ce_projects`, `cdl_owner_field` | The Asana projects builds live in, and the custom field that names their CDL owner, so only your builds are listed. |
| `holidays`, `moved` | Days a round moves off, and any one-off moves. |

## Per-job settings

`ask`, `refresh`, `prep` and `post` each take `model` and `effort`. Leave
`model` out to use Sonnet 5.5 for refresh and prep, and Opus 5.5 for ask and
post, in your region. `DANDORI_REFRESH_MODEL` and friends override them for one
run.
