#!/bin/bash
set -euo pipefail

# StopFailure hook: notify Flipper when a turn ends with an API error.

SOCKET="/tmp/claude-flipper-bridge.sock"

if [ ! -S "$SOCKET" ]; then
    exit 0
fi

SESSION_ID=$(sed -n 's/.*"session_id" *: *"\([^"]*\)".*/\1/p' | head -1)
echo "{\"action\":\"notify\",\"session_id\":\"$SESSION_ID\",\"sound\":\"error\",\"vibro\":true,\"text\":\"Claude\",\"subtext\":\"API Error\"}" \
    | nc -U "$SOCKET" 2>/dev/null &

exit 0
