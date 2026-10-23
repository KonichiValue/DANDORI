# Set up this DANDORI for its owner

You are in a chat on a fresh copy of DANDORI. The person talking to you is
its new owner, a Kraken Client Delivery Lead. Your job is to leave them with a
working `config.json` and a `me/profile.md` that makes every later run of the
desk write as well for them as it did for the person who built it.

Never send anything to Slack, Asana, Notion or email while you do this. Read only.

## 1. See what is already there

- Run `python3 core/settings.py`. If the client `config.json` exists, read it, and read
  the client `profile.md` if it exists: you are improving them, not starting over.
- If there is no `config.json`, run nothing interactive yourself. Ask the owner
  to run `./core/setup.py` in the terminal first (it takes two minutes and writes the
  file), or, if they would rather do it all here, copy
  `core/setup/config.example.json` to `clients/<short>/config.json` and fill it with them.
- `core/setup/QUESTIONS.md` explains every key. Read it.

## 2. Look up what you can instead of asking

Use the connected tools. Ask the owner only what you cannot find.

- **Asana**: their user gid (`get_me` or the users lookup), the workspace gid,
  and the projects their open tickets sit in (search tasks assigned to them,
  group by project). Show the projects and ask which ones are the client's,
  and which to leave out (onboarding lists, personal lists).
- **Slack**: their user id (`slack_search_users` on their name), and the
  channels where their tickets are discussed (search their recent messages,
  group by channel). Ask which are client-shared and which are internal.
- **Google Calendar**: the main standup with the client (name, days, time,
  length) and the other recurring work meetings. Ask which one is "the" standup.
- **Notion or Drive**: where that standup's notes land, and the note title.

Write every answer into `config.json` as you get it. Never `json.dump` over
the file without reading it first, and keep keys you were not asked about.

## 3. Interview for the profile

Then go through `setup/profile.example.md` heading by heading and ask, a few
questions at a time, in plain words. Start from what you found in step 2 ("you
seem to talk to the client in #ext-..., is that right?"). Things that matter
most, because the desk writes on the owner's behalf:

- the client's language, register and house vocabulary, with two or three
  real sentences the owner has sent that they were happy with;
- what must never reach the client;
- who the owner routes work to at Kraken, and how those people like to be asked;
- the lessons the owner has already learned on this account.

Write `me/profile.md` with the exact headings of the template. Write it as
instructions to an agent, in the owner's terms, quoting them where the wording
matters. Keep what they said; do not pad.

If `language.client` is not English, also fill `language` in `config.json`:
`readings` true only if the owner wants reading markup over hard words, and
`reading_terms` / `plain_runs` only if they name some.

## 4. Check and hand over

- `python3 core/settings.py` must print the owner, the client, the meeting and the
  database line correctly.
- `./core/scripts/setup-mcp.sh --check` shows which connections are live.
- `./core/check.py` must pass.
- Tell the owner, in three lines, what is configured, what is still a guess,
  and the next step: `./install.sh`, then press Refresh on the page.
