"""Tests for PoC 15.12: Rate Limiting & Quotas."""
import pytest

from services.tool_daemon.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_max_10_calls_per_turn(self):
        """Exceeding 10 tool calls in a single turn must be rejected."""
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        for i in range(10):
            assert limiter.check_and_increment("conv1", "turn1") is True
        # The 11th call should be rejected
        assert limiter.check_and_increment("conv1", "turn1") is False

    def test_max_50_calls_per_session(self):
        """Exceeding 50 tool calls in a session must be rejected."""
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        # 5 turns * 10 calls = 50 (the session limit)
        for turn in range(5):
            for _ in range(10):
                assert limiter.check_and_increment("conv1", f"turn{turn}") is True
        # The 51st call should be rejected
        assert limiter.check_and_increment("conv1", "turn5") is False

    def test_different_conversations_independent(self):
        """Rate limits should be per-conversation, not global."""
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        # Exhaust conv1's turn limit
        for _ in range(10):
            limiter.check_and_increment("conv1", "turn1")
        # conv2 should still be able to make calls
        assert limiter.check_and_increment("conv2", "turn1") is True

    def test_new_turn_resets_per_turn_counter(self):
        """A new turn should reset the per-turn counter but not the session counter."""
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        for _ in range(10):
            limiter.check_and_increment("conv1", "turn1")
        # New turn — per-turn counter resets
        assert limiter.check_and_increment("conv1", "turn2") is True
        # But session counter should be 11
        assert limiter.get_session_count("conv1") == 11

    def test_get_turn_count(self):
        """get_turn_count should return the number of calls in a turn."""
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        for _ in range(5):
            limiter.check_and_increment("conv1", "turn1")
        assert limiter.get_turn_count("conv1", "turn1") == 5

    def test_get_session_count(self):
        """get_session_count should return total calls in a session."""
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        for _ in range(3):
            limiter.check_and_increment("conv1", "turn1")
        for _ in range(2):
            limiter.check_and_increment("conv1", "turn2")
        assert limiter.get_session_count("conv1") == 5

    def test_nonexistent_conversation(self):
        """Counts for nonexistent conversations should be 0."""
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        assert limiter.get_turn_count("nonexistent", "turn1") == 0
        assert limiter.get_session_count("nonexistent") == 0

    def test_reset(self):
        """reset should clear all counters."""
        limiter = RateLimiter(max_per_turn=10, max_per_session=50)
        for _ in range(10):
            limiter.check_and_increment("conv1", "turn1")
        limiter.reset()
        assert limiter.get_session_count("conv1") == 0
        assert limiter.check_and_increment("conv1", "turn1") is True

    def test_session_limit_blocks_even_in_new_turn(self):
        """Once session limit is hit, new turns can't bypass it."""
        limiter = RateLimiter(max_per_turn=10, max_per_session=15)
        # Use 15 calls across 2 turns
        for _ in range(10):
            limiter.check_and_increment("conv1", "turn1")
        for _ in range(5):
            limiter.check_and_increment("conv1", "turn2")
        # Session limit hit — even a new turn should be blocked
        assert limiter.check_and_increment("conv1", "turn3") is False
