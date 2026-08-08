#!/usr/bin/env bash
# PoC 8.5: Install the scheduled consolidation systemd timer + service.
#
# Copies the service and timer files to /etc/systemd/system/, enables the
# timer, and verifies it's scheduled. Traces to docs/roadmap.md Tier 1B, PoC 8.5.
#
# Usage: sudo ./scripts/hardening/install_consolidation_timer.sh
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}ERROR: This script must be run as root (sudo).${NC}"
    echo "  sudo $0"
    exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVICE_SRC="$PROJECT_ROOT/services/systemd/aios-consolidation.service"
TIMER_SRC="$PROJECT_ROOT/services/systemd/aios-consolidation.timer"
SERVICE_DST="/etc/systemd/system/aios-consolidation.service"
TIMER_DST="/etc/systemd/system/aios-consolidation.timer"

echo "=== Install AIOS Memory Consolidation Timer (PoC 8.5) ==="
echo

# --- Verify source files exist ---
for f in "$SERVICE_SRC" "$TIMER_SRC"; do
    if [ ! -f "$f" ]; then
        echo -e "${RED}ERROR: Missing $f${NC}"
        exit 1
    fi
done

# --- Copy files ---
echo "Copying service file..."
cp "$SERVICE_SRC" "$SERVICE_DST"
echo "Copying timer file..."
cp "$TIMER_SRC" "$TIMER_DST"

# --- Reload systemd daemon ---
echo "Reloading systemd daemon..."
systemctl daemon-reload

# --- Enable and start the timer ---
echo "Enabling timer..."
systemctl enable aios-consolidation.timer
systemctl start aios-consolidation.timer

# --- Verify ---
echo
echo "=== Verification ==="
echo "--- Timer status ---"
systemctl status aios-consolidation.timer --no-pager || true
echo
echo "--- Next scheduled run ---"
systemctl list-timers aios-consolidation.timer --no-pager || true
echo
echo "--- To run consolidation manually now ---"
echo "  sudo systemctl start aios-consolidation.service"
echo "  journalctl -u aios-consolidation.service -f"
echo
echo "--- To view consolidation logs ---"
echo "  journalctl -u aios-consolidation.service --since today"
echo
echo -e "${GREEN}Done. Consolidation will run nightly at 03:00.${NC}"
