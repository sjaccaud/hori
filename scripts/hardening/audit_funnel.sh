#!/usr/bin/env bash
# PoC 1.0.1: Tailscale Serve Audit
#
# Audits the current Tailscale Serve/Funnel configuration to verify:
#   1. Funnel (public internet) is OFF — nothing exposed to the internet
#   2. Serve (Tailnet HTTPS) only exposes allowed paths (voice + admin)
#   3. No sensitive endpoints (/system/*, /chat, etc.) are proxied
#
# Traces to docs/roadmap.md Tier 1A, PoC 1.0.1.
#
# This is a READ-ONLY audit — it does not change anything. Run the companion
# hardening script (harden_funnel.sh) to apply restrictions.
#
# Usage: ./scripts/hardening/audit_funnel.sh
#
# Exit codes:
#   0 — audit passed (Serve only exposes allowed paths, Funnel is off)
#   1 — audit failed (Funnel is on, or Serve exposes disallowed paths)
#   2 — audit could not run (Tailscale not installed, not running, etc.)
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo "=== Tailscale Serve Audit (PoC 1.0.1) ==="
echo

# --- Check Tailscale is available ---
if ! command -v tailscale &>/dev/null; then
    echo -e "${RED}ERROR: tailscale command not found.${NC}"
    exit 2
fi

if ! tailscale status &>/dev/null; then
    echo -e "${RED}ERROR: Tailscale is not running.${NC}"
    exit 2
fi

# --- Check Funnel is OFF (public internet) ---
echo "--- Funnel status (should be OFF) ---"
FUNNEL_OUTPUT=$(tailscale funnel status 2>&1 || true)
echo "$FUNNEL_OUTPUT"
echo

if echo "$FUNNEL_OUTPUT" | grep -q "Funnel on"; then
    fail "Funnel is ON — endpoints are exposed to the public internet!"
    echo "  Run: sudo ./scripts/hardening/harden_funnel.sh"
    exit 1
else
    pass "Funnel is OFF — nothing exposed to the public internet."
fi
echo

# --- Check Serve config (Tailnet HTTPS) ---
echo "--- Serve status (Tailnet HTTPS) ---"
SERVE_OUTPUT=$(tailscale serve status 2>&1 || true)
echo "$SERVE_OUTPUT"
echo

if ! echo "$SERVE_OUTPUT" | grep -q "ts.net"; then
    warn "Serve is not configured. Voice and admin will not be accessible via HTTPS."
    echo "  Run: sudo ./scripts/hardening/harden_funnel.sh"
    exit 0
fi

# --- Parse allowed paths ---
# Allowed paths: voice endpoints + admin + health + static
ALLOWED_PATTERN='(^/voice$|^/v1/voice/|^/admin$|^/admin/api$|^/health$|^/static$)'

# --- Classify routes ---
echo "--- Route classification ---"
ROUTES=$(echo "$SERVE_OUTPUT" | grep -E '^\|--' || true)
if [ -z "$ROUTES" ]; then
    warn "No routes found in Serve config."
    exit 0
fi

AUDIT_FAILED=0

while IFS= read -r route; do
    # Extract the path (first field after |--)
    path=$(echo "$route" | sed -E 's/^\|--\s+//' | awk '{print $1}')
    if [ -z "$path" ]; then
        continue
    fi

    if [ "$path" = "/" ]; then
        fail "Catch-all route '/' exposes ALL endpoints to the Tailnet."
        echo "  This means /system/*, /chat, /v1/chat/completions, etc. are all reachable."
        AUDIT_FAILED=1
    elif echo "$path" | grep -qE "$ALLOWED_PATTERN"; then
        pass "Route '$path' is an allowed endpoint."
    else
        fail "Route '$path' is NOT in the allowlist — should not be served."
        AUDIT_FAILED=1
    fi
done <<< "$ROUTES"
echo

# --- Check for missing required paths ---
echo "--- Required path check ---"
REQUIRED_PATHS=("/voice" "/health" "/static" "/admin" "/admin/api")
for req in "${REQUIRED_PATHS[@]}"; do
    if echo "$SERVE_OUTPUT" | grep -q "$req"; then
        pass "Required path '$req' is configured."
    else
        warn "Required path '$req' is missing."
    fi
done
echo

# --- Summary ---
echo "=== Summary ==="
if [ "$AUDIT_FAILED" -eq 1 ]; then
    fail "Serve exposes disallowed paths."
    echo
    echo "Remediation:"
    echo "  1. Run: sudo ./scripts/hardening/harden_funnel.sh"
    echo "  2. This will reset Serve to only proxy allowed paths."
    exit 1
else
    pass "Serve only exposes allowed paths. Funnel is off."
    pass "All endpoints are Tailnet-only HTTPS — not reachable from the public internet."
    exit 0
fi
