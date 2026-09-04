#!/bin/bash
# Batch the remaining hardening steps that require root.
# Run once with: sudo ./scripts/hardening/batch_privileged.sh
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}ERROR: This script must be run as root (sudo).${NC}"
    exit 1
fi

SECRETS=/etc/aios/secrets.env
HORI_SECRETS=/etc/hori/secrets.env
HORI_YAML=$(find /home -maxdepth 4 -name hori.yaml -path '*/.config/hori/hori.yaml' 2>/dev/null | head -1)

echo "=== AIOS hardening batch (root required) ==="

# --- 1. Admin token for aios_core /admin endpoints ---
# aios_core checks AIOS_ADMIN_TOKEN first, then HORI_ADMIN_TOKEN.
mkdir -p "$(dirname "$SECRETS")" "$(dirname "$HORI_SECRETS")"
touch "$SECRETS" "$HORI_SECRETS"
chmod 0600 "$SECRETS" "$HORI_SECRETS"

if ! grep -qE '^AIOS_ADMIN_TOKEN=' "$SECRETS" 2>/dev/null; then
    ADMIN_TOKEN=$(openssl rand -hex 32)
    echo "AIOS_ADMIN_TOKEN=$ADMIN_TOKEN" >> "$SECRETS"
    echo "HORI_ADMIN_TOKEN=$ADMIN_TOKEN" >> "$HORI_SECRETS"
    echo -e "${GREEN}Set AIOS_ADMIN_TOKEN / HORI_ADMIN_TOKEN.${NC}"
    echo "  Find the token in $SECRETS (root-only) if you need to paste it into the /admin panel."
else
    echo -e "${YELLOW}AIOS_ADMIN_TOKEN already set in $SECRETS; leaving it unchanged.${NC}"
fi

if ! grep -qE '^HORI_SERVICE_HOST=' "$SECRETS" 2>/dev/null; then
    echo "HORI_SERVICE_HOST=127.0.0.1" >> "$SECRETS"
    echo -e "${GREEN}Set HORI_SERVICE_HOST=127.0.0.1 so aios-core listens only on localhost.${NC}"
else
    echo -e "${YELLOW}HORI_SERVICE_HOST already set in $SECRETS; leaving it unchanged.${NC}"
fi

# If a hori.yaml exists, keep admin.token in sync for consistency.
if [ -f "$HORI_YAML" ]; then
    if command -v yq >/dev/null 2>&1; then
        yq e -i ".admin.token = \"$(grep '^AIOS_ADMIN_TOKEN=' "$SECRETS" | cut -d= -f2)\"" "$HORI_YAML"
        chmod 0600 "$HORI_YAML"
        echo -e "${GREEN}Updated $HORI_YAML admin.token.${NC}"
    else
        echo -e "${YELLOW}yq not found; not updating $HORI_YAML. The env token is enough for aios_core.${NC}"
    fi
fi

# --- 2. Restart llama-server (now bound to 127.0.0.1) and aios_core for the token ---
echo "Restarting llama-server and aios_core..."
systemctl restart llamacpp aios_core

# --- 3. Disable Ubuntu telemetry ---
echo "Disabling Ubuntu insights telemetry..."
for unit in ubuntu-insights-collect.timer ubuntu-insights-upload.timer; do
    if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
        systemctl stop "$unit" 2>/dev/null || true
        systemctl disable --now "$unit" 2>/dev/null || true
        systemctl mask "$unit" 2>/dev/null || true
        echo "  $unit disabled + masked"
    else
        echo "  $unit not found, skipping"
    fi
done

# --- 4. Enable whole-Ubuntu restic backups ---
echo "Enabling whole-Ubuntu restic backup timers..."
for unit in restic-ubuntu-backup.timer restic-ubuntu-prune.timer; do
    if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
        systemctl enable --now "$unit" 2>/dev/null || true
        systemctl start "$unit" 2>/dev/null || true
        echo "  $unit enabled/started"
    else
        echo "  ${YELLOW}$unit not found, skipping${NC}"
    fi
done

# --- 5. Verify backup target is mounted ---
if mountpoint -q /mnt/8TB 2>/dev/null; then
    echo -e "${GREEN}/mnt/8TB is mounted.${NC}"
else
    echo -e "${RED}WARNING: /mnt/8TB is not mounted. Whole-Ubuntu backups may fail until it is available.${NC}"
fi

echo
echo -e "${GREEN}Done. Run 'make health' to verify the assistant stack, and check the next backup time with 'systemctl list-timers restic-ubuntu-backup'.${NC}"
