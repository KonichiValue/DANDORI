# My DANDORI profile

Every agent the desk runs reads this file before it writes a word, and an
interactive chat on this folder is told to read it first. Write it as
instructions to the agent: plain sentences, the way you would brief a new
colleague who is going to draft your messages. Leave a section with its
placeholder if it does not apply; delete the placeholder lines you have
answered.

The headings are fixed: the prompts refer to them by name.

## Me

- My name, role and workstream: _e.g. "Alex Example, Client Delivery Lead for billing"_.
- The languages I work in and my level in each: _e.g. "English native, Japanese N2 spoken, N3 reading"_.
- How I like answers in chat: _e.g. "terse, lead with what matters, no em dashes"_.

## The client

- Who they are, in one or two sentences, and what Kraken does for them.
- Their short name on the page: _e.g. "EE"_ (also `client.short` in config.json).
- Email domains that mean a person is from the client.
- Their support site and how a single account's page is addressed.

## Where my work lives

- The Asana projects my tickets live in, and what makes a ticket mine
  (assignee, a custom field, a tag).
- Which Slack channels the client and Kraken talk in, and which are internal only.
- Where specs or notes live in Notion or Google Drive, if anywhere.

## My rooms

- The main standup: name, days, time, length, who attends, which language.
- Where its notes land (Notion meeting notes, a Gemini doc, nowhere) and what the
  title looks like.
- Other recurring meetings that matter (a weekly review, a programme-wide daily),
  and which ones I speak in.
- Onsites or long sessions: how often, and how much preparation they need.

## People

- Client contacts: name, role, how to address them (Sato-san, 佐藤さん), what
  they care about.
- Kraken colleagues I route work to: name, team (CE, IE, Core, Markets), what
  they own, how they like to be asked.
- Anyone whose word overrides a meeting decision.

## Writing to the client

- Language and register: _e.g. "Japanese, ですます, plain not stiff keigo"_.
- Sentence length and shape.
- House vocabulary: the client's own terms, never simplified substitutes.
- Readings: whether lines carry `{漢字|かんじ}` markup, and above which level.
- The standard caveat line when AI helped with an answer, if there is one.
- Any phrasing to avoid.

## Never reaches the client

- Standing confidentiality rules: _e.g. "no story points, t-shirt sizes,
  refinement status, build-queue position, internal Asana links or Kraken
  engineer names; write 'the engineers'"_.
- Who set the rule, if it matters.

## Kraken routes

- How a build gets raised, refined and prioritised for this client.
- Who runs the prioritisation round, where and how often (also
  `prioritization` in config.json, if there is a round).
- How to escalate, and to whom.

## Data I can reach

- The read-only replica MCP (`database.mcp` in config.json), what it covers and
  what it does not.
- The support site URL pattern for one account.
- A local codebase checkout to read, if any: _e.g. "~/Projects/kraken-core, read
  its AGENTS.md first"_.
- What never to query.

## Lessons from my work

- Rules learned the hard way about this client or workstream. One bullet each,
  with the reason.

## Glossary seeds

- Client terms with their plain meaning, one per line: _term (meaning)_.
