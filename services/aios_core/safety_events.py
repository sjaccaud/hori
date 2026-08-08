"""
PoC 15.14 + Gate Criteria: Safety events logger.

Logs EVERY response verification to a JSONL file for review by the audit
review tool (scripts/audit_review.py). This includes safe responses, not
just interceptions — the denominator is required to compute the rate.

WHY IT EXISTS:
  The gate criteria for AIOS 1.6 → 2.0 require measuring:
  - "Hallucination interception rate: 100% of claims-without-tool-calls
     are intercepted (measurable from the audit log)"

  The tool audit log (PoC 15.9) records tool calls, but hallucination
  interceptions happen in aios-core (not the tool daemon). This logger
  records every verification so the rate can be computed as:
      rate = intercepted / (claim_detected AND NOT tool_was_called)

  Before this change, only interceptions were logged — the denominator
  was invisible, making the gate metric unmeasurable. Now every
  verify_and_log() call emits a response_verified event with
  claim_detected and intercepted fields.

TRACES TO:
  docs/roadmap.md Gate Criteria, PoC 15.14.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from hori.config import SAFETY_EVENTS_LOG


def log_safety_event(
    event_type: str,
    *,
    original_response: str = "",
    replacement_response: str = "",
    tool_was_called: bool = False,
    claim_detected: bool = False,
    intercepted: bool = False,
    conversation_id: str | None = None,
    endpoint: str = "",
) -> None:
    """Log a safety event to the safety events log.

    Args:
        event_type: "response_verified", "hallucination_intercepted", etc.
        original_response: The original LLM response (before interception).
        replacement_response: The safe fallback response (if intercepted).
        tool_was_called: Whether a tool was actually called in this turn.
        claim_detected: Whether the response contains an action claim.
        intercepted: Whether the response was intercepted (claim + no tool).
        conversation_id: Optional conversation ID for correlation.
        endpoint: Which endpoint triggered this (e.g., "/v1/voice/chat").
    """
    entry = {
        "timestamp": time.time(),
        "event_type": event_type,
        "tool_was_called": tool_was_called,
        "claim_detected": claim_detected,
        "intercepted": intercepted,
        "original_response": original_response[:500],  # Truncate for log
        "replacement_response": replacement_response[:500],
        "conversation_id": conversation_id,
        "endpoint": endpoint,
    }
    try:
        log_path = Path(SAFETY_EVENTS_LOG)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # If we can't write to the log (e.g., /var/log/aios doesn't exist
        # in dev), silently skip. The interception still happens — we
        # just can't measure it.
        pass


def verify_and_log(
    response: str,
    tool_was_called: bool,
    *,
    conversation_id: str | None = None,
    endpoint: str = "",
) -> str:
    """Verify a response and log the verification.

    Every call is logged as a response_verified event, regardless of
    whether an interception occurred. This makes the gate metric
    (interception rate) measurable from the log alone:
        rate = intercepted / (claim_detected AND NOT tool_was_called)

    Args:
        response: The LLM's response text.
        tool_was_called: Whether a tool was actually called in this turn.
        conversation_id: Optional conversation ID for correlation.
        endpoint: Which endpoint triggered this (e.g., "/v1/voice/chat").

    Returns:
        The original response if safe, or the safe fallback if intercepted.
    """
    from services.tool_daemon.response_verification import (
        contains_action_claim,
        SAFE_FALLBACK,
    )

    claim_detected = contains_action_claim(response)
    intercepted = claim_detected and not tool_was_called
    result = SAFE_FALLBACK if intercepted else response

    log_safety_event(
        "response_verified",
        original_response=response,
        replacement_response=SAFE_FALLBACK if intercepted else "",
        tool_was_called=tool_was_called,
        claim_detected=claim_detected,
        intercepted=intercepted,
        conversation_id=conversation_id,
        endpoint=endpoint,
    )
    return result
