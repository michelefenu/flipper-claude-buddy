#!/bin/bash
set -euo pipefail

# UserPromptSubmit hook: show "Thinking..." on Flipper when user sends a prompt

SOCKET="/tmp/claude-flipper-bridge.sock"

if [ ! -S "$SOCKET" ]; then
    exit 0
fi

# Refresh the active input target from the session that just submitted a prompt.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/session-target.py" register_target "$SOCKET" >/dev/null 2>&1 || true

SESSION_ID=$(sed -n 's/.*"session_id" *: *"\([^"]*\)".*/\1/p' | head -1)
echo "{\"action\":\"display\",\"session_id\":\"$SESSION_ID\",\"text\":\"Thinking...\",\"subtext\":\"\"}" \
    | nc -U "$SOCKET" 2>/dev/null &

exit 0
