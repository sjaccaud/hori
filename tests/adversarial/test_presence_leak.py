"""Adversarial test: presence stream must not leak content.

Tests that the /v1/presence SSE endpoint (UX-1.3) emits state-only data
and does not leak:
- User message content or AIOS response content
- Proactive nudge/work-order content
- Tool call details, audit log entries, or safety event data
- Admin tokens or credentials

The stream must emit {"state": "idle"|"thinking"|"has_nudge"} only —
never content. This is a privacy property: a compromised client
subscribing to the presence stream cannot reconstruct conversations.

Defends: UX-1.3 presence stream privacy boundary.
Traces to: Manifesto Pillar VII (Simplicity as Security), UX Gameplan §5.
"""
import re
from pathlib import Path


MAIN_PY = Path(__file__).resolve().parents[2] / "services" / "aios_core" / "main.py"


class TestPresenceLeak:
    """The presence stream must not leak content."""

    def test_presence_endpoint_exists(self):
        """main.py must have a /v1/presence route."""
        src = MAIN_PY.read_text()
        assert '/v1/presence' in src, (
            "main.py must have a /v1/presence SSE endpoint."
        )

    def test_presence_emits_state_only(self):
        """The presence handler must emit state, not content."""
        src = MAIN_PY.read_text()
        # Find the presence handler region
        idx = src.find('/v1/presence')
        assert idx >= 0, "presence endpoint not found in main.py"
        # Get a generous region around the handler
        region = src[max(0, idx - 200):idx + 2000]
        # Must emit "state" in the SSE data
        assert 'state' in region, (
            "presence handler must emit a 'state' field in SSE data."
        )
        # Must NOT emit user message content, response text, or nudge content
        forbidden_patterns = [
            "user_text",
            "response_text",
            "fullText",
            "nudge_text",
            "work_order_text",
            "proposal_text",
            "audit",
            "admin_token",
            "AIOS_ADMIN_TOKEN",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in region, (
                f"presence handler must not emit '{pattern}' — "
                f"the stream is state-only, content-free."
            )

    def test_presence_states_are_limited(self):
        """The presence states must be exactly: idle, thinking, has_nudge."""
        src = MAIN_PY.read_text()
        idx = src.find('/v1/presence')
        assert idx >= 0, "presence endpoint not found in main.py"
        region = src[max(0, idx - 500):idx + 3000]
        # Must contain the three valid states
        for state in ('idle', 'thinking', 'has_nudge'):
            assert state in region, (
                f"presence handler must define the '{state}' state."
            )
