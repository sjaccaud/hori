#!/usr/bin/env bash
# Remove legacy admin-panel sudoers and (re)generate the admin auth token.
#
# Service restarts no longer use sudo; they are routed through the
# system-root-broker. This script removes the old NOPASSWD admin sudoers
# and ensures /etc/hori/secrets.env contains a bearer token for
# /admin/api/* endpoints.
#
# IMPORTANT: Only run this after aios_core has been restarted with the
#            /etc/systemd/system/aios_core.service.d/20-root-broker.conf
#            drop-in (created by scripts/hardening/install_root_broker.sh).
#
# Usage: sudo ./scripts/hardening/setup_admin_sudoers.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    echo "  sudo $0"
    exit 1
fi

# Detect the user who will run HORI (the one who invoked sudo)
HORI_USER="${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}"
SECRETS_FILE="/etc/aios/secrets.env"
# Legacy admin-panel sudoers that must be removed now that the broker handles restarts
LEGACY_SUDOERS=(
    /etc/sudoers.d/aios-admin
    /etc/sudoers.d/hori-admin
)

echo "=== HORI Admin Auth Token + Legacy Sudoers Cleanup ==="
echo
echo "User: $HORI_USER"
echo

# Service restarts now go through the system root broker. The admin panel
# (services/aios_core/main.py) calls RootBrokerClient instead of sudo.
# Remove the legacy admin-panel sudoers that are no longer needed.
for legacy in "${LEGACY_SUDOERS[@]}"; do
    if [ -f "$legacy" ]; then
        echo "Removing legacy admin sudoers: $legacy"
        rm -f "$legacy"
    fi
done

# Safety check: the aios_core drop-in must exist, otherwise the admin panel
# cannot reach the broker socket.
AIOS_DROPIN="/etc/systemd/system/aios_core.service.d/20-root-broker.conf"
if [ ! -f "$AIOS_DROPIN" ]; then
    echo
    echo "WARNING: $AIOS_DROPIN not found."
    echo "  The admin panel cannot use the broker until the aios_core service"
    echo "  is configured with SupplementaryGroups=root-broker."
    echo "  Run: sudo bash scripts/hardening/install_root_broker.sh"
    echo "  Then: sudo systemctl restart aios_core"
fi

# Validate the remaining sudoers configuration
visudo -c

# --- Admin auth token ---
# Ensure a bearer token exists in /etc/hori/secrets.env. If one already
# exists, leave it alone so existing admin panel sessions keep working.
# The aios_core systemd unit reads this via EnvironmentFile=.
# The admin panel JS prompts for this token on login.
echo
echo "Admin auth token in $SECRETS_FILE..."

mkdir -p /etc/aios

if grep -qE '^(HORI_ADMIN_TOKEN|AIOS_ADMIN_TOKEN)=' "$SECRETS_FILE" 2>/dev/null; then
    echo "  Existing token preserved. No change."
else
    # Generate 32 bytes of random hex as the token
    ADMIN_TOKEN=$(head -c 32 /dev/urandom | xxd -p | tr -d '\n')
    {
        echo "HORI_ADMIN_TOKEN=${ADMIN_TOKEN}"
        echo "AIOS_ADMIN_TOKEN=${ADMIN_TOKEN}"
    } >> "$SECRETS_FILE"
    echo "  Generated new admin token."
    echo "  Token: ${ADMIN_TOKEN}"
    echo "  (Save this — you'll need to paste it into the admin panel login.)"
fi

chown root:"$HORI_USER" "$SECRETS_FILE"
chmod 0600 "$SECRETS_FILE"

echo "✓ Admin token in place."
echo
echo "Done. The admin panel uses the root broker for service restarts."
