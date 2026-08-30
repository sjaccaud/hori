#!/usr/bin/env bash
# Install the system root broker user, sudoers, and systemd unit.
#
# Usage: sudo ./scripts/hardening/install_root_broker.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    exit 1
fi

BROKER_USER="root-broker"
BROKER_GROUP="root-broker"
ETC_DIR="/etc/system-root-broker"
LOG_DIR="/var/log/system-root-broker"
RUN_DIR="/run/system-root-broker"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVICE_SRC="${REPO_ROOT}/services/broker/system-root-broker.service"
ALLOWLIST_SRC="${REPO_ROOT}/services/broker/allowlist.py"

# The human client user must be in the broker group to connect to the socket.
CLIENT_USER="${SUDO_USER:-$(logname 2>/dev/null || true)}"
if [ -z "${CLIENT_USER}" ] || [ "${CLIENT_USER}" = "root" ]; then
    echo "ERROR: Cannot determine the client user. Run with sudo from a normal shell."
    exit 1
fi

# --- Create user and group ---
if ! getent group "${BROKER_GROUP}" >/dev/null; then
    groupadd --system "${BROKER_GROUP}"
fi
if ! getent passwd "${BROKER_USER}" >/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin \
        -g "${BROKER_GROUP}" "${BROKER_USER}"
fi
# Allow the invoking human user to connect to the broker socket.
if ! id -nG "${CLIENT_USER}" | grep -qw "${BROKER_GROUP}"; then
    usermod -aG "${BROKER_GROUP}" "${CLIENT_USER}"
    echo "Added ${CLIENT_USER} to ${BROKER_GROUP} group. Re-login to apply."
fi

# --- Create directories ---
mkdir -p "${ETC_DIR}" "${LOG_DIR}" "${RUN_DIR}"
chown "root:${BROKER_GROUP}" "${ETC_DIR}"
chmod 0750 "${ETC_DIR}"
chown "root:${BROKER_GROUP}" "${LOG_DIR}"
chmod 0750 "${LOG_DIR}"
chown "root:${BROKER_GROUP}" "${RUN_DIR}"
chmod 0755 "${RUN_DIR}"

# --- Install default allowlist ---
if [ ! -f "${ETC_DIR}/broker-allowlist.json" ]; then
    # Extract the DEFAULT allowlist from the Python module.
    # A production install may ship a hand-written JSON file instead.
    PYTHONPATH="${REPO_ROOT}" "${REPO_ROOT}/.venv/bin/python3" - <<'PY'
import json
from services.broker.allowlist import Allowlist
data = dict(Allowlist.DEFAULT)
data.setdefault("_meta", {"version": "1.0"})
with open("/etc/system-root-broker/broker-allowlist.json", "w") as f:
    json.dump(data, f, indent=2)
PY
    chown "root:${BROKER_GROUP}" "${ETC_DIR}/broker-allowlist.json"
    chmod 0640 "${ETC_DIR}/broker-allowlist.json"
fi

# --- Generate token if missing ---
SECRETS_FILE="${ETC_DIR}/broker-secrets.env"
if [ ! -f "${SECRETS_FILE}" ] || ! grep -q '^ROOT_BROKER_TOKEN=' "${SECRETS_FILE}"; then
    TOKEN="$(head -c 32 /dev/urandom | xxd -p | tr -d '\n')"
    echo "ROOT_BROKER_TOKEN=${TOKEN}" > "${SECRETS_FILE}"
    chmod 0600 "${SECRETS_FILE}"
    echo "Generated ROOT_BROKER_TOKEN. Save it if you need to configure clients."
else
    chmod 0600 "${SECRETS_FILE}"
fi

# --- Install sudoers rule for the broker itself ---
SUDOERS_FILE="/etc/sudoers.d/system-root-broker"
cat > "${SUDOERS_FILE}" <<EOF
# system-root-broker: allow only specific systemctl commands.
# This is the *only* root path for the broker user.
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl restart aios_core
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl restart aios-sherpa
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl restart aios-tool-daemon
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl restart llamacpp
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl stop aios_core
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl stop aios-sherpa
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl stop aios-tool-daemon
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl stop llamacpp
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl is-active aios_core
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl is-active aios-sherpa
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl is-active aios-tool-daemon
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl is-active llamacpp
EOF
chmod 0440 "${SUDOERS_FILE}"

# --- Install systemd unit ---
# Replace the placeholder with the real repo root before copying.
cp "${SERVICE_SRC}" /etc/systemd/system/system-root-broker.service
sed -i "s|@@REPO_ROOT@@|${REPO_ROOT}|g" /etc/systemd/system/system-root-broker.service
chown root:root /etc/systemd/system/system-root-broker.service
chmod 0644 /etc/systemd/system/system-root-broker.service

# --- Set audit log permissions ---
touch "${LOG_DIR}/audit.jsonl"
chown "root:${BROKER_GROUP}" "${LOG_DIR}/audit.jsonl"
chmod 0640 "${LOG_DIR}/audit.jsonl"

# --- Reload and enable ---
systemctl daemon-reload
systemctl enable --now system-root-broker.service

# Show token
if [ -f "${SECRETS_FILE}" ]; then
    echo
    echo "Broker token: $(grep '^ROOT_BROKER_TOKEN=' "${SECRETS_FILE}" | cut -d= -f2)"
    echo "Save this token for the admin panel and client configuration."
fi

echo
echo "Installed system-root-broker. Start with:"
echo "  sudo systemctl start system-root-broker"
