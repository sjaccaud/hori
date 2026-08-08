"""Tests for PoC 15.14 + Gate Criteria: Safety events logger.

These tests verify that the safety events logger records EVERY response
verification, not just interceptions. This is required for the gate
criterion "Hallucination interception rate: 100% of claims-without-tool-
calls are intercepted (measurable from the audit log)" to be actually
measurable.

WHY THIS TEST EXISTS (Pillar VII — TDD for safety properties):
  The gate metric demands a rate: interceptions / claims-without-tool-calls.
  Before this change, only interceptions were logged — the denominator was
  invisible. The test asserts that every verify_and_log() call emits a
  response_verified event with claim_detected and intercepted fields, so
  the rate can be computed as:
      rate = intercepted / (claim_detected AND NOT tool_was_called)

TRACES TO:
  docs/roadmap.md Gate Criteria: AIOS 1.6 → AIOS 2.0, PoC 15.14.
"""
import json
from pathlib import Path

import pytest

from services.aios_core.safety_events import verify_and_log


@pytest.fixture
def tmp_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the safety events log to a temp file."""
    log_path = tmp_path / "safety_events.jsonl"
    # SAFETY_EVENTS_LOG is read at module import time, so we must patch
    # the module-level constant directly, not just the env var.
    monkeypatch.setattr("services.aios_core.safety_events.SAFETY_EVENTS_LOG", str(log_path))
    return log_path


def _read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


class TestVerifyAndLogAlwaysRecords:
    """Every call to verify_and_log must emit a response_verified event."""

    def test_safe_response_no_claim_is_logged(self, tmp_log: Path):
        """A response with no claim and no tool call must still be logged."""
        verify_and_log("Hello, how can I help?", tool_was_called=False,
                       conversation_id="c1", endpoint="/chat")
        events = _read_events(tmp_log)
        assert len(events) == 1
        assert events[0]["event_type"] == "response_verified"
        assert events[0]["claim_detected"] is False
        assert events[0]["intercepted"] is False
        assert events[0]["tool_was_called"] is False

    def test_safe_response_with_tool_call_is_logged(self, tmp_log: Path):
        """A response with a tool call (legitimate claim) must be logged."""
        verify_and_log("I found 847 files in your Projects directory.",
                       tool_was_called=True,
                       conversation_id="c2", endpoint="/v1/voice/chat")
        events = _read_events(tmp_log)
        assert len(events) == 1
        assert events[0]["event_type"] == "response_verified"
        assert events[0]["claim_detected"] is True
        assert events[0]["intercepted"] is False  # tool was called, so legit
        assert events[0]["tool_was_called"] is True

    def test_hallucination_intercepted_is_logged(self, tmp_log: Path):
        """A hallucinated claim without a tool call must be intercepted AND logged."""
        result = verify_and_log("I found 847 MIDI files in your Projects directory.",
                                tool_was_called=False,
                                conversation_id="c3", endpoint="/chat")
        events = _read_events(tmp_log)
        assert len(events) == 1
        assert events[0]["event_type"] == "response_verified"
        assert events[0]["claim_detected"] is True
        assert events[0]["intercepted"] is True
        assert events[0]["tool_was_called"] is False
        # The returned response must be the safe fallback
        assert "I cannot perform that action" in result

    def test_rate_is_computable_from_log(self, tmp_log: Path):
        """The log must contain enough information to compute the interception rate.

        Scenario: 3 turns — one hallucination, one legit tool claim, one safe.
        Rate = 1 intercepted / 1 claim-without-tool = 100%.
        """
        verify_and_log("Hello!", tool_was_called=False)  # no claim
        verify_and_log("I found 847 files.", tool_was_called=True)  # legit
        verify_and_log("I found 12 files.", tool_was_called=False)  # hallucination
        events = _read_events(tmp_log)
        assert len(events) == 3

        claims_without_tool = [
            e for e in events
            if e["claim_detected"] and not e["tool_was_called"]
        ]
        intercepted = [e for e in claims_without_tool if e["intercepted"]]
        assert len(claims_without_tool) == 1
        assert len(intercepted) == 1
        rate = len(intercepted) / len(claims_without_tool)
        assert rate == 1.0
