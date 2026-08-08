#!/usr/bin/env bash
# Setup NOPASSWD sudoers rule + admin auth token for the HORI admin panel.
#
# Two things happen here:
# 1. Sudoers rule: allows the HORI user to restart/status specific HORI
#    services without a password (restricted to an allowlist).
# 2. Admin token: generates a random bearer token in /etc/hori/secrets.env.
#    The admin panel requires this token for all /admin/api/* calls.
#    Without it, the admin API is fail-closed (403).
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
SUDOERS_FILE="/etc/sudoers.d/hori-admin"
SECRETS_FILE="/etc/hori/secrets.env"

echo "=== HORI Admin Sudoers + Auth Token Setup ==="
echo
echo "User: $HORI_USER"
echo
echo "Creating $SUDOERS_FILE with NOPASSWD rules for:"
echo "  - systemctl restart/status aios_core"
echo "  - systemctl restart/status aios-sherpa"
echo "  - systemctl restart/status aios-tool-daemon"
echo "  - systemctl restart/status llamacpp"
echo

cat > "$SUDOERS_FILE" << EOF
# HORI Admin Panel — service control without password.
# Allows the admin web panel (running as $HORI_USER) to restart/status
# HORI services. Restricted to an allowlist of service names.
# Traces to: services/aios_core/main.py admin_service_control()
$HORI_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart aios_core
$HORI_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart aios-sherpa
$HORI_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart aios-tool-daemon
$HORI_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart llamacpp
$HORI_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl status aios_core
$HORI_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl status aios-sherpa
$HORI_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl status aios-tool-daemon
$HORI_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl status llamacpp
EOF

chmod 0440 "$SUDOERS_FILE"

# Validate the sudoers file
if visudo -cf "$SUDOERS_FILE"; then
    echo "✓ Sudoers file valid and installed."
else
    echo "✗ ERROR: Sudoers file validation failed. Removing."
    rm -f "$SUDOERS_FILE"
    exit 1
fi

# --- Admin auth token ---
# Generate a random token and add it to /etc/hori/secrets.env.
# The aios_core systemd unit reads this via EnvironmentFile=.
# The admin panel JS prompts for this token on login.
echo
echo "Generating admin auth token in $SECRETS_FILE..."

mkdir -p /etc/hori
# Generate 32 bytes of random hex as the token
ADMIN_TOKEN=$(head -c 32 /dev/urandom | xxd -p | tr -d '\n')

# Check if HORI_ADMIN_TOKEN already exists in the secrets file
if grep -q "^HORI_ADMIN_TOKEN=" "$SECRETS_FILE" 2>/dev/null; then
    echo "  HORI_ADMIN_TOKEN already exists in $SECRETS_FILE — replacing."
    sed -i "s|^HORI_ADMIN_TOKEN=.*|HORI_ADMIN_TOKEN=${ADMIN_TOKEN}|" "$SECRETS_FILE"
else
    echo "HORI_ADMIN_TOKEN=${ADMIN_TOKEN}" >> "$SECRETS_FILE"
fi

# Also set AIOS_ADMIN_TOKEN for backward compatibility
if grep -q "^AIOS_ADMIN_TOKEN=" "$SECRETS_FILE" 2>/dev/null; then
    sed -i "s|^AIOS_ADMIN_TOKEN=.*|AIOS_ADMIN_TOKEN=${ADMIN_TOKEN}|" "$SECRETS_FILE"
else
    echo "AIOS_ADMIN_TOKEN=${ADMIN_TOKEN}" >> "$SECRETS_FILE"
fi

chown root:"$HORI_USER" "$SECRETS_FILE"
chmod 0600 "$SECRETS_FILE"

echo "✓ Admin token installed."
echo
echo "  Token: ${ADMIN_TOKEN}"
echo "  (Save this — you'll need to paste it into the admin panel login.)"
echo
echo "  NOTE: Restart aios_core to pick up the new env var:"
echo "    sudo systemctl restart aios_core"

echo
echo "Done. The admin panel now requires a bearer token for all API calls."
