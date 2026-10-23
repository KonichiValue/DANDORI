#!/bin/zsh
# Fold today's standup notes into the board, then open the desk.
#
#   ./core/scripts/run_post.sh            normal run, skips if today's page already exists
#   ./core/scripts/run_post.sh --force    rebuild even if today's page exists
#   ./core/scripts/run_post.sh --no-open  build only, don't open the browser
#   ./core/scripts/run_post.sh --once     one attempt only, don't wait for the Notion note
#
# The meeting note lands roughly five minutes after the standup ends, but
# the meeting itself can run long. So this polls: each attempt is a cheap agent
# run that exits early when the note is missing, and we retry until the deadline.

set -uo pipefail

REPO="${0:A:h:h:h}"
cd "$REPO" || exit 1

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONPATH="$REPO/core${PYTHONPATH:+:$PYTHONPATH}"
TZ_NAME="$(python3 -c 'import settings; print(settings.tz_name())' 2>/dev/null)"
[[ -n "$TZ_NAME" ]] && export TZ="$TZ_NAME"

FORCE=0
OPEN=1
ONCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --no-open) OPEN=0 ;;
    --once) ONCE=1 ;;
  esac
done

TODAY="$(date +%Y-%m-%d)"
STATE_DIR="$(python3 -c 'import settings; print(settings.STATE_DIR)' 2>/dev/null)"
OUTPUT_DIR="$(python3 -c 'import settings; print(settings.OUTPUT_DIR)' 2>/dev/null)"
LOGS_DIR="$(python3 -c 'import settings; print(settings.LOGS_DIR)' 2>/dev/null)"

BOARD="$STATE_DIR/board.json"
HTML="$OUTPUT_DIR/desk.html"
LOG="$LOGS_DIR/run.log"
AGENT_LOG="$LOGS_DIR/agent-post-$TODAY.log"
TIMEOUT_SECS="${POST_TIMEOUT:-900}"
# 45 minutes after the main standup ends, from config.json `meeting`.
DEADLINE="${POST_DEADLINE:-$(python3 -c '
import datetime, settings
h, m = settings.meeting_ends()
print((datetime.datetime(2000, 1, 1, h, m) + datetime.timedelta(minutes=45)).strftime("%H:%M"))
' 2>/dev/null)}"
DEADLINE="${DEADLINE:-11:45}"
MEETING="$(python3 -c 'import settings; print(settings.meeting()["name"])' 2>/dev/null)"
RETRY_SECS="${POST_RETRY:-300}"

mkdir -p "$OUTPUT_DIR" "$LOGS_DIR" "$STATE_DIR"

log() { print -r -- "[$(date '+%Y-%m-%d %H:%M:%S')] post: $*" | tee -a "$LOG"; }

fail() {
  log "FAILED: $1"
  python3 "$REPO/core/render_desk.py" --error "$1

Agent log: $AGENT_LOG" "$HTML"
  [[ $OPEN -eq 1 ]] && open "$HTML"
  exit 1
}

AGENT_CMD=("${(@f)$(python3 -c '
import sys
import agent
kind = agent.pick()
if kind is None:
    sys.exit(3)
model = sys.argv[1] or agent.model_for("post")
print("\n".join(kind.command(model, effort=agent.effort_for("post"), job="post")))
' "${POST_MODEL:-}")}") || fail "Claude Code is unavailable, or DANDORI_AGENT names an unsupported runner."

AGENT_BIN="${AGENT_CMD[1]}"

LOCK="$STATE_DIR/refresh.lock"

lock_is_held() {
  [[ -f "$LOCK" ]] || return 1
  local pid
  pid="$(cut -d' ' -f1 "$LOCK" 2>/dev/null)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  rm -f "$LOCK"   # stale: whoever held it is gone
  return 1
}

take_lock() {
  local waited=0
  while lock_is_held; do
    log "board is locked by $(cat "$LOCK" 2>/dev/null); waiting rather than racing it"
    sleep 10
    waited=$((waited + 10))
    (( waited > TIMEOUT_SECS )) && fail "waited $((TIMEOUT_SECS / 60)) min for the board lock; something else is stuck"
  done
  print -r -- "$$ launchd-post $(date +%H:%M)" >"$LOCK"
}

release_lock() {
  [[ -f "$LOCK" && "$(cut -d' ' -f1 "$LOCK" 2>/dev/null)" == "$$" ]] && rm -f "$LOCK"
}

trap 'release_lock; [[ -n "${WATCHDOG:-}" ]] && kill "$WATCHDOG" 2>/dev/null' EXIT INT TERM

note_found() {
  [[ -f "$BOARD" ]] || return 1
  python3 -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        note = json.load(fh).get("meeting_note") or {}
except (OSError, json.JSONDecodeError):
    sys.exit(1)
sys.exit(0 if note.get("found") and note.get("date") == sys.argv[2] else 1)
' "$BOARD" "$TODAY"
}

if note_found && [[ $FORCE -eq 0 ]]; then
  log "today's note is already on the board, opening the desk (use --force to redo)"
  [[ $OPEN -eq 1 ]] && open "$HTML"
  exit 0
fi

attempt=0
while true; do
  attempt=$((attempt + 1))
  log "attempt $attempt, looking for today's meeting note"

  take_lock

  python3 "$REPO/core/keep.py" post >>"$LOG" 2>&1 || true

  python3 "$REPO/core/settings.py" --prompt core/prompts/post.md | "${AGENT_CMD[@]}" >"$AGENT_LOG" 2>&1 &
  AGENT_PID=$!

  ( sleep "$TIMEOUT_SECS"
    if kill -0 "$AGENT_PID" 2>/dev/null && ps -p "$AGENT_PID" -o command= 2>/dev/null | grep -q "$AGENT_BIN"; then
      kill -9 "$AGENT_PID" 2>/dev/null
    fi ) &
  WATCHDOG=$!

  wait $AGENT_PID
  AGENT_RC=$?
  kill $WATCHDOG 2>/dev/null
  WATCHDOG=""
  release_lock

  [[ -f "$BOARD" ]] || fail "the agent finished (exit $AGENT_RC) without writing $BOARD"

  if note_found; then
    log "meeting note found on attempt $attempt"
    break
  fi

  if [[ $ONCE -eq 1 ]]; then
    fail "the meeting note has not been published yet (single attempt requested)"
  fi

  if [[ "$(date +%H:%M)" > "$DEADLINE" ]]; then
    fail "gave up at $DEADLINE: no ${MEETING:-standup} meeting note published today"
  fi

  log "no note yet, retrying in $((RETRY_SECS / 60)) min"
  sleep "$RETRY_SECS"
done

if ! python3 "$REPO/core/render_desk.py" "$BOARD" "$HTML" >>"$LOG" 2>&1; then
  fail "could not render $BOARD into HTML. See $LOG"
fi

if ! python3 "$REPO/core/render_desk_md.py" "$BOARD" "$OUTPUT_DIR/desk.md" >>"$LOG" 2>&1; then
  log "WARNING: markdown render failed, HTML page is still fine"
fi

if [[ "${POST_REMIND:-0}" == "1" ]]; then
  if ! python3 "$REPO/core/remind.py" "$BOARD" >>"$LOG" 2>&1; then
    log "WARNING: could not push reminders"
  fi
fi

log "$(python3 - "$BOARD" <<'PY' 2>/dev/null || true
import json, sys
from render import sessions
board = json.load(open(sys.argv[1], encoding="utf-8"))
rows = sessions(board)
if not rows:
    print("no session on the board")
else:
    print("next: " + "; ".join(
        f"{s.get('title') or s.get('name')} {s.get('date')}"
        + (" (skipped)" if s.get("skipped") else "")
        for s in rows[:3]
    ))
PY
)"

log "built $HTML"
[[ $OPEN -eq 1 ]] && open "$HTML"
exit 0
