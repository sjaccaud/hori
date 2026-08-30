#!/usr/bin/env bash
# Temporary end-to-end test for root-broker service_restart.
# Creates a dummy systemd unit, temporarily adds it to the broker allowlist
# and sudoers, runs status + restart via the broker, then cleans up.
#
# Usage: sudo bash scripts/hardening/test_broker_restart.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo)."
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DUMMY_SERVICE="test-broker-dummy"
DUMMY_UNIT="/etc/systemd/system/${DUMMY_SERVICE}.service"
ALLOWLIST="/etc/system-root-broker/broker-allowlist.json"
SUDOERS="/etc/sudoers.d/system-root-broker"
PYTHON="${REPO_ROOT}/venv/bin/python3"

# --- Cleanup on exit ---
cleanup() {
    echo
    echo "=== Cleaning up ==="
    set +e
    if systemctl is-active --quiet "${DUMMY_SERVICE}" 2>/dev/null; then
        systemctl stop "${DUMMY_SERVICE}"
    fi
    if [ -f "${DUMMY_UNIT}" ]; then
        rm -f "${DUMMY_UNIT}"
    fi
    if [ -f "${ALLOWLIST}.bak" ]; then
        cp "${ALLOWLIST}.bak" "${ALLOWLIST}"
        rm -f "${ALLOWLIST}.bak"
    fi
    if [ -f "${SUDOERS}.bak" ]; then
        cp "${SUDOERS}.bak" "${SUDOERS}"
        rm -f "${SUDOERS}.bak"
    fi
    systemctl daemon-reload
    if systemctl is-active --quiet system-root-broker; then
        systemctl restart system-root-broker
    fi
    echo "Cleanup done."
}
trap cleanup EXIT

echo "=== Creating dummy systemd unit: ${DUMMY_SERVICE} ==="
cat > "${DUMMY_UNIT}" <<EOF
[Unit]
Description=Temporary test unit for root broker

[Service]
Type=simple
ExecStart=/usr/bin/sleep 300
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${DUMMY_UNIT}"

echo "=== Starting ${DUMMY_SERVICE} ==="
systemctl daemon-reload
systemctl start "${DUMMY_SERVICE}"
for i in {1..10}; do
    if systemctl is-active --quiet "${DUMMY_SERVICE}"; then
        break
    fi
    sleep 0.5
done
systemctl is-active --quiet "${DUMMY_SERVICE}" || {
    echo "ERROR: dummy service did not start"
    exit 1
}

echo "=== Backing up and patching allowlist / sudoers ==="
cp -p "${ALLOWLIST}" "${ALLOWLIST}.bak" || {
    cp "${ALLOWLIST}" "${ALLOWLIST}.bak"
    chown "root:root-broker" "${ALLOWLIST}.bak"
    chmod 0640 "${ALLOWLIST}.bak"
}
cp -p "${SUDOERS}" "${SUDOERS}.bak" || {
    cp "${SUDOERS}" "${SUDOERS}.bak"
    chown root:root "${SUDOERS}.bak"
    chmod 0440 "${SUDOERS}.bak"
}

# Add dummy service to allowlist
PYTHONPATH="${REPO_ROOT}" "${PYTHON}" - <<PY
import json
with open("${ALLOWLIST}", "r") as f:
    data = json.load(f)
for op in ("service_status", "service_restart", "service_stop"):
    if op in data:
        data[op]["service"].append("${DUMMY_SERVICE}")
with open("${ALLOWLIST}", "w") as f:
    json.dump(data, f, indent=2)
PY

# Add narrow sudoers lines for the dummy service
cat >> "${SUDOERS}" <<EOF
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl is-active ${DUMMY_SERVICE}
root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl restart ${DUMMY_SERVICE}
EOF

if command -v visudo >/dev/null 2>&1; then
    visudo -c -f "${SUDOERS}" || {
        echo "ERROR: sudoers syntax check failed"
        exit 1
    }
else
    echo "(visudo not available; skipping syntax check)"
fi

echo "=== Reloading broker with new allowlist ==="
systemctl restart system-root-broker
sleep 1

echo "=== Running broker service_status test ==="
PYTHONPATH="${REPO_ROOT}" "${PYTHON}" - <<PY
from services.broker.client import RootBrokerClient
import json
c = RootBrokerClient()
print(json.dumps(c.call("service_status", {"service": "${DUMMY_SERVICE}"}), indent=2))
PY

echo "=== Running broker service_restart test ==="
PYTHONPATH="${REPO_ROOT}" "${PYTHON}" - <<PY
from services.broker.client import RootBrokerClient
import json, time
c = RootBrokerClient()
print("BEFORE:", json.dumps(c.call("service_status", {"service": "${DUMMY_SERVICE}"})))
print("RESTART:", json.dumps(c.call("service_restart", {"service": "${DUMMY_SERVICE}"}), indent=2))
time.sleep(2)
print("AFTER:", json.dumps(c.call("service_status", {"service": "${DUMMY_SERVICE}"})))
PY

echo
echo "=== Test complete; cleanup will run via trap ==="
