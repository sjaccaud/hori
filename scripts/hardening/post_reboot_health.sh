#!/bin/bash
# Post-reboot functional health check for AIOS safety spine.
#
# Verifies that the safety spine is not just "running" but actually
# functional. This was created after the Aug 8 2026 incident where
# the tool daemon was crash-looping (missing /tmp/hori-workspace) and
# the Sherpa was blind (timestamp type mismatch) — both after a reboot.
# `systemctl is-active` returned "active" for aios-core and the Sherpa,
# but the safety spine was non-functional.
#
# This script checks:
#   1. All critical services are active (and not flapping — crash-loopers
#      sit in "activating" forever and never show in `systemctl --failed`)
#   2. The tool daemon socket exists and is reachable
#   3. The Sherpa capability file exists, is fresh, and shows Level 0
#   4. The Sherpa is NOT skipping audit log entries (not blind), AND its
#      audit path agrees with the daemon's configured path (input is live)
#   5. The tool daemon can execute a real tool call (end-to-end)
#   6. The LLM inference server is responding
#   7. /tmp/hori-workspace exists (daemon needs it at namespace setup)
#   8. Docker stack containers are running (advisory, warn-only)
#   9. Auxiliary daemons are running (advisory, warn-only)
#
# 2026-09-25 incident addendum: `chmod 750` on the repo owner's home broke CHDIR for
# aios-worker + root-broker services — both crash-looped ~90min undetected
# because nothing ran this check. Coverage added: system-root-broker,
# llama-nano-adaptive, local-tools-adaptive, project-tools-adaptive, and
# NRestarts flap detection. hori-boot-health.service now runs this at boot
# and notifies pass/fail via scripts/hardening/notify_health.sh.
#
# Usage: sudo scripts/hardening/post_reboot_health.sh
#        (sudo needed to read the audit log and tool socket)
#
# Exit codes:
#   0 = all checks passed
#   1 = one or more checks failed
#
# Traces to: docs/operations.md "Post-Reboot Health Check"
#            docs/roadmap.md Tier 2 (safety spine)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0
WARN=0

ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
warn() { echo "  WARN: $1"; WARN=$((WARN+1)); }

echo "=== HORI Post-Reboot Functional Health Check ==="
echo "  $(date)"
echo ""

# --- 1. Service status ---
echo "--- 1. Service Status ---"
SERVICES="aios_core aios-sherpa aios-tool-daemon system-root-broker
          llamacpp-adaptive llama-nano-adaptive local-tools-adaptive
          project-tools-adaptive"
for svc in $SERVICES; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        ok "$svc is active"
    else
        fail "$svc is NOT active (state: $(systemctl is-active "$svc" 2>/dev/null || echo missing))"
    fi
done

# Flap detection: a restart-looping unit can read "active" in a lucky
# instant. NRestarts counts auto-restarts since last manual start — a
# healthy boot leaves these at 0.
for svc in $SERVICES; do
    N=$(systemctl show "$svc" -p NRestarts --value 2>/dev/null || echo 0)
    if [ "${N:-0}" -gt 5 ]; then
        warn "$svc has auto-restarted $N times — flapping (invisible to 'systemctl --failed')"
    fi
done
echo ""

# --- 2. Tool daemon socket ---
echo "--- 2. Tool Daemon Socket ---"
SOCKET="/run/hori/tool-daemon.sock"
if [ -S "$SOCKET" ]; then
    ok "Unix socket exists at $SOCKET"
else
    fail "Unix socket missing at $SOCKET — tool daemon not listening"
fi
echo ""

# --- 3. Sherpa capability file ---
echo "--- 3. Sherpa Capability File ---"
CAP_FILE="/run/sherpa/capability_level"
if [ ! -f "$CAP_FILE" ]; then
    fail "Capability file missing at $CAP_FILE — Sherpa hasn't started"
else
    CAP_CONTENT=$(cat "$CAP_FILE" 2>/dev/null || echo "")
    if [ -z "$CAP_CONTENT" ]; then
        fail "Capability file is empty"
    else
        CAP_LEVEL=$(echo "$CAP_CONTENT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('level','?'))" 2>/dev/null || echo "?")
        CAP_AGE=$(python3 -c "
import json, time
with open('$CAP_FILE') as f:
    d = json.load(f)
age = time.time() - d.get('timestamp', 0)
print(f'{age:.1f}s')
" 2>/dev/null || echo "?")

        if [ "$CAP_LEVEL" = "0" ]; then
            ok "Capability level is 0 (normal)"
        elif [ "$CAP_LEVEL" = "4" ]; then
            fail "Capability level is 4 (full stop) — Sherpa may be dead or escalated"
        else
            warn "Capability level is $CAP_LEVEL (not 0) — check Sherpa logs"
        fi

        # Check freshness (should be < 10s)
        if python3 -c "
import json, time, sys
with open('$CAP_FILE') as f:
    d = json.load(f)
age = time.time() - d.get('timestamp', 0)
sys.exit(0 if age < 10 else 1)
" 2>/dev/null; then
            ok "Capability file is fresh (age: $CAP_AGE)"
        else
            fail "Capability file is stale (age: $CAP_AGE > 10s) — Sherpa may be dead"
        fi
    fi
fi
echo ""

# --- 4. Sherpa is not blind ---
echo "--- 4. Sherpa Audit Log Health ---"
AUDIT_LOG="/var/log/hori/tool_audit.jsonl"
if [ ! -f "$AUDIT_LOG" ]; then
    warn "Audit log doesn't exist yet at $AUDIT_LOG — no tool calls have been made"
else
    # Check the Sherpa's recent logs for "skipping malformed"
    SHERPA_SKIPS=$(journalctl -u aios-sherpa --no-pager --since "5 min ago" 2>/dev/null | grep -c "skipping malformed" || true)
    if [ "$SHERPA_SKIPS" -eq 0 ]; then
        ok "Sherpa is not skipping audit entries (no parse errors in last 5 min)"
    else
        fail "Sherpa is skipping $SHERPA_SKIPS audit entries — it may be blind (check timestamp format)"
    fi

    # Check for the health check log
    SHERPA_HEALTH=$(journalctl -u aios-sherpa --no-pager --since "5 min ago" 2>/dev/null | grep -c "health" || true)
    if [ "$SHERPA_HEALTH" -gt 0 ]; then
        ok "Sherpa health check is running"
    fi
fi

# Producer/consumer path agreement — the Aug 8 2026 split: the daemon
# wrote /var/log/hori/tool_audit.jsonl while the Sherpa read a frozen
# /var/log/aios/tool_audit.jsonl for 5 weeks. Parse-ratio checks stayed
# green because a frozen-but-valid file parses fine. This check binds
# the two paths and verifies the input is actually alive.
DAEMON_AUDIT=$(systemctl show aios-tool-daemon -p Environment 2>/dev/null \
    | tr ' ' '\n' | grep -oP 'AIOS_AUDIT_LOG=\K\S+' || true)
DAEMON_AUDIT="${DAEMON_AUDIT:-/var/log/hori/tool_audit.jsonl}"
SHERPA_AUDIT=$(journalctl -u aios-sherpa -n 20 --no-pager 2>/dev/null \
    | grep -oP 'audit log: \K[^,]+' | tail -1 || true)

if [ -z "$SHERPA_AUDIT" ]; then
    warn "Could not determine Sherpa's audit path from journal — check manually"
elif [ "$SHERPA_AUDIT" != "$DAEMON_AUDIT" ]; then
    fail "Audit path split: daemon writes $DAEMON_AUDIT but Sherpa reads $SHERPA_AUDIT"
else
    ok "Sherpa audit path matches daemon ($SHERPA_AUDIT)"
    if [ ! -f "$SHERPA_AUDIT" ]; then
        warn "Shared audit log $SHERPA_AUDIT doesn't exist yet"
    else
        AUDIT_AGE_H=$(( ($(date +%s) - $(stat -c %Y "$SHERPA_AUDIT")) / 3600 ))
        if [ "$AUDIT_AGE_H" -ge 24 ]; then
            warn "Audit log silent for ${AUDIT_AGE_H}h — legitimate idle, or dead input?"
        else
            ok "Audit log has recent activity (${AUDIT_AGE_H}h ago)"
        fi
    fi
fi
echo ""

# --- 5. Tool daemon end-to-end test ---
echo "--- 5. Tool Daemon End-to-End ---"
if [ -S "$SOCKET" ]; then
    # Send a real count_files request to the tool daemon
    RESULT=$(python3 -c "
import json, socket, sys
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect('$SOCKET')
    req = json.dumps({'tool': 'count_files', 'args': {'path': '$PROJECT_DIR', 'pattern': '*.py'}}) + '\n'
    s.sendall(req.encode())
    resp = b''
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
    s.close()
    data = json.loads(resp.decode().strip())
    if 'error' in data:
        print(f'ERROR: {data[\"error\"]}')
        sys.exit(1)
    count = data.get('result', {}).get('count', '?')
    print(f'count_files returned {count} .py files')
    sys.exit(0)
except Exception as e:
    print(f'EXCEPTION: {e}')
    sys.exit(1)
" 2>&1) && ok "Tool daemon responded: $RESULT" || fail "Tool daemon request failed: $RESULT"
else
    fail "Cannot test tool daemon — socket missing"
fi
echo ""

# --- 6. LLM inference server ---
echo "--- 6. LLM Inference Server ---"
# Retry once — a cold boot may still be loading the model.
LLAMA_OK=0
for attempt in 1 2; do
    if curl -s --max-time 5 http://127.0.0.1:8080/health 2>/dev/null | grep -q "ok"; then
        LLAMA_OK=1
        break
    fi
    sleep 12
done
if [ "$LLAMA_OK" -eq 1 ]; then
    ok "llama-server is healthy (responded to /health)"
else
    fail "llama-server is not responding to /health"
fi
echo ""

# --- 7. /tmp/hori-workspace exists ---
echo "--- 7. Tool Daemon Workspace ---"
if [ -d "/tmp/hori-workspace" ]; then
    ok "/tmp/hori-workspace exists"
else
    fail "/tmp/hori-workspace missing — tool daemon will crash on restart"
fi
echo ""

# --- 8. Docker stack (advisory — degraded function, not spine) ---
echo "--- 8. Docker Stack (advisory) ---"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    for c in qdrant_aios searchrouter_aios aios_embedding open_webui_aios; do
        state=$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || echo "missing")
        if [ "$state" = "true" ]; then
            ok "container $c running"
        else
            warn "container $c not running (state: $state)"
        fi
    done
else
    warn "docker unreachable — container checks skipped"
fi
echo ""

# --- 9. Auxiliary daemons (advisory — project services, not spine) ---
echo "--- 9. Auxiliary Services (advisory) ---"
for svc in krill_harvester krill-supervisor; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        ok "$svc is active"
    else
        warn "$svc is not active (state: $(systemctl is-active "$svc" 2>/dev/null || echo missing))"
    fi
done
echo ""

# --- Summary ---
echo "=== Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
echo "  Warnings: $WARN"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "RESULT: FAIL — $FAIL check(s) failed. The safety spine is not fully functional."
    echo "Check service logs with:"
    echo "  journalctl -u aios-tool-daemon -e"
    echo "  journalctl -u aios-sherpa -e"
    exit 1
else
    echo "RESULT: PASS — all checks passed. The safety spine is functional."
    exit 0
fi
