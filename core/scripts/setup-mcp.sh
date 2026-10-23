#!/bin/zsh
# Give headless Claude Code the connections this desk reads from.
#
# Run it once. It adds any missing server, then walks the ones that need a
# browser approval. Everything it does is to your user config, so it counts for
# every folder, not only this one.
#
#   ./setup-mcp.sh          add what is missing, then log in to what needs it
#   ./setup-mcp.sh --check  say where things stand, change nothing

set -uo pipefail

HUB="https://hub.ai.ktl.net/mcp"
REPO="${0:A:h:h:h}"
export PYTHONPATH="$REPO/core${PYTHONPATH:+:$PYTHONPATH}"

command -v claude >/dev/null 2>&1 || {
  print -u2 "claude is not installed. Nothing to set up."
  exit 1
}

# `claude mcp list` health-checks every server, which costs seconds and can time
# one out and omit it, so it is asked once and only for the thing it alone knows:
# which grants are live. Whether a server is configured at all is a question the
# config file answers instantly and without a network.
states() { claude mcp list 2>&1 }

configured() {
  python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".claude.json"
try:
    d = json.loads(p.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    d = {}
print("\n".join(d.get("mcpServers") or {}))
PY
}

if [[ "${1:-}" == "--check" ]]; then
  states
  exit 0
fi

have="$(configured)"

# Adding one that is already there is not a failure, it is the normal state of
# every run after the first. Only a real refusal should stop the script, because
# stopping here is what skips the browser approvals underneath, which are the
# whole point of running it.
add() {
  local name="$1"; shift
  if print -r -- "$have" | grep -qx "$name"; then
    print "$name: already configured"
    return 0
  fi
  print "adding $name"
  local out
  if out="$(claude mcp add "$@" 2>&1)"; then
    return 0
  fi
  if [[ "$out" == *"already exists"* ]]; then
    print "$name: already configured"
    return 0
  fi
  print -u2 "could not add $name: $out"
  return 1
}

# Asana and Slack are the two a sweep cannot work without: the tickets and the
# threads. Google comes along because the AI Hub grants it beside Slack and a
# meeting invite is sometimes the only record of when a room happens. Notion is
# where meeting notes often land; without it here, a sweep reports Notion
# as missing even when the desktop app has it connected, because headless
# Claude Code only sees the servers in ~/.claude.json.
for name in asana slack google notion; do
  add "$name" --transport http --scope user "$name" "$HUB/$name"
done

# A read-only production replica, when config.json `database` names one. It is
# a local process rather than an OAuth grant, so it is only ever missing on a
# fresh machine. `database.command` is the binary that serves it and
# `database.conn`, when set, goes to it as KTDB_CONN.
DB_NAME="$(python3 -c 'import settings; print(settings.database_mcp())' 2>/dev/null)"
if [[ -n "$DB_NAME" ]]; then
  DB_CMD="$(python3 -c 'import settings; print(settings.section("database").get("command") or "")' 2>/dev/null)"
  DB_CONN="$(python3 -c 'import settings; print(settings.section("database").get("conn") or "")' 2>/dev/null)"
  DB_CMD="${DB_CMD:-$HOME/.local/bin/ktdb-mcp}"
  DB_CMD="${DB_CMD/#\~/$HOME}"
  if [[ -x "$DB_CMD" ]]; then
    db_env=()
    [[ -n "$DB_CONN" ]] && db_env=(-e "KTDB_CONN=$DB_CONN")
    add "$DB_NAME" --scope user "${db_env[@]}" "$DB_NAME" -- "$DB_CMD"
  else
    print "$DB_NAME: no $DB_CMD on this machine, skipping"
  fi
fi

print ""
print "Now the browser approvals. Each opens a tab; approve it and come back."

# Read the state fresh: a server added a moment ago always needs its grant.
needs=()
while IFS= read -r line; do
  [[ "$line" == *"Needs authentication"* ]] || continue
  needs+=("${line%%:*}")
done < <(states)

if (( ${#needs} == 0 )); then
  print "Nothing needs authorising. You are done."
else
  for name in "${needs[@]}"; do
    print ""
    # `print -r --` or zsh reads a leading dash as its own option.
    print -r -- "--- $name ---"
    claude mcp login "$name" || print -u2 "$name did not complete. Run: claude mcp login $name"
  done
fi

print ""
print "Where things stand now:"
states
