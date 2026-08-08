#!/usr/bin/env bash
# PoC 1.0.2: Credential Migration to systemd EnvironmentFile
#
# Moves the GEMINI_API_KEY from the plaintext .env file to a root-owned
# EnvironmentFile at /etc/hori/secrets.env (mode 0600), and updates the
# aios_core.service unit to load it from there.
#
# Traces to: docs/roadmap.md Tier 1A, PoC 1.0.2.
# Traces to: docs/system_security_audit.md "Audit and Rotate Credentials".
#
# This is a DESTRUCTIVE operation — it moves secrets and modifies the systemd
# unit. Run audit_credentials.sh first.
#
# Usage: sudo ./scripts/hardening/migrate_credentials.sh
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
ENV_FILE="$PROJECT_ROOT/.env"
SECRETS_DIR="/etc/hori"
SECRETS_FILE="$SECRETS_DIR/secrets.env"
SERVICE_FILE="/etc/systemd/system/aios_core.service"

echo "=== Credential Migration (PoC 1.0.2) ==="
echo
echo "This will:"
echo "  1. Create $SECRETS_DIR (root-owned, mode 0700)"
echo "  2. Move GEMINI_API_KEY from $ENV_FILE to $SECRETS_FILE (mode 0600)"
echo "  3. Add EnvironmentFile= to $SERVICE_FILE"
echo "  4. Remove the key from the .env file (keep a backup)"
echo "  5. Reload systemd and restart aios-core"
echo
echo "NOTE: You should ROTATE the Gemini API key BEFORE running this."
echo "      Go to https://aistudio.google.com/app/apikey, create a new key,"
echo "      and paste it when prompted."
echo
read -r -p "Proceed? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# --- Step 1: Create secrets directory ---
echo
echo "--- Creating secrets directory ---"
mkdir -p "$SECRETS_DIR"
chmod 0700 "$SECRETS_DIR"
chown root:root "$SECRETS_DIR"
echo "Created $SECRETS_DIR (root:root, 0700)"

# --- Step 2: Read the current key (or prompt for a new one) ---
echo
echo "--- Reading current GEMINI_API_KEY ---"
CURRENT_KEY=""
if [ -f "$ENV_FILE" ]; then
    CURRENT_KEY=$(grep -E '^GEMINI_API_KEY=' "$ENV_FILE" | cut -d= -f2- || true)
fi

if [ -n "$CURRENT_KEY" ]; then
    echo "Found existing key in .env (length: ${#CURRENT_KEY})"
    read -r -p "Use this key, or paste a NEW (rotated) key? [use/paste] " choice
    if [[ "$choice" =~ ^[Pp]$ ]]; then
        read -r -p "Paste new GEMINI_API_KEY: " CURRENT_KEY
    fi
else
    echo "No existing key found. Paste a new GEMINI_API_KEY."
    read -r -p "GEMINI_API_KEY: " CURRENT_KEY
fi

if [ -z "$CURRENT_KEY" ]; then
    echo -e "${RED}ERROR: No key provided. Aborting.${NC}"
    exit 1
fi

# --- Step 3: Write secrets file ---
echo
echo "--- Writing secrets file ---"
cat > "$SECRETS_FILE" <<EOF
# AIOS secrets — loaded by aios_core.service via EnvironmentFile=
# This file is root-owned and mode 0600. Only root and the aios-core
# service can read it. Do NOT commit this file to git.
GEMINI_API_KEY=$CURRENT_KEY
EOF
chmod 0600 "$SECRETS_FILE"
chown root:$SUDO_USER "$SECRETS_FILE"
echo "Wrote $SECRETS_FILE (root:$SUDO_USER, 0600)"

# --- Step 4: Update the systemd unit ---
echo
echo "--- Updating systemd unit ---"
if [ -f "$SERVICE_FILE" ]; then
    # Backup the current unit
    cp "$SERVICE_FILE" "${SERVICE_FILE}.bak"
    echo "Backed up $SERVICE_FILE to ${SERVICE_FILE}.bak"

    # Add EnvironmentFile= if not already present
    if ! grep -q 'EnvironmentFile=' "$SERVICE_FILE"; then
        # Insert after the FIRST Environment= line only (0~1 = first match only)
        sed -i "0~1{/^Environment=/a EnvironmentFile=$SECRETS_FILE
}" "$SERVICE_FILE"
        echo "Added EnvironmentFile=$SECRETS_FILE to $SERVICE_FILE"
    else
        echo "EnvironmentFile= already present in $SERVICE_FILE"
    fi
else
    warn "$SERVICE_FILE not found — skipping unit update."
    warn "Add 'EnvironmentFile=$SECRETS_FILE' to your aios-core service unit manually."
fi

# --- Step 5: Remove key from .env (keep backup) ---
echo
echo "--- Removing key from .env ---"
if [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "${ENV_FILE}.bak"
    echo "Backed up $ENV_FILE to ${ENV_FILE}.bak"
    # Comment out the GEMINI_API_KEY line. Use | as the sed delimiter
    # because the replacement text contains / (the path /etc/hori/secrets.env).
    sed -i 's|^GEMINI_API_KEY=|# GEMINI_API_KEY migrated to /etc/hori/secrets.env\n# GEMINI_API_KEY=|' "$ENV_FILE"
    echo "Commented out GEMINI_API_KEY in $ENV_FILE"
fi

# --- Step 6: Reload and restart ---
echo
echo "--- Reloading systemd ---"
systemctl daemon-reload
echo "Restarting aios-core..."
systemctl restart aios_core.service
sleep 2
systemctl status aios_core.service --no-pager || true

echo
echo -e "${GREEN}Done. GEMINI_API_KEY is now in $SECRETS_FILE (root-owned, 0600).${NC}"
echo
echo "Verify:"
echo "  1. systemctl show aios_core.service | grep EnvironmentFile"
echo "  2. sudo cat $SECRETS_FILE  (should show the key)"
echo "  3. cat $ENV_FILE  (should NOT show the key)"
echo "  4. curl http://localhost:5680/health  (aios-core should still work)"
echo
echo "Next steps:"
echo "  - Rotate the Telegram bot token if needed (via @BotFather)"
echo "  - Audit n8n credentials: docker exec n8n_aios sqlite3 /home/node/.n8n/database.sqlite \"SELECT name, type FROM credential_entity;\""
echo "  - Revoke unused Home Assistant long-lived tokens"
