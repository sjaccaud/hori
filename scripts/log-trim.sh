#!/bin/sh
# log-trim: cap append-only diagnostic logs (weekly timer).
#
# Rotates by tail-keep: files over MAX_BYTES are replaced with their last
# KEEP_LINES lines. mv-replace is safe here — every writer opens per-append
# (probe filter, _dbg, watchdog), so nothing holds a stale inode.
#
# Deliberately NOT covered: ~/.local/share/hori/decisions/*.jsonl and
# sessions.jsonl — those are data (decision/session records), not logs.
set -u
MAX_BYTES=20971520   # 20 MB
KEEP_LINES=50000
LOG="$HOME/.local/state/log-trim.log"

trim() {  # path
    f=$1
    [ -f "$f" ] || return 0
    size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX_BYTES" ]; then
        tail -n "$KEEP_LINES" "$f" > "$f.tmp" && mv "$f.tmp" "$f"
        mkdir -p "$HOME/.local/state"
        echo "$(date -Is) trimmed $f ($size bytes)" >> "$LOG"
    fi
}

# Host-side logs
trim "$HOME/.local/state/hori-mcpo-watchdog.log"
trim "$HOME/.local/state/krill/cron.log"
trim "$HOME/.local/state/log-trim.log"

# OWUI volume logs — root-owned on host, reach them via the container
if docker exec open_webui_aios true 2>/dev/null; then
    docker exec open_webui_aios sh -c '
        for f in /app/backend/data/request_probe.log \
                 /app/backend/data/tool_server_filter_debug.log; do
            [ -f "$f" ] || continue
            size=$(stat -c%s "$f" 2>/dev/null || echo 0)
            if [ "$size" -gt 20971520 ]; then
                tail -n 50000 "$f" > "$f.tmp" && mv "$f.tmp" "$f"
            fi
        done'
fi
