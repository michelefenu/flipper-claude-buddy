#!/bin/bash
set -euo pipefail

# TaskCompleted hook: buzz Flipper when a task is marked done.

SOCKET="/tmp/claude-flipper-bridge.sock"

if [ ! -S "$SOCKET" ]; then
    exit 0
fi

SESSION_ID=$(sed -n 's/.*"session_id" *: *"\([^"]*\)".*/\1/p' | head -1)
echo "{\"action\":\"notify\",\"session_id\":\"$SESSION_ID\",\"sound\":\"success\",\"vibro\":true,\"text\":\"Task\",\"subtext\":\"Completed\"}" \
    | nc -U "$SOCKET" 2>/dev/null &

exit 0
