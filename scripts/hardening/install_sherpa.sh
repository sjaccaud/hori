#!/bin/bash
# PoC 15.50: Install the AIOS Sherpa behavioral guardian.
#
# This script requires sudo. It:
#   1. Creates the `sherpa` system user (if not exists)
#   2. Compiles the Go binary (if Go is available) or installs the prebuilt one
#   3. Installs the binary to /usr/local/bin/hori-sherpa
#   4. Creates /run/sherpa with correct permissions
#   5. Installs the systemd service file
#   6. Enables and starts the service
#
# Traces to: docs/roadmap.md Tier 2E, PoC 15.50.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SHERPA_SRC="$PROJECT_DIR/services/sherpa"
SERVICE_NAME="hori-sherpa"
SERVICE_SRC="$SHERPA_SRC/hori-sherpa.service"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}.service"
BINARY_DST="/usr/local/bin/hori-sherpa"

echo "=== HORI Sherpa Installer ==="
echo ""

# --- Check root ---
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)."
    exit 1
fi

# --- Create sherpa user ---
echo "1. Checking sherpa user..."
if ! id sherpa &>/dev/null; then
    echo "   Creating sherpa system user..."
    useradd --system --no-create-home --shell /usr/sbin/nologin sherpa
    echo "   Created sherpa user (uid=$(id -u sherpa))"
else
    echo "   sherpa user exists (uid=$(id -u sherpa))"
fi
echo ""

# --- Compile or install binary ---
echo "2. Installing Sherpa binary..."

# Check if a prebuilt binary exists
PREBUILT="$SHERPA_SRC/sherpa"
if [ -f "$PREBUILT" ] && [ -x "$PREBUILT" ]; then
    echo "   Using prebuilt binary from $PREBUILT"
    cp "$PREBUILT" "$BINARY_DST"
else
    # Try to compile with Go
    GO_BIN=""
    for candidate in /usr/local/go/bin/go  /snap/go/current/bin/go; do
        if [ -x "$candidate" ]; then
            GO_BIN="$candidate"
            break
        fi
    done

    if [ -z "$GO_BIN" ]; then
        echo "   ERROR: No prebuilt binary and Go not found."
        echo "   Install Go or run: cd $SHERPA_SRC && go build -o sherpa ."
        exit 1
    fi

    echo "   Compiling with $GO_BIN..."
    cd "$SHERPA_SRC"
    "$GO_BIN" build -o "$BINARY_DST" .
    echo "   Compiled and installed to $BINARY_DST"
fi

chown root:root "$BINARY_DST"
chmod 0755 "$BINARY_DST"
echo "   Binary: $BINARY_DST ($(ls -lh $BINARY_DST | awk '{print $5}'))"
echo ""

# --- Verify it's a static binary ---
echo "3. Verifying binary..."
if ldd "$BINARY_DST" 2>&1 | grep -q "not a dynamic executable"; then
    echo "   OK: statically linked (no shared library dependencies)"
else
    echo "   WARNING: binary is not statically linked"
fi
file "$BINARY_DST" | grep -q "statically linked" && echo "   Confirmed: static ELF" || true
echo ""

# --- Create /run/sherpa ---
echo "4. Creating /run/sherpa..."
mkdir -p /run/sherpa
chown root:root /run/sherpa
chmod 0755 /run/sherpa
echo "   Created /run/sherpa (root:root 0755 — world-readable so aios-worker can read the cap file)"
echo ""

# --- Verify audit log is readable ---
echo "5. Checking audit log..."
if [ -f /var/log/hori/tool_audit.jsonl ]; then
    PERMS=$(stat -c "%a %U:%G" /var/log/hori/tool_audit.jsonl)
    echo "   Audit log: /var/log/hori/tool_audit.jsonl ($PERMS)"
    if [ -r /var/log/hori/tool_audit.jsonl ]; then
        echo "   OK: root can read the audit log"
    else
        echo "   WARNING: root cannot read the audit log"
    fi
else
    echo "   WARNING: audit log not found at /var/log/hori/tool_audit.jsonl"
    echo "   The Sherpa will start but won't detect any patterns until the tool daemon writes to it."
fi
echo ""

# --- Install systemd service ---
echo "6. Installing systemd service..."
if [ ! -f "$SERVICE_SRC" ]; then
    echo "   ERROR: Service file not found at $SERVICE_SRC"
    exit 1
fi

cp "$SERVICE_SRC" "$SERVICE_DST"
chown root:root "$SERVICE_DST"
chmod 0644 "$SERVICE_DST"
echo "   Installed: $SERVICE_DST"
echo ""

# --- Reload and start ---
echo "7. Reloading systemd and starting service..."
systemctl daemon-reload
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
    echo "PASS: Sherpa service is running"
else
    echo "FAIL: Sherpa service is not running"
    echo "Check logs with: journalctl -u $SERVICE_NAME -e"
    exit 1
fi

# Check that the capability file exists and shows Level 0
if [ -f /run/sherpa/capability_level ]; then
    CAP_CONTENT=$(cat /run/sherpa/capability_level)
    echo "PASS: Capability file exists: $CAP_CONTENT"
    if echo "$CAP_CONTENT" | grep -q '"level":0'; then
        echo "PASS: Level is 0 (normal) — Sherpa is alive and writing"
    else
        echo "WARNING: Level is not 0 — check Sherpa logs"
    fi
else
    echo "FAIL: Capability file not found at /run/sherpa/capability_level"
    exit 1
fi

echo ""
echo "=== Installation Complete ==="
echo "The Sherpa is running and monitoring the tool daemon's audit log."
echo ""
echo "To check status:  systemctl status $SERVICE_NAME"
echo "To view logs:     journalctl -u $SERVICE_NAME -f"
echo "To stop:          systemctl stop $SERVICE_NAME"
echo ""
echo "IMPORTANT: If the Sherpa stops, the tool daemon will drop to Level 4"
echo "(full stop) within 10 seconds. This is the fail-closed design."
