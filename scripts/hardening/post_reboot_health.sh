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
#   1. All 4 critical services are active
#   2. The tool daemon socket exists and is reachable
#   3. The Sherpa capability file exists, is fresh, and shows Level 0
#   4. The Sherpa is NOT skipping audit log entries (not blind)
#   5. The tool daemon can execute a real tool call (end-to-end)
#   6. The LLM inference server is responding
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
for svc in aios_core aios-sherpa aios-tool-daemon llamacpp; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        ok "$svc is active"
    else
        fail "$svc is NOT active"
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
if curl -s --max-time 5 http://127.0.0.1:8080/health 2>/dev/null | grep -q "ok"; then
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
