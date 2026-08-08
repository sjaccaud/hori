#!/bin/bash
# PoC 15.0a + 15.5: Install the HORI tool daemon as a systemd service.
#
# This script requires sudo. It:
#   1. Verifies the aios-worker user exists (PoC 15.0a)
#   2. Creates /run/hori with correct permissions
#   3. Creates /var/log/hori with the audit log file
#   4. Creates /tmp/hori-workspace (RW scratch space for Landlock)
#   5. Installs the systemd service file
#   6. Enables and starts the service
#
# Traces to: docs/roadmap.md Tier 2A (15.0a) + Tier 2C (15.5).
set -euo pipefail

# Auto-detect project directory (parent of this script's location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVICE_NAME="aios-tool-daemon"
SERVICE_SRC="$PROJECT_DIR/services/tool_daemon/aios-tool-daemon.service"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}.service"

echo "=== HORI Tool Daemon Installer ==="
echo ""
echo "Project directory: $PROJECT_DIR"
echo ""

# --- Check root ---
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)."
    exit 1
fi

# --- Check aios-worker user (PoC 15.0a) ---
echo "1. Checking aios-worker user..."
if ! id aios-worker &>/dev/null; then
    echo "   ERROR: aios-worker user does not exist."
    echo "   Create it with:"
    echo "     sudo useradd --system --no-create-home --shell /usr/sbin/nologin aios-worker"
    exit 1
fi

AIOS_UID=$(id -u aios-worker)
AIOS_GID=$(id -g aios-worker)
AIOS_GROUPS=$(id -Gn aios-worker)

echo "   User: aios-worker (uid=$AIOS_UID, gid=$AIOS_GID)"
echo "   Groups: $AIOS_GROUPS"

# Verify no supplementary groups (no sudo, no docker, etc.)
if [ "$(id -G aios-worker | wc -w)" -gt 1 ]; then
    echo "   WARNING: aios-worker has supplementary groups. Expected only its primary group."
    echo "   This is a security risk — supplementary groups may grant unwanted access."
fi

# Verify no login shell
AIOS_SHELL=$(getent passwd aios-worker | cut -d: -f7)
if [ "$AIOS_SHELL" != "/usr/sbin/nologin" ] && [ "$AIOS_SHELL" != "/bin/false" ]; then
    echo "   WARNING: aios-worker has shell '$AIOS_SHELL'. Expected /usr/sbin/nologin."
fi

# Verify no home directory
AIOS_HOME=$(getent passwd aios-worker | cut -d: -f6)
if [ "$AIOS_HOME" != "/nonexistent" ] && [ -n "$AIOS_HOME" ] && [ -d "$AIOS_HOME" ]; then
    echo "   WARNING: aios-worker has a home directory at '$AIOS_HOME'."
    echo "   This could expose ~/.ssh, ~/.gnupg, ~/.env to the daemon."
fi

echo "   OK"
echo ""

# --- Create /tmp/hori-workspace (Landlock RW path) ---
# Install the tmpfiles.d config so the directory is recreated at boot.
# /tmp is volatile (wiped on reboot), and ReadWritePaths requires the
# directory to exist before mount namespace setup. ExecStartPre can't
# do this (namespace setup runs first), so we use systemd-tmpfiles.
echo "2. Installing tmpfiles.d config for /tmp/hori-workspace..."
TMPFILES_SRC="$PROJECT_DIR/scripts/hardening/hori-workspace.conf"
TMPFILES_DST="/etc/tmpfiles.d/hori-workspace.conf"
if [ ! -f "$TMPFILES_SRC" ]; then
    echo "   ERROR: tmpfiles.d config not found at $TMPFILES_SRC"
    exit 1
fi
cp "$TMPFILES_SRC" "$TMPFILES_DST"
chown root:root "$TMPFILES_DST"
chmod 0644 "$TMPFILES_DST"
echo "   Installed: $TMPFILES_DST"

# Apply the tmpfiles.d config now (creates the directory immediately)
systemd-tmpfiles --create "$TMPFILES_DST"
echo "   Created /tmp/hori-workspace (aios-worker:aios-worker 1777)"
echo ""

# --- Create /var/log/hori (audit log directory) ---
echo "3. Creating audit log directory..."
mkdir -p /var/log/hori
chown root:aios-worker /var/log/hori
chmod 0750 /var/log/hori

# Create the audit log file with permission separation (red-team fix #2):
# root:aios-worker 0620 — aios-worker can append, root can read,
# aios-worker CANNOT read the log (can't see what the Sherpa sees).
touch /var/log/hori/tool_audit.jsonl
chown root:aios-worker /var/log/hori/tool_audit.jsonl
chmod 0620 /var/log/hori/tool_audit.jsonl
echo "   Created /var/log/hori/tool_audit.jsonl (root:aios-worker 0620)"
echo "   Permission separation: aios-worker can append but NOT read."
echo ""

# --- Install systemd service ---
echo "4. Installing systemd service..."
if [ ! -f "$SERVICE_SRC" ]; then
    echo "   ERROR: Service file not found at $SERVICE_SRC"
    exit 1
fi

# Replace template variables in the service file
# %h → actual home dir of the user who will run hori-core
# %i → actual username
# We detect the current user (the one who cloned the repo, not root)
HORI_USER="${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}"
HORI_HOME=$(getent passwd "$HORI_USER" | cut -d: -f6)

sed "s|%h/Projects/hori|$PROJECT_DIR|g; s|%h/.config|$HORI_HOME/.config|g; s|%i|$HORI_USER|g" "$SERVICE_SRC" > "$SERVICE_DST"
chown root:root "$SERVICE_DST"
chmod 0644 "$SERVICE_DST"
echo "   Installed: $SERVICE_DST (user=$HORI_USER, dir=$PROJECT_DIR)"
echo ""

# --- Reload systemd ---
echo "5. Reloading systemd daemon..."
systemctl daemon-reload
echo "   OK"
echo ""

# --- Enable and start ---
echo "6. Enabling and starting service..."
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 2

# --- Check status ---
echo ""
echo "=== Service Status ==="
systemctl status "$SERVICE_NAME" --no-pager || true
echo ""

# --- Verify ---
echo "=== Verification ==="
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "PASS: Service is running"
else
    echo "FAIL: Service is not running"
    echo "Check logs with: journalctl -u $SERVICE_NAME -e"
    exit 1
fi

# Check that the socket exists
if [ -S /run/hori/tool-daemon.sock ]; then
    echo "PASS: Unix domain socket exists at /run/hori/tool-daemon.sock"
else
    echo "FAIL: Socket not found at /run/hori/tool-daemon.sock"
    exit 1
fi

# Check socket permissions
SOCKET_PERMS=$(stat -c "%a %U:%G" /run/hori/tool-daemon.sock 2>/dev/null || echo "unknown")
echo "Socket permissions: $SOCKET_PERMS"

echo ""
echo "=== Installation Complete ==="
echo "The tool daemon is running as aios-worker with Landlock + seccomp."
echo ""
echo "To check status:  systemctl status $SERVICE_NAME"
echo "To view logs:     journalctl -u $SERVICE_NAME -f"
echo "To stop:          systemctl stop $SERVICE_NAME"
