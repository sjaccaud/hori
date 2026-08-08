#!/usr/bin/env bash
# PoC 12.4: Safe Self-Update
#
# Safely updates AIOS: pulls latest code, runs tests, and only restarts the
# service if tests pass. If tests fail, the update is rolled back and the
# service keeps running on the old code.
#
# Traces to: docs/roadmap.md Tier 1B, PoC 12.4.
#
# Safety properties:
#   1. Stash uncommitted changes before pulling (never lose user work).
#   2. Run the FULL test suite before restarting anything.
#   3. If tests fail, roll back to the previous commit and abort.
#   4. If tests pass, restart aios-core and verify it comes up healthy.
#   5. If aios-core fails to start, roll back and restart on old code.
#   6. Log everything to logs/self_update.log for auditability.
#
# Usage: sudo ./scripts/hardening/safe_self_update.sh [--dry-run]
#
# Exit codes:
#   0 — update successful
#   1 — update failed (tests or health check), rolled back
#   2 — update failed and rollback also failed (MANUAL INTERVENTION NEEDED)
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}ERROR: This script must be run as root (sudo).${NC}"
    echo "  sudo $0"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_FILE="$PROJECT_ROOT/logs/self_update.log"
DRY_RUN=false
HEALTH_URL="http://localhost:5680/health"
HEALTH_TIMEOUT=30  # seconds to wait for aios-core to come up

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

mkdir -p "$PROJECT_ROOT/logs"

log() {
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "[$ts] $1" | tee -a "$LOG_FILE"
}

pass() { log -e "${GREEN}[PASS]${NC} $1"; }
fail() { log -e "${RED}[FAIL]${NC} $1"; }
warn() { log -e "${YELLOW}[WARN]${NC} $1"; }

log "=== AIOS Safe Self-Update (PoC 12.4) ==="
[ "$DRY_RUN" = true ] && log "DRY RUN MODE — no changes will be applied."

cd "$PROJECT_ROOT"

# --- Step 1: Record the current state ---
CURRENT_COMMIT=$(git rev-parse HEAD)
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
log "Current state: $CURRENT_BRANCH @ ${CURRENT_COMMIT:0:8}"

# --- Step 2: Stash uncommitted changes ---
log "Checking for uncommitted changes..."
if ! git diff --quiet || ! git diff --cached --quiet; then
    STASH_NAME="aios-self-update-$(date +%s)"
    log "Stashing uncommitted changes as '$STASH_NAME'..."
    if [ "$DRY_RUN" = false ]; then
        git stash push -m "$STASH_NAME" || {
            fail "Failed to stash changes. Aborting."
            exit 1
        }
    fi
    STASHED=true
else
    log "No uncommitted changes."
    STASHED=false
fi

# --- Step 3: Pull latest ---
log "Pulling latest from origin/$CURRENT_BRANCH..."
if [ "$DRY_RUN" = false ]; then
    if ! git pull origin "$CURRENT_BRANCH" 2>&1 | tee -a "$LOG_FILE"; then
        fail "git pull failed."
        [ "$STASHED" = true ] && git stash pop || true
        exit 1
    fi
fi

NEW_COMMIT=$(git rev-parse HEAD)
if [ "$NEW_COMMIT" = "$CURRENT_COMMIT" ]; then
    log "Already up to date. Nothing to do."
    [ "$STASHED" = true ] && [ "$DRY_RUN" = false ] && git stash pop || true
    exit 0
fi
log "Updated to ${NEW_COMMIT:0:8}"

# --- Step 4: Run tests ---
log "Running test suite..."
if [ "$DRY_RUN" = false ]; then
    if ! PYTHONPATH=. ./venv/bin/python3 -m pytest services/ tests/ -x -q 2>&1 | tee -a "$LOG_FILE"; then
        fail "Tests failed. Rolling back to ${CURRENT_COMMIT:0:8}."
        git reset --hard "$CURRENT_COMMIT"
        [ "$STASHED" = true ] && git stash pop || true
        log "Rolled back. aios-core is still running on the old code."
        exit 1
    fi
fi
pass "All tests passed."

# --- Step 5: Restore stashed changes ---
if [ "$STASHED" = true ] && [ "$DRY_RUN" = false ]; then
    log "Restoring stashed changes..."
    git stash pop || warn "Stash pop failed — your changes are in 'git stash list'."
fi

# --- Step 6: Restart aios-core ---
log "Restarting aios-core..."
if [ "$DRY_RUN" = false ]; then
    systemctl restart aios_core.service

    # Wait for health check
    log "Waiting for aios-core to come up (timeout: ${HEALTH_TIMEOUT}s)..."
    for i in $(seq 1 "$HEALTH_TIMEOUT"); do
        if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
            pass "aios-core is healthy after update."
            log "=== Update complete: ${CURRENT_COMMIT:0:8} -> ${NEW_COMMIT:0:8} ==="
            exit 0
        fi
        sleep 1
    done

    # Health check failed — roll back
    fail "aios-core did not come up within ${HEALTH_TIMEOUT}s. Rolling back."
    git reset --hard "$CURRENT_COMMIT"
    systemctl restart aios_core.service
    sleep 5
    if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
        warn "Rolled back to ${CURRENT_COMMIT:0:8}. aios-core is healthy on old code."
        log "=== Update FAILED, rolled back to ${CURRENT_COMMIT:0:8} ==="
        exit 1
    else
        fail "Rollback failed — aios-core is NOT healthy. MANUAL INTERVENTION NEEDED."
        log "Check: journalctl -u aios_core.service -n 50"
        log "=== UPDATE AND ROLLBACK BOTH FAILED ==="
        exit 2
    fi
else
    log "[DRY RUN] Would restart aios-core and run health check."
    log "=== Dry run complete ==="
    exit 0
fi
