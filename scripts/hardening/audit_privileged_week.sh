#!/usr/bin/env bash
# Weekly privileged-operation audit for HORI.
#
# Collects the privileged commands the <user> account (and therefore Devin
# running as <user>) actually used in the last week.  This data is used to
# decide which broker operations are needed before removing the account from
# sudo and docker.
#
# Run as yourself for the read-only summary:
#   ./scripts/hardening/audit_privileged_week.sh
#
# Run with sudo to also include the broker's append-only audit log:
#   sudo ./scripts/hardening/audit_privileged_week.sh

set -euo pipefail

SINCE="${1:-7 days ago}"
REPORT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hori/weekly-audits"
REPORT="$REPORT_DIR/weekly-audit-$(date +%Y%m%d-%H%M%S).md"

mkdir -p "$REPORT_DIR"

NOW=$(date --rfc-3339=seconds)
WEEK_AGO=$(date -d "$SINCE" --rfc-3339=seconds)

{
  cat <<EOF
# HORI weekly privileged-operation audit

- Generated: $NOW
- Window: $WEEK_AGO → $NOW
- Run by: $(whoami)

## 1. Sudo commands

EOF

  # Extract COMMAND= lines from the sudo journal.  Some lines will be session
  # noise; we want the actual command the user ran as root.
  if journalctl --quiet _COMM=sudo --since "$SINCE" 2>/dev/null | grep -q .; then
    journalctl --no-pager _COMM=sudo --since "$SINCE" 2>/dev/null \
      | grep -oP 'COMMAND=\K.*' \
      | sort \
      | uniq -c \
      | sort -rn \
      | head -50 \
      | sed 's/^\s*/- /' || true
  else
    echo "_No sudo usage recorded in this window._"
  fi

  cat <<EOF

## 2. Docker events

EOF

  if command -v docker >/dev/null 2>&1; then
    docker events --since "$SINCE" --until now --format '{{json .}}' 2>/dev/null \
      | jq -r '[.Type, .Action] | join("/")' \
      | sort \
      | uniq -c \
      | sort -rn \
      | head -50 \
      | sed 's/^\s*/- /' || echo "_No docker events recorded._"
  else
    echo "_docker binary not found._"
  fi

  cat <<EOF

## 3. Shell history (bash)

Recent commands matching common privileged keywords:

EOF

  if [[ -f "$HOME/.bash_history" ]]; then
    grep -E '^\s*(sudo|docker|apt|apt-get|systemctl|rocm-smi|nvidia-smi|ufw|fail2ban-client|flatpak)\s' \
      "$HOME/.bash_history" 2>/dev/null | tail -50 | sed 's/^/- /' || true
  fi

  if [[ -f "$HOME/.zsh_history" ]]; then
    grep -E '\s*(sudo|docker|apt|apt-get|systemctl|rocm-smi|nvidia-smi|ufw|fail2ban-client|flatpak)\s' \
      "$HOME/.zsh_history" 2>/dev/null | tail -50 | sed 's/^/- /' || true
  fi

  cat <<EOF

## 4. Broker audit log

EOF

  AUDIT_LOG="/var/log/system-root-broker/audit.jsonl"
  if [[ -r "$AUDIT_LOG" ]]; then
    # Count broker operations and include the full set of distinct operation names.
    jq -r '.operation' "$AUDIT_LOG" 2>/dev/null \
      | sort \
      | uniq -c \
      | sort -rn \
      | sed 's/^\s*/- /' || true

    echo ""
    echo "Distinct operations seen:"
    jq -r '.operation' "$AUDIT_LOG" 2>/dev/null \
      | sort -u \
      | sed 's/^/- /' || true
  else
    echo "_Broker audit log not readable as $(whoami). Run with \`sudo $0\` to include it._"
    echo ""
    echo "(This is expected: the log is append-only and readable only by root to protect evidence.)"
  fi

  cat <<EOF

## 5. Summary / candidate broker operations

Look at the patterns above.  Common clusters map to future broker operations:

| Pattern | Candidate broker op |
|---------|---------------------|
| `apt install/remove/purge` | `package_install` / `package_remove` with `confirmed_by` |
| `systemctl restart/stop/status` | already covered |
| `docker run/exec/rm/build` | `docker_*` mutation ops or separate Docker admin account |
| `rocm-smi --set*` / `nvidia-smi -ac` | `gpu_set_*` with bounded values and `confirmed_by` |
| `ufw allow/delete` | `ufw_allow` / `ufw_deny` with `confirmed_by` |
| `fail2ban-client set ... ban` | `fail2ban_ban` with `confirmed_by` |

The safest next hardening step is to build the most common missing operation
before removing the primary account from `sudo` or `docker`.

---
Report written to: $REPORT
EOF
} > "$REPORT"

echo "Report written to: $REPORT"
