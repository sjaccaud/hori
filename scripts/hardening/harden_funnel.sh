#!/usr/bin/env bash
# PoC 1.0.1: Tailscale Serve Hardening
#
# Resets the Tailscale Serve config to expose ONLY voice + admin endpoints
# via Tailnet-only HTTPS. Nothing is exposed to the public internet (Funnel).
# This gives iOS the HTTPS it needs for Web Speech API while keeping all
# endpoints private to the Tailnet.
#
# Traces to docs/roadmap.md Tier 1A, PoC 1.0.1.
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

echo "=== Tailscale Serve Hardening (PoC 1.0.1) ==="
echo
echo "This will RESET Tailscale Serve to Tailnet-only HTTPS, exposing:"
echo "  /voice                -> proxy http://localhost:5680/voice"
echo "  /health               -> proxy http://localhost:5680/health"
echo "  /static               -> proxy http://localhost:5680/static"
echo "  /v1/voice/chat        -> proxy http://localhost:5680/v1/voice/chat"
echo "  /v1/voice/chat/stream -> proxy http://localhost:5680/v1/voice/chat/stream"
echo "  /v1/voice/chat/audio  -> proxy http://localhost:5680/v1/voice/chat/audio"
echo "  /admin                -> proxy http://localhost:5680/admin"
echo "  /admin/api            -> proxy http://localhost:5680/admin/api"
echo
echo "All other endpoints (/system/*, /chat, /v1/chat/completions, /v1/models, etc.)"
echo "will be unreachable. Funnel (public internet) will be DISABLED — everything"
echo "is Tailnet-only HTTPS. This is more secure than the previous Funnel setup."
echo
read -r -p "Proceed? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo
echo "--- Resetting Serve/Funnel config ---"

# Reset both to a clean state first.
tailscale funnel reset 2>/dev/null || true
tailscale serve reset 2>/dev/null || true

# Add all paths via Serve (Tailnet-only HTTPS, NOT public internet Funnel).
# This gives iOS the HTTPS it needs for Web Speech API without exposing
# anything to the public internet.
# --bg runs in background (non-interactive), --yes skips prompts.
# IMPORTANT: The proxy target MUST include the full path. If you omit the
# path (e.g., "http://localhost:5680" instead of "http://localhost:5680/voice"),
# the proxy strips the path and sends all requests to the root "/", which
# breaks POST endpoints (they hit the GET root route and return 405).
tailscale serve --bg --yes --set-path=/voice http://localhost:5680/voice
tailscale serve --bg --yes --set-path=/health http://localhost:5680/health
tailscale serve --bg --yes --set-path=/static http://localhost:5680/static
tailscale serve --bg --yes --set-path=/v1/voice/chat http://localhost:5680/v1/voice/chat
tailscale serve --bg --yes --set-path=/v1/voice/chat/stream http://localhost:5680/v1/voice/chat/stream
tailscale serve --bg --yes --set-path=/v1/voice/chat/audio http://localhost:5680/v1/voice/chat/audio
tailscale serve --bg --yes --set-path=/admin http://localhost:5680/admin
tailscale serve --bg --yes --set-path=/admin/api http://localhost:5680/admin/api

echo
echo "--- Verifying ---"
tailscale serve status

echo
echo -e "${GREEN}Done. Tailscale Serve is now Tailnet-only HTTPS.${NC}"
echo "Funnel (public internet) is OFF. All endpoints are private to your Tailnet."
echo
echo "Next steps:"
echo "  1. Test voice from your phone: https://$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Self",{}).get("DNSName","YOUR-TAILNET.ts.net"))' 2>/dev/null || echo 'YOUR-TAILNET.ts.net')/voice"
echo "  2. Test admin from your phone:  https://$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Self",{}).get("DNSName","YOUR-TAILNET.ts.net"))' 2>/dev/null || echo 'YOUR-TAILNET.ts.net')/admin"
echo "  3. Verify /system/state is unreachable:"
echo "     curl -s -o /dev/null -w '%{http_code}' https://$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Self",{}).get("DNSName","YOUR-TAILNET.ts.net"))' 2>/dev/null || echo 'YOUR-TAILNET.ts.net')/system/state"
echo "     (should return 000 or 404, not 200)"
echo "  4. Verify nothing is exposed to the public internet:"
echo "     tailscale funnel status  (should show 'Funnel off')"
