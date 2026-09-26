#!/bin/bash
# notify_health.sh — run post_reboot_health.sh and push the result to
# Telegram (+ ntfy if configured) and the GNOME desktop notification
# daemon. ALWAYS notifies, pass or fail.
#
# Runs as root from hori-boot-health.service at boot; can also be run
# manually: sudo scripts/hardening/notify_health.sh
#
# Config: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / NTFY_TOPIC / NTFY_SERVER
#   read from ${HORI_NOTIFY_CONF:-/etc/hori/notify.env}, falling back to
#   idea_factory/config/notify.conf (the same creds the digest uses).
# Desktop target user: ${HORI_DESKTOP_USER:-$SUDO_USER} (falls back to invoking user).
#
# --selftest sends a canned notification through all channels without
# running the health check (usable unprivileged — proves the channels
# work before installing the unit).
#
# Exit code = the health check's exit code, so on spine failure the
# systemd unit lands in `systemctl --failed` as a persistent flag.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEALTH_CHECK="${SCRIPT_DIR}/post_reboot_health.sh"
LOG_FILE="/var/log/hori/boot_health.log"
DESKTOP_USER="${HORI_DESKTOP_USER:-${SUDO_USER:-$(id -un)}}"

# --- config ---
CONF="${HORI_NOTIFY_CONF:-/etc/hori/notify.env}"
if [ ! -r "$CONF" ]; then
    DESKTOP_HOME="$(getent passwd "$DESKTOP_USER" 2>/dev/null | cut -d: -f6)"
    CONF="${DESKTOP_HOME:+${DESKTOP_HOME}/Projects/idea_factory/config/notify.conf}"
fi
if [ -r "$CONF" ]; then
    set -a; . "$CONF"; set +a
fi

# --- channels ---
tg_send() {
    [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] || return 1
    curl -sf --max-time 15 \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "disable_web_page_preview=true" \
        --data-urlencode "text=$1" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" >/dev/null
}

ntfy_send() {
    [ -n "${NTFY_TOPIC:-}" ] || return 1
    curl -sf --max-time 15 \
        -H "Title: HORI boot health" -H "Priority: ${2:-3}" \
        --data-binary "$1" \
        "${NTFY_SERVER:-https://ntfy.sh}/${NTFY_TOPIC}" >/dev/null
}

desktop_send() {
    # notify-send needs the user's session bus. At boot the session may
    # not exist yet — best-effort, never fatal.
    local uid
    uid=$(id -u "$DESKTOP_USER" 2>/dev/null) || return 0
    local bus="unix:path=/run/user/${uid}/bus"
    local urgency="normal"
    [ "${2:-}" = "fail" ] && urgency="critical"
    if [ "$(id -u)" -eq "$uid" ]; then
        env DBUS_SESSION_BUS_ADDRESS="$bus" notify-send -u "$urgency" "HORI boot health" "$1" 2>/dev/null || true
    else
        runuser -u "$DESKTOP_USER" -- env DBUS_SESSION_BUS_ADDRESS="$bus" \
            notify-send -u "$urgency" "HORI boot health" "$1" 2>/dev/null || true
    fi
}

notify_all() {
    # $1 = message, $2 = pass|fail
    local sent=""
    tg_send "$1" && sent="${sent}telegram " || sent="${sent}telegram-FAILED "
    ntfy_send "$1" "$([ "${2:-}" = fail ] && echo 5 || echo 3)" && sent="${sent}ntfy" || true
    desktop_send "$1" "${2:-}"
    echo "notify_health: channels: ${sent}" >&2
}

if [ "${1:-}" = "--selftest" ]; then
    notify_all "🧪 HORI health notifier self-test — channels live ($(hostname))" pass
    exit 0
fi

# --- wait for services to settle — only right after boot ---
MIN_UPTIME_S="${HORI_HEALTH_MIN_UPTIME_S:-90}"
UPTIME_S=$(cut -d. -f1 /proc/uptime)
if [ "$UPTIME_S" -lt "$MIN_UPTIME_S" ]; then
    sleep $((MIN_UPTIME_S - UPTIME_S))
fi

# --- run the check ---
OUT=$("$HEALTH_CHECK" 2>&1)
RC=$?
echo "$OUT"
if [ -d /var/log/hori ] && [ -w /var/log/hori ]; then
    echo "$OUT" >> "$LOG_FILE"
fi

PASS_N=$(grep -c "^  PASS:" <<<"$OUT" || true)
FAIL_N=$(grep -c "^  FAIL:" <<<"$OUT" || true)
WARN_N=$(grep -c "^  WARN:" <<<"$OUT" || true)

if [ "$RC" -eq 0 ]; then
    MSG="🟢 HORI spine healthy on $(hostname) — ${PASS_N} pass / ${FAIL_N} fail / ${WARN_N} warn"
    if [ "$WARN_N" -gt 0 ]; then
        MSG="$MSG"$'\n'"$(grep "^  WARN:" <<<"$OUT" | head -5 | sed 's/^  //')"
    fi
    notify_all "$MSG" pass
else
    MSG="🔴 HORI spine FAIL on $(hostname) — ${PASS_N} pass / ${FAIL_N} fail / ${WARN_N} warn"
    MSG="$MSG"$'\n'"$(grep "^  FAIL:" <<<"$OUT" | head -6 | sed 's/^  //')"
    MSG="$MSG"$'\n'"full log: journalctl -u hori-boot-health"
    notify_all "$MSG" fail
fi

exit "$RC"
