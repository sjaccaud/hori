#!/usr/bin/env bash
# AI Root Surface Audit
#
# Scans the system for paths where AI agents (Devin, Open-WebUI, aider,
# HORI/AIOS, IDE agents) can currently escalate to root or modify system
# state. This is a READ-ONLY audit. It does NOT change permissions, write
# files, install/uninstall packages, or restart services.
#
# Traces to:
#   - docs/safety.md (HORI safety spine)
#   - docs/roadmap.md Tier 4 (shell execution sandbox, confirmation gate,
#     destructive-action escalation, out-of-band confirmation)
#
# Usage: ./scripts/hardening/audit_ai_root_surface.sh
#
# Exit codes:
#   0 - audit completed (report may still contain CRITICAL findings)
#   1 - the audit could not complete (missing tools, not a finding)
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

pass()    { echo -e "${GREEN}[PASS]${NC} $1"; }
fail()    { echo -e "${RED}[FAIL]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
info()    { echo -e "${BLUE}[INFO]${NC} $1"; }

# Counters for the summary
CRITICAL_COUNT=0
HIGH_COUNT=0
MEDIUM_COUNT=0
LOW_COUNT=0

critical() {
    echo -e "${RED}${BOLD}[CRITICAL]${NC} $1"
    CRITICAL_COUNT=$((CRITICAL_COUNT + 1))
}

report() {
    local level="$1"; shift
    case "$level" in
        critical) critical "$*" ;;
        high)     echo -e "${RED}[HIGH]${NC} $*"; HIGH_COUNT=$((HIGH_COUNT + 1)) ;;
        medium)   echo -e "${YELLOW}[MEDIUM]${NC} $*"; MEDIUM_COUNT=$((MEDIUM_COUNT + 1)) ;;
        low)      echo -e "${GREEN}[LOW]${NC} $*"; LOW_COUNT=$((LOW_COUNT + 1)) ;;
    esac
}

echo "=== AI Root Surface Audit ==="
echo
echo "This script is READ-ONLY. It inventories where AI agents can touch"
echo "root or system state. It does not change anything."
echo

# ---------------------------------------------------------------------------
# 1. Devin CLI permissions
# ---------------------------------------------------------------------------
echo "--- 1. Devin CLI permissions ---"

DEVIN_USER_CONFIG="$HOME/.config/devin/config.json"
DEVIN_PROJECT_CONFIGS=()
while IFS= read -r f; do
    DEVIN_PROJECT_CONFIGS+=("$f")
done < <(find "$HOME" -maxdepth 4 -type f \( -name 'config.json' -o -name 'config.local.json' \) -path '*/.devin/*' 2>/dev/null || true)

audit_devin_config() {
    local file="$1"
    if [ ! -f "$file" ]; then
        return
    fi

    echo "  File: $file"

    local tmp
    tmp=$(mktemp)
    trap 'rm -f "$tmp"' RETURN

    python3 - "$file" > "$tmp" <<'PY'
import json, sys, re, shlex

path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)

perms = cfg.get('permissions', {})
allows = perms.get('allow', [])
denies = perms.get('deny', [])
asks = perms.get('ask', [])

# Rules that are root-escalation or full-system access no matter the rest.
critical_patterns = [
    re.compile(r'^Exec\(\s*(?:sudo|su|pkexec|doas)\b', re.I),
    re.compile(r'^Exec\([^)]*(?<!\.)\b(?:bash|sh|zsh|fish|csh|ksh|tcsh)\b', re.I),
    re.compile(r'^Fetch\(\s*(?:domain:\*|\*\*|\*)\s*\)', re.I),
    re.compile(r'^Write\(\s*/(?:root|boot|etc/sudoers|bin|sbin|lib|lib64|usr/bin|usr/sbin|usr/local/bin|usr/local/sbin)\b', re.I),
]

# Dangerous package/destuctive/arbitrary commands.  Be specific about subcommands.
high_patterns = [
    # package installs/removes/upgrades
    re.compile(r'^Exec\([^)]*\b(?:apt(?:-get)?)\s+(?:install|remove|purge|autoremove|full-upgrade|dist-upgrade|upgrade|reinstall|download)\b', re.I),
    re.compile(r'^Exec\([^)]*\bpip(?:3)?\s+(?:install|uninstall|cache\s+(?:purge|remove))\b', re.I),
    re.compile(r'^Exec\([^)]*\bflatpak\s+(?:install|remove|uninstall|update|override)\b', re.I),
    re.compile(r'^Exec\([^)]*\bsnap\s+(?:install|refresh|remove|revert|enable|disable)\b', re.I),
    re.compile(r'^Exec\([^)]*\bdocker\s+(?:run|pull|load|push|build|exec|cp)\b', re.I),
    re.compile(r'^Exec\([^)]*\bnpm\s+(?:(?:-g\s+|\S+\s+)?install|uninstall|publish)\b', re.I),
    re.compile(r'^Exec\([^)]*\byarn\s+(?:global\s+add|add|remove)\b', re.I),
    re.compile(r'^Exec\([^)]*\b(?:cargo|go)\s+install\b', re.I),
    re.compile(r'^Exec\([^)]*\bbrew\s+install\b', re.I),
    re.compile(r'^Exec\([^)]*\b(?:yay|paru|pacman)\s+(?:-S|-R|-Syu)\b', re.I),
    re.compile(r'^Exec\([^)]*\b(?:dnf|yum|zypper|urpmi|eopkg|xbps-install)\s+install\b', re.I),
    # destructive file ops / arbitrary stream processors
    re.compile(r'^Exec\([^)]*\b(?:rm|rmdir|mv|cp|chmod|chown|install|dd|mkfs|fdisk|parted|wipefs|cryptsetup)\b', re.I),
    re.compile(r'^Exec\([^)]*\b(?:sed|awk|tee|xargs|find)\b', re.I),
    # arbitrary code / network / remote
    re.compile(r'^Exec\([^)]*\b(?:python3?|python|node|npx|perl|ruby|php)\b', re.I),
    re.compile(r'^Exec\([^)]*\b(?:curl|wget|ftp|scp|sftp|rsync|ssh|openssl)\b', re.I),
    # firewall / network control
    re.compile(r'^Exec\([^)]*\b(?:ufw\s+(?:allow|deny|delete|reset|enable|disable)|iptables|firewalld|firewall-cmd)\b', re.I),
    # system control
    re.compile(r'^Exec\([^)]*\bsystemctl\s+(?:start|stop|restart|reload|enable|disable|mask|unmask|daemon-reload|edit|set-environment|unset-environment|suspend|hibernate)\b', re.I),
    re.compile(r'^Exec\([^)]*\bjournalctl\s+(?:--system|-k)\b', re.I),
    # writes to system directories (including /etc, /opt, /usr, /var, /srv)
    re.compile(r'^Write\(\s*/(?:etc|opt|usr|var|srv|lib|lib64|bin|sbin)(?:/|\))', re.I),
    re.compile(r'^Write\(\s*/etc/systemd/system\b', re.I),
    # bare package/system tools with no subcommand -> can be used for anything
    re.compile(r'^Exec\(\s*(?:apt(?:-get)?|systemctl|docker|flatpak|snap|pip(?:3)?|npm|yarn)\s*\)', re.I),
]

# Read-only or low-impact commands and queries.
medium_patterns = [
    re.compile(r'^Exec\([^)]*\b(?:apt(?:-get)?|apt-cache|dpkg)\s+(?:list|search|show|policy|cache|update|status)\b', re.I),
    re.compile(r'^Exec\([^)]*\b(?:flatpak|snap)\s+(?:list|search|info|remotes|history|ps|run)\b', re.I),
    re.compile(r'^Exec\([^)]*\bdocker\s+(?:ps|info|images|logs|volume|stop|compose)\b', re.I),
    re.compile(r'^Exec\([^)]*\bsystemctl\s+(?:is-active|is-enabled|is-failed|list-units|list-unit-files|status|cat|show)\b', re.I),
    re.compile(r'^Exec\([^)]*\bjournalctl\b', re.I),
    re.compile(r'^Exec\([^)]*\b(?:mkdir|find|grep|tail|head|cat|ls|wc|file|strings|xxd|mount|umount|lsof|ss|pgrep|sleep|whoami|id|date|uptime|free|df|du|lscpu|lspci|lsusb|lsblk|inxi|dmesg|amdgpu-top|amdgpu_top|vulkaninfo|glxinfo|glxgears|gamescope|wlr-randr|xrandr|gsettings|powerprofilesctl|swapon|blkid|readelf|objdump|lshw|lsscsi|dmidecode|lsmem|nvme|ethtool|sensors|cpupower|rocminfo|modinfo|dkms|loginctl|systemd-tmpfiles|true|read|continue|break|command)\b', re.I),
    re.compile(r'^Exec\([^)]*\b(?:git|make|go test|ruff check)\b', re.I),
    re.compile(r'^Exec\([^)]*\btailscale\s+\b', re.I),
    re.compile(r'^Exec\([^)]*\b(?:ollama|rocm-smi|hipcc|amdgpu-top|amdgpu_top|llama-server|llama-model-info|huggingface-cli|hf|pro status|pro security-status)\b', re.I),
    re.compile(r'^Write\(\s*/tmp\b', re.I),
    re.compile(r'^Write\(', re.I),
    re.compile(r'^Fetch\(', re.I),
]

# Shell interpreter as the actual command (not a .sh filename extension).
shell_pattern = re.compile(r'Exec\([^)]*(?<!\.)\b(?:bash|sh|zsh|fish|csh|ksh|tcsh)\b', re.IGNORECASE)

def first_binary(rule):
    """Extract the first real command token (skip KEY=VALUE env assignments)."""
    inner = rule[5:-1].strip()  # strip 'Exec(' and ')'
    if not inner:
        return None, []
    try:
        parts = shlex.split(inner)
    except ValueError:
        # Fallback for unbalanced quotes
        parts = inner.split()
    # Skip leading KEY=VALUE assignments
    i = 0
    while i < len(parts) and '=' in parts[i] and not parts[i].startswith('-'):
        i += 1
    if i < len(parts):
        return parts[i], parts[i:]
    return None, parts

def classify(rule):
    rule = rule.strip()
    if not rule:
        return None
    # Privilege escalation / full-system access
    for p in critical_patterns:
        if p.search(rule):
            return 'critical'
    # Plain shell invocation or env-wrapped shell
    if rule.startswith('Exec(') and shell_pattern.search(rule):
        return 'critical'
    # Dangerous package/ destructive / arbitrary code / system control
    for p in high_patterns:
        if p.search(rule):
            return 'high'
    # Known read-only / low-impact commands, Fetch/Write/Read generics
    for p in medium_patterns:
        if p.search(rule):
            return 'medium'
    # Any other Exec(...) is at least medium (unknown command)
    if rule.startswith('Exec('):
        return 'medium'
    return 'low'

for rule in allows:
    level = classify(rule)
    if level and level != 'low':
        print(f"{level}|allow|{rule}")

for rule in denies:
    rule = rule.strip()
    if rule:
        print(f"low|deny|{rule}")

for rule in asks:
    rule = rule.strip()
    if rule:
        print(f"low|ask|{rule}")
PY

    while IFS='|' read -r level ruletype rule; do
        [ -n "$level" ] || continue
        case "$level" in
            critical|high|medium|low)
                report "$level" "[$file] Devin $ruletype: $rule"
                ;;
        esac
    done < "$tmp"
}

if [ -f "$DEVIN_USER_CONFIG" ]; then
    audit_devin_config "$DEVIN_USER_CONFIG"
else
    warn "Devin user config not found at $DEVIN_USER_CONFIG"
fi

for cfg in "${DEVIN_PROJECT_CONFIGS[@]}"; do
    audit_devin_config "$cfg"
done

echo

# ---------------------------------------------------------------------------
# 2. Sudoers and privilege status
# ---------------------------------------------------------------------------
echo "--- 2. Sudoers and user privileges ---"

echo "  Groups: $(groups)"

# Check membership in high-privilege groups
for grp in sudo wheel docker adm root; do
    if id -nG "$USER" 2>/dev/null | grep -qw "$grp"; then
        if [ "$grp" = "sudo" ] || [ "$grp" = "wheel" ]; then
            report high "User is in group: $grp (can elevate to root)"
        elif [ "$grp" = "docker" ]; then
            report high "User is in group: $grp (Docker group == root via privileged containers)"
        else
            report medium "User is in group: $grp"
        fi
    fi
done

# Try to list sudo privileges without a password (non-destructive)
if command -v sudo &>/dev/null; then
    SUDO_LIST=$(sudo -n -l 2>/dev/null || true)
    if [ -n "$SUDO_LIST" ]; then
        echo "  Sudo privileges (from 'sudo -n -l'):"
        echo "$SUDO_LIST" | sed 's/^/    /'
        if echo "$SUDO_LIST" | grep -qE '(NOPASSWD|ALL)|(/bin/bash|/usr/bin/bash|/bin/sh|/usr/bin/sh)'; then
            report critical "Sudo configuration allows root shell or ALL commands"
        fi
    else
        info "Could not list sudo privileges (may require a password)."
    fi
fi

# /etc/sudoers.d files (may not be readable without root)
if [ -d /etc/sudoers.d ]; then
    SUDOERS_FILES=$(find /etc/sudoers.d -maxdepth 1 -type f 2>/dev/null || true)
    if [ -n "$SUDOERS_FILES" ]; then
        echo "  Sudoers drop-in files:"
        echo "$SUDOERS_FILES" | sed 's/^/    /'
        for f in $SUDOERS_FILES; do
            if [ -r "$f" ]; then
                echo "    --- $f ---"
                grep -vE '^#|^$' "$f" | sed 's/^/        /' || true
            else
                warn "  $f exists but is not readable (requires root). Run with sudo -n for full output."
            fi
        done
    else
        pass "No /etc/sudoers.d drop-in files found."
    fi
fi

# pkexec / polkit actions
echo
echo "--- 3. pkexec / polkit rules ---"
if [ -d /usr/share/polkit-1/actions ] || [ -d /etc/polkit-1/rules.d ]; then
    info "Polkit rules exist. Review them manually if agents use pkexec."
    if [ -d /etc/polkit-1/rules.d ]; then
        ls -la /etc/polkit-1/rules.d 2>/dev/null | sed 's/^/    /' || true
    fi
else
    pass "No polkit rules directory found."
fi

echo

# ---------------------------------------------------------------------------
# 4. AI/HORI services running as root or with privileges
# ---------------------------------------------------------------------------
echo "--- 4. AI/HORI systemd services ---"

AI_SERVICES=(aios_core aios-sherpa aios-tool-daemon llamacpp open-webui n8n qdrant)
for svc in "${AI_SERVICES[@]}"; do
    # Avoid pipelines with `grep -q` under `set -o pipefail` (SIGPIPE issues).
    UNIT_FILE=$(systemctl show -p FragmentPath --value "$svc.service" 2>/dev/null || true)
    if [ -n "$UNIT_FILE" ] && [ -f "$UNIT_FILE" ]; then
        OWNER=$(stat -c '%U' "$UNIT_FILE" 2>/dev/null || true)
        if [ "$OWNER" = "root" ]; then
            echo "  $svc.service: root-owned at $UNIT_FILE"
        else
            report medium "$svc.service: owned by $OWNER (not root) at $UNIT_FILE"
        fi
    fi
done

# ---------------------------------------------------------------------------
# 5. Open-WebUI, aider, and other agents
# ---------------------------------------------------------------------------
echo
echo "--- 5. Other agent configurations ---"

OWUI_COMPOSE="$HOME/Projects/aios/services/open-webui/docker-compose.yml"
if [ -f "$OWUI_COMPOSE" ]; then
    info "Open-WebUI docker-compose found: $OWUI_COMPOSE"
    if grep -qE 'ports:' "$OWUI_COMPOSE"; then
        grep -A2 'ports:' "$OWUI_COMPOSE" | sed 's/^/    /'
    fi
fi

AIDER_CONF="$HOME/.aider.conf.yml"
if [ -f "$AIDER_CONF" ]; then
    info "aider config found: $AIDER_CONF"
    # aider's /run command is interactive and asks by default, but can be scripted.
    warn "aider can run shell commands via /run. Review aider history for privileged usage."
fi

# Devin server / desktop if present
if [ -d "$HOME/.devin-server" ]; then
    info "Devin server data found at $HOME/.devin-server"
    info "  Check User/History for agent-generated scripts that may use sudo."
fi

echo

# ---------------------------------------------------------------------------
# 6. Docker, Flatpak, Snap
# ---------------------------------------------------------------------------
echo "--- 6. Container / package manager surfaces ---"

if command -v docker &>/dev/null; then
    if docker info &>/dev/null 2>&1; then
        RUNNING_ROOT=$(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null | head -n 20 || true)
        if [ -n "$RUNNING_ROOT" ]; then
            echo "  Running Docker containers:"
            echo "$RUNNING_ROOT" | sed 's/^/    /'
        fi
    else
        warn "Docker client present but not usable by this user."
    fi
fi

if command -v flatpak &>/dev/null; then
    FLATPAK_LIST=$(flatpak list --app --columns=application,version 2>/dev/null | head -n 20 || true)
    if [ -n "$FLATPAK_LIST" ]; then
        echo "  Installed Flatpak apps:"
        echo "$FLATPAK_LIST" | sed 's/^/    /'
    fi
fi

if command -v snap &>/dev/null; then
    SNAP_LIST=$(snap list 2>/dev/null | tail -n +2 | head -n 20 || true)
    if [ -n "$SNAP_LIST" ]; then
        echo "  Installed snaps:"
        echo "$SNAP_LIST" | sed 's/^/    /'
    fi
fi

echo

# ---------------------------------------------------------------------------
# 7. Custom scripts that call sudo
# ---------------------------------------------------------------------------
echo "--- 7. Custom scripts with sudo / pkexec ---"

SCRIPT_DIRS=(
    "$HOME/.local/bin"
    "$HOME/scripts"
    "$HOME/Projects/aios/scripts"
    "$HOME/Projects/aios/scripts/hardening"
    "$HOME/Projects/krill_harvester/scripts"
    "/opt/scripts"
)

for dir in "${SCRIPT_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        MATCHES=$(grep -RIlnE '\b(sudo|pkexec|doas)\b' --exclude='audit_ai_root_surface.sh' "$dir" 2>/dev/null | head -n 20 || true)
        if [ -n "$MATCHES" ]; then
            echo "  $dir:"
            echo "$MATCHES" | sed 's/^/    /'
        fi
    fi
done

# Agent-generated history (mostly .md conversation logs)
if [ -d "$HOME/.devin-server/data/User/History" ]; then
    HIST_MATCHES=$(grep -RIlnE '\b(sudo|pkexec|doas)\b' --include='*.sh' --include='*.bash' --include='*.py' --include='*.md' "$HOME/.devin-server/data/User/History" 2>/dev/null | head -n 20 || true)
    if [ -n "$HIST_MATCHES" ]; then
        warn "Devin history contains references to sudo/pkexec (check .md for embedded commands):"
        echo "$HIST_MATCHES" | sed 's/^/    /'
    fi
fi

echo

# ---------------------------------------------------------------------------
# 8. Shell profile / rc file aliases and functions
# ---------------------------------------------------------------------------
echo "--- 8. Shell profile aliases/functions ---"

for f in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" "$HOME/.zshrc" "$HOME/.zprofile"; do
    if [ -f "$f" ]; then
        ALIASES=$(grep -nE '\b(sudo|pkexec|doas)\b' "$f" 2>/dev/null | head -n 20 || true)
        if [ -n "$ALIASES" ]; then
            echo "  $f:"
            echo "$ALIASES" | sed 's/^/    /'
        fi
    fi
done

echo

# ---------------------------------------------------------------------------
# 9. Crontab / timers that may invoke AI scripts
# ---------------------------------------------------------------------------
echo "--- 9. Scheduled tasks ---"

USER_CRON=$(crontab -l 2>/dev/null || true)
if [ -n "$USER_CRON" ]; then
    echo "  User crontab:"
    echo "$USER_CRON" | sed 's/^/    /'
fi

if command -v systemctl &>/dev/null; then
    ACTIVE_TIMERS=$(systemctl list-timers --all --no-pager --no-legend 2>/dev/null | grep -iE 'aios|hori|devin|open-webui|n8n' | head -n 20 || true)
    if [ -n "$ACTIVE_TIMERS" ]; then
        echo "  Active AI-related systemd timers:"
        echo "$ACTIVE_TIMERS" | sed 's/^/    /'
    fi
fi

echo

# ---------------------------------------------------------------------------
# 10. Network exposure from AI services
# ---------------------------------------------------------------------------
echo "--- 10. Network bindings of AI services ---"

if command -v ss &>/dev/null; then
    AI_PORTS=(8080 8081 8082 5678 6333 6334 3000 3001 9090 9100 5680 8123)
    for port in "${AI_PORTS[@]}"; do
        LISTENING=$(ss -tlnH 2>/dev/null | awk -v p=":$port" '$4 ~ p {print $4}' | head -n 1 || true)
        if [ -n "$LISTENING" ]; then
            echo "  $LISTENING (AI-related)"
            if echo "$LISTENING" | grep -q '^0\.0\.0\.0'; then
                report medium "AI service listening on 0.0.0.0: $port"
            fi
        fi
    done
else
    warn "ss not found; cannot check network bindings."
fi

echo

# ---------------------------------------------------------------------------
# 11. Summary
# ---------------------------------------------------------------------------
echo "=== Summary ==="
echo
echo "  CRITICAL findings: $CRITICAL_COUNT"
echo "  HIGH findings:     $HIGH_COUNT"
echo "  MEDIUM findings:   $MEDIUM_COUNT"
echo "  LOW findings:      $LOW_COUNT"
echo

echo "Recommended next steps:"
echo "  1. Tighten the Devin CLI user config (~/.config/devin/config.json)."
echo "     Remove blanket Exec(sudo), Exec(bash), Write(/etc), Fetch(domain:*)."
echo "  2. Replace remaining required root actions with scoped wrappers or a root broker."
echo "  3. Review /etc/sudoers.d files and remove any NOPASSWD entries that grant shells"
echo "     or package managers."
echo "  4. If this user is in the 'docker' group, treat that as root access and consider"
echo "     a dedicated Docker user or rootless Docker."
echo
echo "To run a deeper audit with full /etc/sudoers.d content, re-run as:"
echo "  sudo -n $0"
echo
