"""
Proactive Opportunity Agent - Notification Pipeline (PoC 10.3)

Routes "Yes, AND" work-order proposals to the user via push notifications so
they reach the iPhone (or any device) without the user having to poll
core/state/proposed_work_orders.json.

Traces to: docs/roadmap.md Tier 1B, PoC 10.3.

Design:
- Multi-channel: Telegram (primary), Home Assistant (secondary), ntfy.sh
  (lightweight fallback). Channels are tried in order; the first that is
  configured and succeeds wins. Failures fall through to the next channel.
- No new dependencies: uses requests (already in the stack) for Telegram and
  ntfy, and Home Assistant's REST API. Apprise was considered but rejected
  to avoid adding a dependency for a feature that may only need one channel.
- Credentials come from environment variables, NOT from .env files in the
  project tree (per docs/system_security_audit.md: API keys should not sit
  in plaintext files the tool service could read). The Telegram bot token
  is read from TELEGRAM_BOT_TOKEN; the chat ID from TELEGRAM_CHAT_ID. Both
  can be set in the systemd unit's Environment= lines (Tier 1A, PoC 1.0.2).
- Idempotent: each work order is notified at most once. A small on-disk
  ledger (core/state/notified_proposals.json) records which proposal titles
  have already been pushed, so re-running the proposer doesn't spam the user.

Usage:
    from services.proactive_agent.notifier import notify_proposals
    notify_proposals(work_orders)
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOTIFIED_LEDGER_PATH = PROJECT_ROOT / "core" / "state" / "notified_proposals.json"

# Channel configuration (all optional — a channel is only used if its
# credentials are present in the environment).
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

HASS_URL = os.getenv("HASS_URL", "").rstrip("/")
HASS_TOKEN = os.getenv("HASS_TOKEN", "")
HASS_NOTIFY_SERVICE = os.getenv("HASS_NOTIFY_SERVICE", "notify.mobile_app")

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

# Webhook channel (generic JSON POST — works with Slack, Discord, custom endpoints)
WEBHOOK_URL = os.getenv("HORI_WEBHOOK_URL", "")

# Telegram caps a single message at 4096 chars. We leave headroom for the
# header and per-proposal formatting.
TELEGRAM_MAX_CHARS = 4000


def format_proposals(work_orders: List[dict]) -> str:
    """Render work orders into a human-readable push-notification body.

    Each proposal gets a priority marker, title, rationale, and first steps.
    The message is prefixed with a header so the user knows it's from AIOS
    and can distinguish a "Yes, AND" proposal from a direct message.
    """
    lines = ["HORI has new work-order proposals for you:\n"]
    for wo in work_orders:
        priority = wo.get("priority", "?")
        title = wo.get("title", "(untitled)")
        rationale = wo.get("rationale", "")
        first_steps = wo.get("first_steps", "")
        marker = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")
        lines.append(f"{marker} [{priority.upper()}] {title}")
        if rationale:
            lines.append(f"   Why: {rationale}")
        if first_steps:
            lines.append(f"   First steps: {first_steps}")
        lines.append("")
    lines.append("Review at /system/proposals or say \"show me today's proposals\".")
    return "\n".join(lines)


def _send_telegram(text: str) -> bool:
    """Send a message via the Telegram Bot API. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.ok:
            logger.info("Notification sent via Telegram to chat %s.", TELEGRAM_CHAT_ID)
            return True
        logger.warning("Telegram sendMessage failed: %s %s", resp.status_code, resp.text[:200])
    except requests.RequestException as e:
        logger.warning("Telegram request failed: %s", e)
    return False


def _send_hass(text: str) -> bool:
    """Send a notification via Home Assistant's REST API. Returns True on success."""
    if not HASS_URL or not HASS_TOKEN:
        return False
    url = f"{HASS_URL}/api/services/{HASS_NOTIFY_SERVICE}"
    headers = {
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json={"message": text}, headers=headers, timeout=10)
        if resp.ok:
            logger.info("Notification sent via Home Assistant (%s).", HASS_NOTIFY_SERVICE)
            return True
        logger.warning("Home Assistant notify failed: %s %s", resp.status_code, resp.text[:200])
    except requests.RequestException as e:
        logger.warning("Home Assistant request failed: %s", e)
    return False


def _send_ntfy(text: str, title: str = "HORI Proposals") -> bool:
    """Send a notification via an ntfy.sh server. Returns True on success."""
    if not NTFY_TOPIC:
        return False
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    try:
        resp = requests.post(url, data=text, headers={"Title": title, "Priority": "default"}, timeout=10)
        if resp.ok:
            logger.info("Notification sent via ntfy topic %s.", NTFY_TOPIC)
            return True
        logger.warning("ntfy publish failed: %s %s", resp.status_code, resp.text[:200])
    except requests.RequestException as e:
        logger.warning("ntfy request failed: %s", e)
    return False


def _send_webhook(text: str) -> bool:
    """Send a notification via a generic webhook (JSON POST).

    Works with Slack incoming webhooks, Discord webhooks, or any endpoint
    that accepts a JSON body with a 'text' field. Returns True on success.
    """
    if not WEBHOOK_URL:
        return False
    try:
        resp = requests.post(
            WEBHOOK_URL,
            json={"text": text, "username": "HORI"},
            timeout=10,
        )
        if resp.ok:
            logger.info("Notification sent via webhook.")
            return True
        logger.warning("Webhook failed: %s %s", resp.status_code, resp.text[:200])
    except requests.RequestException as e:
        logger.warning("Webhook request failed: %s", e)
    return False


def _load_notified_ledger() -> set:
    """Return the set of proposal titles already notified."""
    try:
        data = json.loads(NOTIFIED_LEDGER_PATH.read_text())
        return {entry["title"] for entry in data if "title" in entry}
    except Exception:
        return set()


def _mark_notified(work_orders: List[dict]) -> None:
    """Record notified proposals so we don't re-send them on the next run."""
    try:
        existing = json.loads(NOTIFIED_LEDGER_PATH.read_text())
    except Exception:
        existing = []
    now = datetime.now().isoformat()
    for wo in work_orders:
        existing.append({"title": wo.get("title", ""), "notified_at": now})
    # Keep the ledger bounded — last 200 entries.
    NOTIFIED_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTIFIED_LEDGER_PATH.write_text(json.dumps(existing[-200:], indent=2))


def notify_proposals(work_orders: List[dict]) -> bool:
    """Push work-order proposals to the user via the first available channel.

    Returns True if at least one notification was delivered. Proposals that
    have already been notified (per the on-disk ledger) are skipped, so this
    is safe to call repeatedly.
    """
    if not work_orders:
        return False

    already_notified = _load_notified_ledger()
    fresh = [wo for wo in work_orders if wo.get("title", "") not in already_notified]
    if not fresh:
        logger.info("No new proposals to notify (all already sent).")
        return False

    body = format_proposals(fresh)
    # Telegram has a 4096-char cap; chunk if needed.
    delivered = False
    if len(body) > TELEGRAM_MAX_CHARS and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        for i in range(0, len(body), TELEGRAM_MAX_CHARS):
            if _send_telegram(body[i : i + TELEGRAM_MAX_CHARS]):
                delivered = True
    else:
        for sender in (_send_telegram, _send_hass, _send_webhook):
            if sender(body):
                delivered = True
                break
        if not delivered:
            delivered = _send_ntfy(body)

    if delivered:
        _mark_notified(fresh)
        logger.info("Notified user about %d new proposal(s).", len(fresh))
        # UX-1.3: set presence to has_nudge so the web surfaces glow
        try:
            import asyncio as _aio
            from services.aios_core.main import set_presence
            try:
                _aio.get_event_loop().create_task(set_presence("has_nudge"))
            except RuntimeError:
                pass  # no event loop in this context
        except ImportError:
            pass  # aios-core not importable in this context
    else:
        logger.warning(
            "No notification channel succeeded. Set TELEGRAM_BOT_TOKEN + "
            "TELEGRAM_CHAT_ID, HASS_URL + HASS_TOKEN, or NTFY_TOPIC to enable "
            "push notifications. Proposals are still saved to "
            "core/state/proposed_work_orders.json."
        )
    return delivered


if __name__ == "__main__":
    # Smoke test: send a single dummy proposal if a channel is configured.
    logging.basicConfig(level=logging.INFO)
    demo = [
        {
            "title": "[demo] Notification pipeline test",
            "priority": "low",
            "rationale": "Verifying the PoC 10.3 notification pipeline works end-to-end.",
            "first_steps": "Check your phone; if you see this, it works.",
        }
    ]
    ok = notify_proposals(demo)
    print("Delivered:" if ok else "No channel configured/available.", ok)
