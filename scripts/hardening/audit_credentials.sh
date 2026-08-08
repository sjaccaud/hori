#!/usr/bin/env bash
# PoC 1.0.2: Credential Audit
#
# Scans the system for plaintext credentials in .env files, checks the n8n
# credential database, and identifies long-lived Home Assistant tokens. Does
# NOT print any secret values — only reports WHERE they are and WHAT TYPE.
#
# Traces to: docs/roadmap.md Tier 1A, PoC 1.0.2.
# Traces to: docs/system_security_audit.md "Audit and Rotate Credentials".
#
# Usage: ./scripts/hardening/audit_credentials.sh
#
# Exit codes:
#   0 — no plaintext credentials found
#   1 — plaintext credentials found (see report for remediation)
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo "=== Credential Audit (PoC 1.0.2) ==="
echo
echo "Scans for plaintext credentials WITHOUT printing secret values."
echo

AUDIT_FAILED=0

# --- 1. Scan for .env files with API keys ---
echo "--- .env files with credentials ---"

scan_env_file() {
    local file="$1"
    if [ ! -f "$file" ]; then
        return
    fi
    # Count lines that look like KEY=VALUE (non-comment, non-empty).
    local count
    count=$(grep -cE '^[A-Z_]+=.+' "$file" 2>/dev/null || echo 0)
    # Count lines that are NOT commented out and have a real value.
    local active
    active=$(grep -cE '^[A-Z_]+=.' "$file" 2>/dev/null || echo 0)
    if [ "$active" -gt 0 ]; then
        fail "$file: $active active credential(s), $count total entries"
        # List the KEY names (not values) so the user knows what's there.
        grep -oE '^[A-Z_]+' "$file" | grep -vE '^#' | sort -u | while read -r key; do
            echo "       - $key"
        done
        AUDIT_FAILED=1
    else
        pass "$file: no active credentials (all commented out)"
    fi
}

# Known .env locations from the security audit.
ENV_FILES=(
    "$HOME/Projects/aios/.env"
    "$HOME/.hermes/.env"
)
# Also scan for any .env files in the project tree.
while IFS= read -r f; do
    ENV_FILES+=("$f")
done < <(find "$HOME/Projects/aios" -name ".env" -type f 2>/dev/null || true)

for f in "${ENV_FILES[@]}"; do
    scan_env_file "$f"
done
echo

# --- 2. Check for .env files in git (should be gitignored) ---
echo "--- .env files tracked by git ---"
if git -C "$HOME/Projects/aios" ls-files --cached '*.env' '.env' 2>/dev/null | grep -q .; then
    fail ".env file(s) are tracked by git — secrets are in version control!"
    git -C "$HOME/Projects/aios" ls-files --cached '*.env' '.env' 2>/dev/null
    AUDIT_FAILED=1
else
    pass "No .env files tracked by git."
fi
echo

# --- 3. Check n8n credential database ---
echo "--- n8n credential database ---"
if docker exec n8n_aios test -f /home/node/.n8n/database.sqlite 2>/dev/null; then
    warn "n8n has a SQLite credential database at /home/node/.n8n/database.sqlite"
    echo "       This contains encrypted credentials for external services."
    echo "       To audit: docker exec n8n_aios sqlite3 /home/node/.n8n/database.sqlite \\"
    echo "         \"SELECT name, type FROM credential_entity;\""
    echo "       (Credentials are encrypted with N8N_ENCRYPTION_KEY — check the n8n"
    echo "        docker-compose for the key.)"
else
    pass "n8n credential database not found (n8n not running or no DB)."
fi
echo

# --- 4. Check for Home Assistant long-lived tokens ---
echo "--- Home Assistant long-lived tokens ---"
HA_CONFIG="/home/$SUDO_USER/homeassistant/.storage/auth"
if [ -f "$HA_CONFIG" ]; then
    TOKEN_COUNT=$(python3 -c "
import json
with open('$HA_CONFIG') as f:
    data = json.load(f)
tokens = data.get('data', {}).get('refresh_tokens', [])
long_lived = [t for t in tokens if t.get('client_id') is None]
print(len(long_lived))
" 2>/dev/null || echo "?")
    if [ "$TOKEN_COUNT" != "0" ] && [ "$TOKEN_COUNT" != "?" ]; then
        warn "Home Assistant has $TOKEN_COUNT long-lived access token(s)."
        echo "       Review at: Home Assistant -> Settings -> People -> [your user] -> Long-Lived Access Tokens"
        echo "       Revoke any tokens that are no longer needed."
    else
        pass "No Home Assistant long-lived tokens found."
    fi
else
    pass "Home Assistant auth storage not found (HA not on this machine)."
fi
echo

# --- 5. Check systemd unit files for credentials in Environment= lines ---
echo "--- Credentials in systemd unit files ---"
for unit in /etc/systemd/system/aios*.service /etc/systemd/system/llamacpp.service; do
    if [ -f "$unit" ]; then
        if grep -qE 'Environment=.*(_KEY|_TOKEN|_SECRET|_PASSWORD)=' "$unit" 2>/dev/null; then
            warn "$(basename $unit) has credential(s) in Environment= lines."
            echo "       These are readable by any user via 'systemctl show'."
            echo "       Consider using EnvironmentFile= with a root-owned file (mode 0600)."
            grep -oE 'Environment=[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=' "$unit" | sed 's/=.*/=<REDACTED>/' | while read -r line; do
                echo "       - $line"
            done
        else
            pass "$(basename $unit): no credentials in Environment= lines."
        fi
    fi
done
echo

# --- 6. Check for SSH/GPG key access ---
echo "--- SSH/GPG key access ---"
if [ -f "$HOME/.ssh/id_ed25519" ]; then
    warn "SSH private key exists at ~/.ssh/id_ed25519 (readable by $USER)."
    echo "       This is normal for the user, but the future tool service (aios-worker)"
    echo "       must NOT have access to it. Landlock (Tier 2A) will deny it."
else
    pass "No SSH private key found."
fi
if [ -d "$HOME/.gnupg" ] && [ "$(ls -A $HOME/.gnupg/ 2>/dev/null)" ]; then
    warn "GPG keyring exists at ~/.gnupg/ (currently public keys only per audit)."
else
    pass "No GPG keyring or empty keyring."
fi
echo

# --- Summary ---
echo "=== Summary ==="
if [ "$AUDIT_FAILED" -eq 1 ]; then
    fail "Plaintext credentials found in .env files."
    echo
    echo "Remediation (PoC 1.0.2):"
    echo "  1. Rotate the Gemini API key (in case it was exposed during testing)."
    echo "     - Go to https://aistudio.google.com/app/apikey"
    echo "     - Create a new key, update the systemd unit, delete the old key."
    echo "  2. Move API keys from .env files to systemd EnvironmentFile= with a"
    echo "     root-owned file (mode 0600):"
    echo "       sudo mkdir -p /etc/hori"
    echo "       sudo bash -c 'echo \"GEMINI_API_KEY=...\" > /etc/hori/secrets.env'"
    echo "       sudo chmod 600 /etc/hori/secrets.env"
    echo "       sudo chown root:$SUDO_USER /etc/hori/secrets.env"
    echo "     Then add to aios_core.service:"
    echo "       EnvironmentFile=/etc/hori/secrets.env"
    echo "  3. Audit n8n credentials (see command above)."
    echo "  4. Revoke unused Home Assistant long-lived tokens."
    exit 1
else
    pass "No plaintext credentials found in scanned locations."
    exit 0
fi
