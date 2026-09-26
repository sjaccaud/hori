#!/bin/bash
# install_boot_health.sh — install + enable hori-boot-health.service.
#
# The unit runs notify_health.sh once per boot (after spine services and
# network), which executes post_reboot_health.sh and pushes the result
# to telegram + ntfy + desktop notification.
#
# Usage: sudo scripts/hardening/install_boot_health.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
UNIT_SRC="${REPO_ROOT}/services/systemd/hori-boot-health.service"
UNIT_DST="/etc/systemd/system/hori-boot-health.service"

if [ "$(id -u)" -ne 0 ]; then
    echo "error: must run as root (sudo $0)" >&2
    exit 1
fi

[ -f "$UNIT_SRC" ] || { echo "error: unit source missing: $UNIT_SRC" >&2; exit 1; }
[ -x "${SCRIPT_DIR}/notify_health.sh" ] || chmod 0755 "${SCRIPT_DIR}/notify_health.sh"
[ -x "${SCRIPT_DIR}/post_reboot_health.sh" ] || chmod 0755 "${SCRIPT_DIR}/post_reboot_health.sh"

cp "$UNIT_SRC" "$UNIT_DST"
sed -i "s|@@REPO_ROOT@@|${REPO_ROOT}|g" "$UNIT_DST"
chown root:root "$UNIT_DST"
chmod 0644 "$UNIT_DST"

systemctl daemon-reload
systemctl enable hori-boot-health.service

echo ""
echo "Installed + enabled hori-boot-health.service (runs ~90s after boot)."
echo ""
echo "Notification config resolution order:"
echo "  1. /etc/hori/notify.env  (HORI_NOTIFY_CONF override)"
echo "  2. ${HOME}/Projects/idea_factory/config/notify.conf (fallback)"
echo ""
echo "Test the channels only:    ${SCRIPT_DIR}/notify_health.sh --selftest"
echo "Test the whole path now:   systemctl start hori-boot-health.service"
echo "Read a run's output after: journalctl -u hori-boot-health"
