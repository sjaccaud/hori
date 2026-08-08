"""
PoC 15.12: Rate Limiting & Quotas

Max 10 tool calls per conversation turn. Max 50 per session. Prevents the
agent from reading the entire filesystem even if each individual call is valid.

Traces to: docs/roadmap.md Tier 2D, PoC 15.12.
Traces to: docs/tool_safety.md "Layer 4: The Leash".

Design:
  - The RateLimiter tracks calls per (conversation_id, turn_id) and per
    conversation_id (session).
  - check_and_increment() returns True if the call is allowed (under both
    limits) and False if it would exceed either limit.
  - The limiter is in-memory (no persistence needed for the spine — if
    the daemon restarts, the counters reset, which is safe).
  - The tool daemon (PoC 15.5) calls check_and_increment() before every
    tool execution. If it returns False, the call is rejected with an
    error message back to the LLM.

Why this exists (what it defends against):
  Without rate limiting, a compromised LLM could make thousands of
  read_file calls to enumerate the entire filesystem within allowed
  paths. Each individual call would be valid, but the aggregate pattern
  is a data exfiltration attack. Rate limiting caps the damage.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _TurnCounter:
    """Counter for a single conversation turn."""

    calls: int = 0


@dataclass
class _SessionCounter:
    """Counter for a full conversation session."""

    total_calls: int = 0
    turns: dict[str, _TurnCounter] = field(default_factory=dict)


class RateLimiter:
    """Rate limiter for tool calls.

    Enforces:
      - max_per_turn: maximum tool calls in a single conversation turn
      - max_per_session: maximum tool calls in a full conversation session

    The limiter is in-memory. If the daemon restarts, counters reset.
    This is safe — the rate limit is a defense against rapid enumeration,
    not a persistent quota.
    """

    def __init__(self, max_per_turn: int = 10, max_per_session: int = 50):
        self.max_per_turn = max_per_turn
        self.max_per_session = max_per_session
        self._sessions: dict[str, _SessionCounter] = {}

    def check_and_increment(self, conversation_id: str, turn_id: str) -> bool:
        """Check if a tool call is allowed under the rate limits.

        Returns True if the call is allowed (and increments the counters).
        Returns False if the call would exceed either the per-turn or
        per-session limit.

        The tool daemon calls this before every tool execution. If it
        returns False, the call is rejected.
        """
        session = self._sessions.setdefault(conversation_id, _SessionCounter())

        # Check session limit
        if session.total_calls >= self.max_per_session:
            return False

        # Check turn limit
        turn = session.turns.setdefault(turn_id, _TurnCounter())
        if turn.calls >= self.max_per_turn:
            return False

        # Increment both counters
        turn.calls += 1
        session.total_calls += 1
        return True

    def get_turn_count(self, conversation_id: str, turn_id: str) -> int:
        """Get the number of calls made in a specific turn."""
        session = self._sessions.get(conversation_id)
        if session is None:
            return 0
        turn = session.turns.get(turn_id)
        return turn.calls if turn else 0

    def get_session_count(self, conversation_id: str) -> int:
        """Get the total number of calls made in a session."""
        session = self._sessions.get(conversation_id)
        return session.total_calls if session else 0

    def reset(self) -> None:
        """Reset all counters. Used in tests."""
        self._sessions.clear()
