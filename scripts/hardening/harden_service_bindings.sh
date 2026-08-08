#!/usr/bin/env bash
# PoC 1.0.3: Service Binding Hardening
#
# Rebinds internal Docker services from 0.0.0.0 to 127.0.0.1 so they are
# only reachable locally, not from the Tailscale mesh or LAN. Also adds
# UFW rules to deny cross-service access.
#
# Traces to: docs/roadmap.md Tier 1A, PoC 1.0.3.
#
# This is a DESTRUCTIVE operation — it modifies docker-compose files and
# restarts containers. Run audit_service_bindings.sh first.
#
# Usage: sudo ./scripts/hardening/harden_service_bindings.sh
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

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "=== Service Binding Hardening (PoC 1.0.3) ==="
echo
echo "This will:"
echo "  1. Rebind Docker services to 127.0.0.1 (n8n, Qdrant, Grafana,"
echo "     Prometheus, Node Exporter)"
echo "  2. Keep aios-core (5680) on 0.0.0.0 (Tailscale Funnel proxies to it)"
echo "  3. Keep Home Assistant on 0.0.0.0 if it uses host networking"
echo "     (it needs mDNS for IoT discovery — cannot be localhost-only)"
echo "  4. Add UFW rules to deny unauthorized cross-service access"
echo
read -r -p "Proceed? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo
echo "--- Updating docker-compose files ---"

# Helper: rebind a port mapping from "HOST:CONTAINER" to "127.0.0.1:HOST:CONTAINER"
rebind_port() {
    local file="$1"
    local port_pair="$2"  # e.g. "5678:5678"
    if grep -q "\"${port_pair}\"" "$file"; then
        sed -i "s|\"${port_pair}\"|\"127.0.0.1:${port_pair}\"|" "$file"
        echo "  Rebound $port_pair in $(basename $file)"
    fi
}

# n8n
N8N_FILE="$PROJECT_ROOT/services/n8n/docker-compose.yml"
if [ -f "$N8N_FILE" ]; then
    rebind_port "$N8N_FILE" "5678:5678"
fi

# Qdrant
QDRANT_FILE="$PROJECT_ROOT/services/qdrant/docker-compose.yml"
if [ -f "$QDRANT_FILE" ]; then
    rebind_port "$QDRANT_FILE" "6333:6333"
    rebind_port "$QDRANT_FILE" "6334:6334"
fi

# Observability (Prometheus, Grafana, Node Exporter)
OBS_FILE="$PROJECT_ROOT/services/observability/docker-compose.yml"
if [ -f "$OBS_FILE" ]; then
    rebind_port "$OBS_FILE" "9090:9090"
    rebind_port "$OBS_FILE" "3001:3000"
    rebind_port "$OBS_FILE" "9100:9100"
fi

# Open WebUI — check if it has port mappings
OWUI_FILE="$PROJECT_ROOT/services/open-webui/docker-compose.yml"
if [ -f "$OWUI_FILE" ]; then
    rebind_port "$OWUI_FILE" "3000:3000"
fi

echo
echo "--- Restarting affected containers ---"
# Restart containers to apply the new port bindings.
for svc in n8n qdrant observability open-webui; do
    dir="$PROJECT_ROOT/services/$svc"
    if [ -f "$dir/docker-compose.yml" ]; then
        echo "  Restarting $svc..."
        docker compose -f "$dir/docker-compose.yml" down 2>/dev/null || true
        docker compose -f "$dir/docker-compose.yml" up -d 2>/dev/null || \
            warn "Failed to restart $svc — check docker compose output."
    fi
done

echo
echo "--- Adding UFW rules ---"
if command -v ufw &>/dev/null; then
    # Deny access to internal service ports from non-local sources.
    # These are now 127.0.0.1-bound, but UFW adds defense-in-depth.
    for port in 5678 6333 6334 3001 9090 9100; do
        ufw deny in to any port "$port" 2>/dev/null || true
    done

    # Ensure aios-core (5680) is reachable on the Tailscale interface.
    # aios-core stays on 0.0.0.0 so the Tailscale Funnel can proxy to it,
    # but it should only be reachable from the tailnet, not the LAN.
    ufw allow in on tailscale0 to any port 5680 2>/dev/null || true

    echo "  UFW rules added for internal service ports."
    echo "  UFW rule added: allow 5680 on tailscale0 (aios-core)"
    ufw status
else
    warn "ufw not installed — skipping firewall rules."
fi

echo
echo "--- Verification ---"
echo "Services now on 127.0.0.1:"
ss -tlnp 2>/dev/null | grep '127\.0\.0\.1:' | awk '{print "  "$4}' | sort || true
echo
echo "Services still on 0.0.0.0 (should be aios-core, SSH, Tailscale only):"
ss -tlnp 2>/dev/null | grep '0\.0\.0\.0:' | awk '{print "  "$4}' | sort || true

echo
echo -e "${GREEN}Done. Internal services are now localhost-only.${NC}"
echo
echo "NOTE: Home Assistant (8123) may still be on 0.0.0.0 if it uses host"
echo "networking — it needs mDNS for IoT discovery. If you want to restrict"
echo "it, configure Home Assistant's network component instead, or add a UFW"
echo "rule: ufw deny in to any port 8123"
