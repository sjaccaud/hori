"""Adversarial test: rate limiting and quotas.

Tests that the rate limiter (PoC 15.12) enforces max 10 tool calls per
conversation turn and max 50 per session.

Defends: PoC 15.12 (Rate Limiting & Quotas).

Status: FAILING (xfail) — the rate limiter (2D) is not yet implemented.
"""
import pytest

# TODO: Import from services.tool_daemon.rate_limiter once 2D is built
# from services.tool_daemon.rate_limiter import RateLimiter


class TestRateLimit:
    """Rate limiting must prevent excessive tool calls."""

    def test_max_10_calls_per_turn(self):
        """Exceeding 10 tool calls in a single turn must be rejected."""
        from services.tool_daemon.rate_limiter import RateLimiter
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        for i in range(10):
            assert limiter.check_and_increment("conv1", "turn1") is True
        # The 11th call should be rejected
        assert limiter.check_and_increment("conv1", "turn1") is False

    def test_max_50_calls_per_session(self):
        """Exceeding 50 tool calls in a session must be rejected."""
        from services.tool_daemon.rate_limiter import RateLimiter
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        # 5 turns * 10 calls = 50 (the session limit)
        for turn in range(5):
            for _ in range(10):
                assert limiter.check_and_increment("conv1", f"turn{turn}") is True
        # The 51st call should be rejected
        assert limiter.check_and_increment("conv1", "turn5") is False

    def test_different_conversations_independent(self):
        """Rate limits should be per-conversation, not global."""
        from services.tool_daemon.rate_limiter import RateLimiter
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        # Exhaust conv1's turn limit
        for _ in range(10):
            limiter.check_and_increment("conv1", "turn1")
        # conv2 should still be able to make calls
        assert limiter.check_and_increment("conv2", "turn1") is True

    def test_new_turn_resets_per_turn_counter(self):
        """A new turn should reset the per-turn counter but not the session counter."""
        from services.tool_daemon.rate_limiter import RateLimiter
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        for _ in range(10):
            limiter.check_and_increment("conv1", "turn1")
        # New turn — per-turn counter resets
        assert limiter.check_and_increment("conv1", "turn2") is True
