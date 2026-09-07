#!/usr/bin/env bash
# Tailscale Serve Hardening — SLICE-13-REMOTE-MCP-TOOLS
#
# Resets Tailscale Serve to a strict, port-aware, Tailnet-only HTTPS config:
#   443  — aios_core with an explicit path allowlist (voice, chat, admin, MCP)
#   3443 — Open-WebUI catch-all
#   8443 — raw llama-server router catch-all
#   8444 — nano (Llama-3.2-3B) catch-all
#
# Nothing is exposed to the public internet (Funnel is off). All remote
# clients must be on the Tailnet and tagged/ACL-controlled.
#
# Traces to .devin/prd/remote-mcp-tools.md and docs/roadmap.md Tier 1A.
#
# This is a DESTRUCTIVE operation — it overwrites the current Serve/Funnel config.
# Run audit_funnel.sh first to see what will change.
#
# Usage: sudo ./scripts/hardening/harden_funnel.sh
#
# Exit codes:
#   0 — Serve hardened successfully
#   1 — hardening failed
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

echo "=== Tailscale Serve Hardening (SLICE-13) ==="
echo
echo "This will RESET Tailscale Serve to a Tailnet-only, port-aware config:"
echo "  https://<tailnet>:443"
echo "    /voice                -> http://localhost:5680/voice"
echo "    /chat                 -> http://localhost:5680/chat"
echo "    /v1/chat/completions  -> http://localhost:5680/v1/chat/completions"
echo "    /v1/voice/*           -> http://localhost:5680/v1/voice/*"
echo "    /v1/audio/*           -> http://localhost:5680/v1/audio/*"
echo "    /v1/wake              -> http://localhost:5680/v1/wake"
echo "    /v1/presence          -> http://localhost:5680/v1/presence"
echo "    /health               -> http://localhost:5680/health"
echo "    /static               -> http://localhost:5680/static"
echo "    /admin                -> http://localhost:5680/admin"
echo "    /admin/api            -> http://localhost:5680/admin/api"
echo "    /mcp                  -> http://localhost:5680/mcp"
echo "  https://<tailnet>:3443  -> Open-WebUI (http://127.0.0.1:3000)"
echo "  https://<tailnet>:8443  -> raw llama-server router (http://localhost:8080)"
echo "  https://<tailnet>:8444  -> nano (http://localhost:8090)"
echo
echo "Funnel (public internet) will be DISABLED."
echo "All other paths/ports will be removed."
echo
read -r -p "Proceed? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo
echo "--- Resetting Serve/Funnel config ---"

tailscale funnel reset 2>/dev/null || true
tailscale serve reset 2>/dev/null || true

# --- Port 3443: Open-WebUI catch-all ---
tailscale serve --bg --yes --https=3443 --set-path=/ http://127.0.0.1:3000

# --- Port 8443: raw llama-server router ---
tailscale serve --bg --yes --https=8443 --set-path=/ http://localhost:8080

# --- Port 8444: nano (dedicated Llama-3.2-3B) ---
tailscale serve --bg --yes --https=8444 --set-path=/ http://localhost:8090

# --- Port 443: aios_core with explicit path allowlist ---
# IMPORTANT: The proxy target MUST include the full path. Omitting the path
# (e.g. "http://localhost:5680" instead of "http://localhost:5680/voice")
# strips the route and breaks POST endpoints.
tailscale serve --bg --yes --https=443 --set-path=/voice http://localhost:5680/voice
tailscale serve --bg --yes --https=443 --set-path=/chat http://localhost:5680/chat
tailscale serve --bg --yes --https=443 --set-path=/v1/chat/completions http://localhost:5680/v1/chat/completions
tailscale serve --bg --yes --https=443 --set-path=/v1/voice/chat http://localhost:5680/v1/voice/chat
tailscale serve --bg --yes --https=443 --set-path=/v1/voice/chat/stream http://localhost:5680/v1/voice/chat/stream
tailscale serve --bg --yes --https=443 --set-path=/v1/voice/chat/audio http://localhost:5680/v1/voice/chat/audio
tailscale serve --bg --yes --https=443 --set-path=/v1/audio/voices http://localhost:5680/v1/audio/voices
tailscale serve --bg --yes --https=443 --set-path=/v1/audio/speech http://localhost:5680/v1/audio/speech
tailscale serve --bg --yes --https=443 --set-path=/v1/wake http://localhost:5680/v1/wake
tailscale serve --bg --yes --https=443 --set-path=/v1/presence http://localhost:5680/v1/presence
tailscale serve --bg --yes --https=443 --set-path=/health http://localhost:5680/health
tailscale serve --bg --yes --https=443 --set-path=/static http://localhost:5680/static
tailscale serve --bg --yes --https=443 --set-path=/admin http://localhost:5680/admin
tailscale serve --bg --yes --https=443 --set-path=/admin/api http://localhost:5680/admin/api
# MCP remote surface — prefix routes /mcp/sse and /mcp/messages to aios_core.
tailscale serve --bg --yes --https=443 --set-path=/mcp http://localhost:5680/mcp

echo
echo "--- Verifying ---"
tailscale serve status

echo
echo -e "${GREEN}Done. Tailscale Serve is now Tailnet-only HTTPS.${NC}"
echo "Funnel (public internet) is OFF. All endpoints are private to your Tailnet."
