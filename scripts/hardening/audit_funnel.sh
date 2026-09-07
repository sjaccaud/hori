#!/usr/bin/env bash
# Tailscale Serve Audit — SLICE-13-REMOTE-MCP-TOOLS
#
# Audits the current Tailscale Serve/Funnel configuration to verify:
#   1. Funnel (public internet) is OFF
#   2. Serve is Tailnet-only
#   3. Only the expected ports/paths are exposed
#   4. Port 443 has no catch-all route
#   5. Port 8444 is configured for nano
#
# This is a READ-ONLY audit. Run harden_funnel.sh to apply restrictions.
#
# Usage: ./scripts/hardening/audit_funnel.sh
#
# Exit codes:
#   0 — audit passed
#   1 — audit failed
#   2 — audit could not run
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo "=== Tailscale Serve/Funnel Audit (SLICE-13) ==="
echo

if ! command -v tailscale &>/dev/null; then
    echo -e "${RED}ERROR: tailscale command not found.${NC}"
    exit 2
fi

if ! tailscale status &>/dev/null; then
    echo -e "${RED}ERROR: Tailscale is not running.${NC}"
    exit 2
fi

# --- Funnel must be off ---
echo "--- Funnel status (should be OFF) ---"
FUNNEL_OUTPUT=$(tailscale funnel status 2>&1 || true)
echo "$FUNNEL_OUTPUT"
echo

if echo "$FUNNEL_OUTPUT" | grep -q "Funnel on"; then
    fail "Funnel is ON — endpoints are exposed to the public internet!"
    exit 1
else
    pass "Funnel is OFF."
fi
echo

# --- Get JSON status ---
STATUS_JSON=$(tailscale serve status --json 2>&1 || true)
if ! echo "$STATUS_JSON" | grep -q '"TCP"'; then
    warn "Serve is not configured."
    exit 0
fi

# --- Validate ports and paths using Python ---
python3 - "$STATUS_JSON" <<'PY'
import json
import sys
import re

status = json.loads(sys.argv[1])
web = status.get("Web", {})
tcp = status.get("TCP", {})

def host_for_port(port):
    for host in web:
        if host.endswith(f":{port}"):
            return host
    return None

failures = []

# Expected ports for HTTPS
expected_ports = {443, 3443, 8443, 8444}
found_ports = set(int(p) for p in tcp.keys())
missing_ports = expected_ports - found_ports
extra_ports = found_ports - expected_ports
if missing_ports:
    failures.append(f"Missing HTTPS ports: {missing_ports}")
if extra_ports:
    failures.append(f"Unexpected extra HTTPS ports: {extra_ports}")

# Helpers to get handlers for a port
def handlers_for(port):
    host = host_for_port(port)
    if not host:
        return {}
    return web.get(host, {}).get("Handlers", {})

# Port 3443: must be a catch-all to Open-WebUI
h3443 = handlers_for(3443)
if h3443.get("/", {}).get("Proxy") != "http://127.0.0.1:3000":
    failures.append("Port 3443 must have a catch-all proxy to http://127.0.0.1:3000")

# Port 8443: raw llama-server
h8443 = handlers_for(8443)
if h8443.get("/", {}).get("Proxy") != "http://localhost:8080":
    failures.append("Port 8443 must have a catch-all proxy to http://localhost:8080")

# Port 8444: nano
h8444 = handlers_for(8444)
if h8444.get("/", {}).get("Proxy") != "http://localhost:8090":
    failures.append("Port 8444 must have a catch-all proxy to http://localhost:8090")

# Port 443: explicit path allowlist, no catch-all
h443 = handlers_for(443)
allowed_443 = {
    "/voice", "/chat", "/v1/chat/completions",
    "/v1/voice/chat", "/v1/voice/chat/stream", "/v1/voice/chat/audio",
    "/v1/audio/voices", "/v1/audio/speech",
    "/v1/wake", "/v1/presence",
    "/health", "/static", "/admin", "/admin/api", "/mcp",
}

if "/" in h443:
    failures.append("Port 443 has a catch-all route '/' — must be path-specific")

missing_443 = allowed_443 - set(h443.keys())
if missing_443:
    failures.append(f"Port 443 missing allowed paths: {missing_443}")

extra_443 = set(h443.keys()) - allowed_443
if extra_443:
    failures.append(f"Port 443 has disallowed extra paths: {extra_443}")

# Every 443 path must proxy to localhost:5680 (or 127.0.0.1:5680)
for path, handler in h443.items():
    proxy = handler.get("Proxy", "")
    if not re.match(r"http://(127\.0\.0\.1|localhost):5680", proxy):
        failures.append(f"Port 443 path {path} proxies to unexpected target: {proxy}")

if failures:
    print("\n".join(f"FAIL: {f}" for f in failures))
    sys.exit(1)
else:
    print("PASS: Port and path configuration is correct.")
    sys.exit(0)
PY

AUDIT_EXIT=$?
echo
if [ "$AUDIT_EXIT" -ne 0 ]; then
    fail "Serve configuration does not match the allowlist."
    echo
    echo "Remediation:"
    echo "  sudo ./scripts/hardening/harden_funnel.sh"
    exit 1
fi

pass "Serve is Tailnet-only and uses the expected port/path allowlist."
exit 0
