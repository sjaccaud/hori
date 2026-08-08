"""Tests for the PoC 10.3 notification pipeline.

Verifies the multi-channel notifier: formatting, channel fallback, idempotency
via the on-disk ledger, and graceful degradation when no channel is configured.
"""
import json
from unittest.mock import patch, MagicMock

import pytest

from services.proactive_agent import notifier
from services.proactive_agent.notifier import (
    format_proposals,
    notify_proposals,
    _send_telegram,
    _send_hass,
    _send_ntfy,
)


@pytest.fixture
def sample_work_orders():
    return [
        {
            "title": "Implement reward function auditing",
            "priority": "high",
            "rationale": "Reward hacking is a critical risk for autonomous agents.",
            "first_steps": "1. Audit reward signals. 2. Add adversarial tests.",
        },
        {
            "title": "Integrate AirLLM for efficient inference",
            "priority": "medium",
            "rationale": "Reduces VRAM pressure on consumer GPUs.",
            "first_steps": "1. Benchmark AirLLM. 2. Compare against llama.cpp.",
        },
    ]


def test_format_proposals_includes_all_fields(sample_work_orders):
    """The notification body should include title, priority, rationale, and first steps."""
    body = format_proposals(sample_work_orders)
    assert "AIOS has new work-order proposals" in body
    assert "Implement reward function auditing" in body
    assert "Integrate AirLLM for efficient inference" in body
    assert "HIGH" in body
    assert "MEDIUM" in body
    assert "Reward hacking" in body
    assert "Audit reward signals" in body


def test_format_proposals_handles_missing_fields():
    """Missing optional fields should not crash the formatter."""
    body = format_proposals([{"title": "Bare proposal"}])
    assert "Bare proposal" in body
    # No rationale/first_steps lines should be present.
    assert "Why:" not in body
    assert "First steps:" not in body


def test_send_telegram_skipped_without_credentials():
    """With no token/chat_id, Telegram should be a no-op returning False."""
    with patch.object(notifier, "TELEGRAM_BOT_TOKEN", ""), patch.object(notifier, "TELEGRAM_CHAT_ID", ""):
        assert _send_telegram("hello") is False


def test_send_hass_skipped_without_credentials():
    """With no URL/token, Home Assistant should be a no-op returning False."""
    with patch.object(notifier, "HASS_URL", ""), patch.object(notifier, "HASS_TOKEN", ""):
        assert _send_hass("hello") is False


def test_send_ntfy_skipped_without_topic():
    """With no topic, ntfy should be a no-op returning False."""
    with patch.object(notifier, "NTFY_TOPIC", ""):
        assert _send_ntfy("hello") is False


def test_send_telegram_success(sample_work_orders):
    """A successful Telegram API call should return True."""
    fake_resp = MagicMock()
    fake_resp.ok = True
    with patch.object(notifier, "TELEGRAM_BOT_TOKEN", "tok"), patch.object(notifier, "TELEGRAM_CHAT_ID", "123"), \
         patch("services.proactive_agent.notifier.requests.post", return_value=fake_resp) as mock_post:
        assert _send_telegram("hello") is True
        mock_post.assert_called_once()
        # Verify the chat_id and text were sent.
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["chat_id"] == "123"
        assert call_kwargs["json"]["text"] == "hello"


def test_send_telegram_failure_falls_through():
    """A failed Telegram API call should return False (caller falls through to next channel)."""
    fake_resp = MagicMock()
    fake_resp.ok = False
    fake_resp.status_code = 403
    fake_resp.text = "Forbidden"
    with patch.object(notifier, "TELEGRAM_BOT_TOKEN", "tok"), patch.object(notifier, "TELEGRAM_CHAT_ID", "123"), \
         patch("services.proactive_agent.notifier.requests.post", return_value=fake_resp):
        assert _send_telegram("hello") is False


def test_notify_proposals_no_channels_returns_false(sample_work_orders, tmp_path, monkeypatch):
    """With no channels configured, notify_proposals should return False, not crash."""
    monkeypatch.setattr(notifier, "NOTIFIED_LEDGER_PATH", tmp_path / "notified.json")
    with patch.object(notifier, "TELEGRAM_BOT_TOKEN", ""), patch.object(notifier, "TELEGRAM_CHAT_ID", ""), \
         patch.object(notifier, "HASS_URL", ""), patch.object(notifier, "HASS_TOKEN", ""), \
         patch.object(notifier, "NTFY_TOPIC", ""):
        assert notify_proposals(sample_work_orders) is False


def test_notify_proposals_idempotent(sample_work_orders, tmp_path, monkeypatch):
    """Re-notifying the same proposals should be skipped (ledger prevents spam)."""
    ledger = tmp_path / "notified.json"
    monkeypatch.setattr(notifier, "NOTIFIED_LEDGER_PATH", ledger)
    # Pre-seed the ledger with one of the two titles.
    ledger.write_text(json.dumps([{"title": sample_work_orders[0]["title"], "notified_at": "2026-01-01"}]))
    fake_resp = MagicMock()
    fake_resp.ok = True
    with patch.object(notifier, "TELEGRAM_BOT_TOKEN", "tok"), patch.object(notifier, "TELEGRAM_CHAT_ID", "123"), \
         patch("services.proactive_agent.notifier.requests.post", return_value=fake_resp) as mock_post:
        result = notify_proposals(sample_work_orders)
        assert result is True
        # Only the second (un-notified) proposal should be sent.
        sent_text = mock_post.call_args.kwargs["json"]["text"]
        assert sample_work_orders[1]["title"] in sent_text
        assert sample_work_orders[0]["title"] not in sent_text


def test_notify_proposals_empty_list_returns_false():
    """An empty work-order list should short-circuit to False."""
    assert notify_proposals([]) is False


def test_notify_proposals_falls_through_to_ntfy(sample_work_orders, tmp_path, monkeypatch):
    """If Telegram and HASS both fail/unconfigured, ntfy should be tried."""
    monkeypatch.setattr(notifier, "NOTIFIED_LEDGER_PATH", tmp_path / "notified.json")
    fake_resp = MagicMock()
    fake_resp.ok = True
    with patch.object(notifier, "TELEGRAM_BOT_TOKEN", ""), patch.object(notifier, "TELEGRAM_CHAT_ID", ""), \
         patch.object(notifier, "HASS_URL", ""), patch.object(notifier, "HASS_TOKEN", ""), \
         patch.object(notifier, "NTFY_TOPIC", "aios-test"), \
         patch("services.proactive_agent.notifier.requests.post", return_value=fake_resp) as mock_post:
        assert notify_proposals(sample_work_orders) is True
        # The ntfy call posts to the topic URL.
        called_url = mock_post.call_args.args[0]
        assert "aios-test" in called_url
