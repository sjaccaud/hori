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

# --- Install / update default allowlist ---
# Merge the current Allowlist.DEFAULT into any existing file so new
# operations are added without clobbering admin-local overrides.
PYTHONPATH="${REPO_ROOT}" "${REPO_ROOT}/venv/bin/python3" - <<'PY'
import json
import os
from pathlib import Path
from services.broker.allowlist import Allowlist

path = Path("/etc/system-root-broker/broker-allowlist.json")
existing = {}
if path.exists():
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except (json.JSONDecodeError, OSError):
        existing = {}

# Merge DEFAULT entries over the existing ones. This keeps the shape
# canonical while preserving any custom keys not in DEFAULT.
data = {**dict(Allowlist.DEFAULT), **existing}
for key, value in Allowlist.DEFAULT.items():
    data[key] = value

data.setdefault("_meta", {"version": "1.1"})
if "_meta" in data and isinstance(data["_meta"], dict):
    data["_meta"]["version"] = "1.1"

path.parent.mkdir(parents=True, exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PY
chown "root:${BROKER_GROUP}" "${ETC_DIR}/broker-allowlist.json"
chmod 0640 "${ETC_DIR}/broker-allowlist.json"

# --- Generate token if missing ---
SECRETS_FILE="${ETC_DIR}/broker-secrets.env"
if [ ! -f "${SECRETS_FILE}" ] || ! grep -q '^ROOT_BROKER_TOKEN=' "${SECRETS_FILE}"; then
    TOKEN="$(head -c 32 /dev/urandom | xxd -p | tr -d '\n')"
    echo "ROOT_BROKER_TOKEN=${TOKEN}" > "${SECRETS_FILE}"
    chown "root:${BROKER_GROUP}" "${SECRETS_FILE}"
    chmod 0640 "${SECRETS_FILE}"
    echo "Generated ROOT_BROKER_TOKEN. Save it if you need to configure clients."
else
    chown "root:${BROKER_GROUP}" "${SECRETS_FILE}"
    chmod 0640 "${SECRETS_FILE}"
fi

# --- Install sandboxed wrappers ---
# sudo-rs does not allow wildcards in command-line arguments, so a
# fixed-path wrapper is the only portable way to give the broker narrow
# systemctl/journalctl access. Each wrapper validates the JSON request,
# applies Landlock/seccomp, and execs the narrow command.
_install_wrapper() {
    local src="$1"
    local dst="$2"
    cp "${src}" "${dst}"
    sed -i "s|@@REPO_ROOT@@|${REPO_ROOT}|g" "${dst}"
    chown root:root "${dst}"
    chmod 0755 "${dst}"
}

_install_wrapper "${REPO_ROOT}/scripts/hardening/hori_systemctl.py" /usr/local/sbin/hori-systemctl
_install_wrapper "${REPO_ROOT}/scripts/hardening/hori_journalctl.py" /usr/local/sbin/hori-journalctl
_install_wrapper "${REPO_ROOT}/scripts/hardening/hori_gpu.py" /usr/local/sbin/hori-gpu
_install_wrapper "${REPO_ROOT}/scripts/hardening/hori_docker.py" /usr/local/sbin/hori-docker
_install_wrapper "${REPO_ROOT}/scripts/hardening/hori_ufw.py" /usr/local/sbin/hori-ufw
_install_wrapper "${REPO_ROOT}/scripts/hardening/hori_fail2ban.py" /usr/local/sbin/hori-fail2ban

# --- Install sudoers rule for the broker itself ---
SUDOERS_FILE="/etc/sudoers.d/system-root-broker"
{
    echo "# system-root-broker: allow only the sandboxed wrappers."
    echo "# This is the *only* root path for the broker user."
    echo "root-broker ALL=(root) NOPASSWD: /usr/local/sbin/hori-systemctl"
    echo "root-broker ALL=(root) NOPASSWD: /usr/local/sbin/hori-journalctl"
    echo "root-broker ALL=(root) NOPASSWD: /usr/local/sbin/hori-gpu"
    echo "root-broker ALL=(root) NOPASSWD: /usr/local/sbin/hori-docker"
    echo "root-broker ALL=(root) NOPASSWD: /usr/local/sbin/hori-ufw"
    echo "root-broker ALL=(root) NOPASSWD: /usr/local/sbin/hori-fail2ban"
} > "${SUDOERS_FILE}"
chmod 0440 "${SUDOERS_FILE}"

# --- Install systemd unit ---
# Replace the placeholder with the real repo root before copying.
cp "${SERVICE_SRC}" /etc/systemd/system/system-root-broker.service
sed -i "s|@@REPO_ROOT@@|${REPO_ROOT}|g" /etc/systemd/system/system-root-broker.service
chown root:root /etc/systemd/system/system-root-broker.service
chmod 0644 /etc/systemd/system/system-root-broker.service

# --- Set audit log permissions ---
# Permission separation: root-broker can append but cannot read or truncate.
# Only root can read the audit log (mirrors tool_daemon audit log design).
touch "${LOG_DIR}/audit.jsonl"
chown "root:${BROKER_GROUP}" "${LOG_DIR}/audit.jsonl"
chmod 0620 "${LOG_DIR}/audit.jsonl"

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

# --- Grant aios_core access to the broker socket ---
# aios_core runs as the human client user. It must be in the root-broker
# group to read the broker token and connect to the Unix socket.
# NOTE: aios_core must be restarted after this drop-in is created.
AIOS_DROPIN="/etc/systemd/system/aios_core.service.d/20-root-broker.conf"
mkdir -p "$(dirname "${AIOS_DROPIN}")"
cat > "${AIOS_DROPIN}" <<'EOF'
[Service]
SupplementaryGroups=root-broker
EOF
chmod 0644 "${AIOS_DROPIN}"
systemctl daemon-reload
echo
echo "Created ${AIOS_DROPIN}."
echo "Run 'sudo systemctl restart aios_core' so the admin panel can use the broker."
