#!/bin/sh
# hori-mcpo watchdog: restart mcpo when its tool spec went empty.
#
# mcpo snapshots the MCP tool list once at startup and never reconnects —
# if the SSE handshake raced aios_core at login, or aios_core restarted and
# dropped the session, the mounted spec has zero paths and every hori-state
# tool silently vanishes from Open-WebUI. Detect the empty spec on mcpo
# itself (:8774, bypassing the :8775 filter) and restart.
set -u
LOG="$HOME/.local/state/hori-mcpo-watchdog.log"
# mcpo is behind --api-key/--strict-auth (2026-09-23); source the key.
# shellcheck disable=SC1091
. "$HOME/.config/mcpo/mcpo.env"
spec=$(curl -sf -m 5 -H "Authorization: Bearer $MCPO_API_KEY" http://127.0.0.1:8774/hori/openapi.json) || exit 0
paths=$(printf '%s' "$spec" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("paths",{})))' 2>/dev/null) || exit 0
if [ "$paths" = "0" ]; then
    mkdir -p "$HOME/.local/state"
    echo "$(date -Is) mcpo spec empty; restarting hori-mcpo" >> "$LOG"
    systemctl --user restart hori-mcpo.service
fi
