#!/bin/bash
# Apply systemd hardening changes that require root.
# Usage:
#   sudo bash apply-systemd-hardening.sh
# or, to also restart the main llama server:
#   sudo bash apply-systemd-hardening.sh --restart-main

set -euo pipefail

BUILD_DIR="${BUILD_DIR:-$HOME/llama-builds/mainline-adaptive-mtp-10}"
ETC_SYSTEMD=/etc/systemd/system

RESTART_MAIN=0
if [[ "${1:-}" == "--restart-main" ]]; then
    RESTART_MAIN=1
fi

# Tighten ReadWritePaths on the llama services.
cp -v "${BUILD_DIR}/llamacpp-adaptive.service" "${ETC_SYSTEMD}/llamacpp-adaptive.service"
cp -v "${BUILD_DIR}/llama-nano-adaptive.service" "${ETC_SYSTEMD}/llama-nano-adaptive.service"

# Remove the masked old ROCm 7.14 unit that is now dead weight.
if [[ -L "${ETC_SYSTEMD}/llamacpp.service" && "$(readlink "${ETC_SYSTEMD}/llamacpp.service")" == "/dev/null" ]]; then
    rm -v "${ETC_SYSTEMD}/llamacpp.service"
fi

systemctl daemon-reload

# Restart the nano sidecar first as the low-risk test.
if systemctl is-active --quiet llama-nano-adaptive; then
    systemctl restart llama-nano-adaptive
    sleep 2
    systemctl is-active llama-nano-adaptive
fi

if [[ ${RESTART_MAIN} -eq 1 ]]; then
    systemctl restart llamacpp-adaptive
    sleep 2
    systemctl is-active llamacpp-adaptive
else
    echo
    echo "llamacpp-adaptive.service was updated but not restarted."
    echo "Run 'sudo systemctl restart llamacpp-adaptive' when you are ready"
    echo "(this will unload the resident model and abort any in-flight chats)."
fi
