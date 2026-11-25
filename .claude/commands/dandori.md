---
description: Open the interactive DANDORI Desk MCP App (optionally focused on a job, ticket, or draft)
argument-hint: [job-or-ticket] [draft-number]
---

- If `$1` is omitted (or `"board"`), call `dandori_board` from the `dandori` MCP server right now to open my active board.
- If `$1` names a specific job or ticket, call `dandori_context` for `$1` (and `draft: $2` if `$2` is numeric) so you load only that spot's ticket, drafts, and timeline without dumping the whole board; call `dandori_board` only if I ask for the interactive board widget, and call `dandori_inspect_account` only if live DB state is needed.

If `$ARGUMENTS` contains an additional question or edit instruction, carry it out. Never send external messages without my explicit approval.
