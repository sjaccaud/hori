#!/usr/bin/env bash
# PoC 1.0.3: Service Binding Audit
#
# Audits which services listen on 0.0.0.0 (all interfaces) vs 127.0.0.1
# (localhost only). Services on 0.0.0.0 are reachable from the Tailscale mesh
# and potentially the LAN — a security hole per docs/system_security_audit.md.
#
# This is a READ-ONLY audit. Run harden_service_bindings.sh to apply fixes.
#
# Traces to: docs/roadmap.md Tier 1A, PoC 1.0.3.
#
# Usage: ./scripts/hardening/audit_service_bindings.sh
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo "=== Service Binding Audit (PoC 1.0.3) ==="
echo
echo "Services listening on 0.0.0.0 are reachable from the Tailscale mesh"
echo "and the LAN. Internal services should bind to 127.0.0.1 instead."
echo

# --- Map known ports to service names ---
declare -A PORT_NAMES=(
    [5680]="aios-core (intentionally exposed via Tailscale Funnel)"
    [5678]="n8n (workflow automation — has credential DB)"
    [8123]="Home Assistant (IoT control — physical devices)"
    [6333]="Qdrant (vector database)"
    [6334]="Qdrant gRPC (vector database)"
    [3000]="Open WebUI"
    [3001]="Grafana (dashboards)"
    [9090]="Prometheus (metrics)"
    [9100]="Node Exporter (system metrics)"
    [52222]="SSH (intentionally exposed)"
    [8080]="llama-server (LLM inference)"
    [8081]="Embedding server"
)

# --- Ports that SHOULD be on 0.0.0.0 (intentionally exposed) ---
# aios-core: exposed via Tailscale Funnel (but should be restricted by PoC 1.0.1)
# SSH: needs to be reachable for remote access
# Tailscale (443): the Funnel itself
declare -A INTENTIONALLY_EXPOSED=(
    [52222]="SSH — remote access required"
    [443]="Tailscale Funnel — intentional"
)

echo "--- Services on 0.0.0.0 (all interfaces) ---"
AUDIT_FAILED=0
# Extract the local address:port (4th field) and filter for binds to 0.0.0.0.
# We match the LOCAL address column, not the remote 0.0.0.0:* peer.
WILDCARD_SERVICES=$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep '^0\.0\.0\.0:' | sed 's/0\.0\.0\.0://' | sort -n || true)

if [ -z "$WILDCARD_SERVICES" ]; then
    pass "No services found on 0.0.0.0."
fi

while IFS= read -r port; do
    [ -z "$port" ] && continue
    name="${PORT_NAMES[$port]:-unknown service}"
    if [ -n "${INTENTIONALLY_EXPOSED[$port]:-}" ]; then
        pass "Port $port ($name) — intentionally exposed: ${INTENTIONALLY_EXPOSED[$port]}"
    else
        fail "Port $port ($name) — bound to 0.0.0.0, should be 127.0.0.1"
        AUDIT_FAILED=1
    fi
done <<< "$WILDCARD_SERVICES"

echo
echo "--- Services on 127.0.0.1 (localhost only — safe) ---"
LOCALHOST_SERVICES=$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep '^127\.0\.0\.1:' | sed 's/127\.0\.0\.1://' | sort -n || true)
if [ -n "$LOCALHOST_SERVICES" ]; then
    while IFS= read -r port; do
        [ -z "$port" ] && continue
        name="${PORT_NAMES[$port]:-unknown service}"
        echo "  127.0.0.1:$port ($name)"
    done <<< "$LOCALHOST_SERVICES"
else
    echo "  (none)"
fi

echo
echo "--- Docker port mappings ---"
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | while IFS=$'\t' read -r name ports; do
        if echo "$ports" | grep -q '0\.0\.0\.0:'; then
            fail "$name: $ports (bound to 0.0.0.0)"
        else
            pass "$name: $ports"
        fi
    done
else
    warn "Docker not available — skipping container port audit."
fi

echo
echo "--- UFW firewall status ---"
if command -v ufw &>/dev/null; then
    ufw status 2>/dev/null || warn "ufw status requires sudo."
else
    warn "ufw not installed."
fi

echo
echo "=== Summary ==="
if [ "$AUDIT_FAILED" -eq 1 ]; then
    fail "Internal services are bound to 0.0.0.0 — reachable from the mesh/LAN."
    echo
    echo "Remediation:"
    echo "  1. Run: sudo ./scripts/hardening/harden_service_bindings.sh"
    echo "  2. This rebinds Docker services to 127.0.0.1 and adds UFW rules."
    echo "  3. aios-core (5680) stays on 0.0.0.0 because the Tailscale Funnel"
    echo "     proxies to it — but PoC 1.0.1 restricts WHICH endpoints the"
    echo "     Funnel exposes."
    exit 1
else
    pass "All internal services are bound to 127.0.0.1."
    exit 0
fi
