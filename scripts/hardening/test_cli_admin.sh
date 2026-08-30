#!/usr/bin/env bash
# Verification script for the cli-admin break-glass + sudoers hygiene slice.
#
# Run as root (or with sudo) after install_cli_admin.sh.
#
# Usage: sudo ./scripts/hardening/test_cli_admin.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    exit 1
fi

# Detect the primary user we expect to be hardened.
PRIMARY_USER="${SUDO_USER:-$(logname 2>/dev/null || echo "")}"
if [ -z "$PRIMARY_USER" ] || [ "$PRIMARY_USER" = "root" ]; then
    echo "ERROR: Could not detect the primary user. Run with sudo from the user account."
    exit 1
fi

REMOVED_GROUPS=(lxd ollama lpadmin cdrom dip plugdev)

echo "=== Verifying cli-admin / sudoers hygiene ==="
echo

# cli-admin exists and has a real shell + sudo membership.
if ! id -u cli-admin >/dev/null 2>&1; then
    echo "FAIL: cli-admin user does not exist"
    exit 1
fi
if [ "$(getent passwd cli-admin | cut -d: -f7)" != "/bin/bash" ]; then
    echo "FAIL: cli-admin does not have /bin/bash shell"
    exit 1
fi
if ! id -nG cli-admin | grep -qw sudo; then
    echo "FAIL: cli-admin is not in the sudo group"
    exit 1
fi
echo "  cli-admin exists, has bash shell, and is in sudo group."

# Primary user is no longer in the removed groups.
echo "  Checking $PRIMARY_USER group membership..."
for group in "${REMOVED_GROUPS[@]}"; do
    if id -nG "$PRIMARY_USER" | grep -qw "$group"; then
        echo "FAIL: $PRIMARY_USER is still in $group"
        exit 1
    fi
done
echo "  $PRIMARY_USER is not in ${REMOVED_GROUPS[*]}."

# Stale sudoers files are gone.
STALE=(
    /etc/sudoers.d/10-devin
    /etc/sudoers.d/10-devin.bak
    /etc/sudoers.d/10-devin.bak.*
    /etc/sudoers.d/aios-admin
    /etc/sudoers.d/aios-admin.bak
    /etc/sudoers.d/aios-admin.bak.*
    /etc/sudoers.d/hori-admin
    /etc/sudoers.d/hori-admin.bak
    /etc/sudoers.d/hori-admin.bak.*
    "/etc/sudoers.d/${PRIMARY_USER}-shutdown"
    /etc/sudoers.d/nopasswd-suspend
    /etc/sudoers.d/pyxx
    /etc/sudoers.d/pyxx-fail2ban
)

echo "  Checking for stale /etc/sudoers.d/ fragments..."
shopt -s nullglob
for f in "${STALE[@]}"; do
    if [ -f "$f" ]; then
        echo "FAIL: stale file still present: $f"
        exit 1
    fi
done
echo "  no stale fragments found."

# Expected active files.
for f in /etc/sudoers.d/README /etc/sudoers.d/hori-user /etc/sudoers.d/system-root-broker; do
    if [ ! -f "$f" ]; then
        echo "FAIL: expected file missing: $f"
        exit 1
    fi
done
echo "  expected files present: README, hori-user, system-root-broker."

# visudo syntax is valid.
visudo -c
echo "  visudo -c passed."

# cli-admin can list its sudo privileges (proves sudo group works).
sudo -l -U cli-admin >/dev/null
echo "  cli-admin can use sudo."

echo
echo "All checks passed."
