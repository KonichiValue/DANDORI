# Client Workspaces & Plugins (`clients/`)

Modeled on Kraken Core's `octoenergy/plugins/clients/` architecture, DANDORI
separates its universal engine (`core/`) from each client and owner workspace in
`clients/<client>/`.

## How the layers fit together

- **`core/`** is DANDORI Core: the board engine, local server, renderers, agent
  runner, prompts, stylesheets and setup flow. It is tracked in git and shared by
  every Client Delivery Lead.
- **`clients/<client>/`** (for example `clients/ee/`) is the client plugin and
  workspace layer. It holds everything specific to one client and one owner's
  workflow, and is gitignored so client data, scripts and configuration never
  leak into the shared repository.

`clients/active` is a symlink pointing to the active client folder (set
automatically by `./core/setup.py` or the `/setup` page, and overridable per
command with `DANDORI_CLIENT=<code>`).

## What lives in `clients/<client>/`

```text
clients/<client>/
├── config.json     # Client & owner settings (IDs, rooms, timezone, models)
├── profile.md      # Playbook in prose: house style, people, rooms, rules
├── plugin.py       # Optional Python plugin hooks for check.py and audit.py
├── scripts/        # Client-specific workflow scripts (`DANDORI scripts`)
├── docs/           # Client documents, handovers, sheets and PDFs (`/doc/...`)
├── state/          # board.json, asks.json, calendar.json, prefs.json, history/
├── output/         # Rendered desk.html and desk.md
├── logs/           # Refresh, prep, ask and server logs
└── tmp/            # Scratch space for sweep patches and data dumps
```

## Writing a `plugin.py`

A client workspace can optionally include `plugin.py` to hook into DANDORI Core
without modifying `core/`:

- `ACCOUNT_RE`: a compiled `re.Pattern` matching the client's account numbers.
- `CHECKS`: a tuple of `(name, fn(board) -> list[str])` functions run by
  `./core/check.py` (for example, verifying that client-facing lines contain no
  internal ticket IDs or story points).
- `AUDIT_CHECKS`: a tuple of `fn(board, report)` functions run by
  `./core/audit.py`.

## Client workflow scripts (`scripts/`)

Put any client-specific Python script (tariff builders, workbook diffs, data
checks) in `clients/<client>/scripts/`. DANDORI discovers them automatically:

```bash
DANDORI scripts                     # list all scripts in clients/<client>/scripts/
DANDORI run <script-name> [args...] # run a client script
```
