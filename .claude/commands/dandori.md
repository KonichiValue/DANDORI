---
description: Open the interactive DANDORI Desk MCP App (optionally focused on a job, ticket, or draft)
argument-hint: [job-or-ticket] [draft-number]
---

Call `dandori_board` from the `dandori` MCP server right now:
- If `$1` is provided, pass `focus: "$1"`; otherwise omit `focus` to open my active board.
- If `$2` is a number, pass `draft: $2`.

If `$1` names a specific job or ticket, also call `dandori_context` for `$1` (and `draft: $2` if numeric) so you have the full ticket, numbered drafts, recent timeline, and account diagnostics ready. If `$ARGUMENTS` contains an additional question or edit instruction, carry it out. Never send external messages without my explicit approval.
