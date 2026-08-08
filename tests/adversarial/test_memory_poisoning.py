"""Adversarial test: memory poisoning prevention.

Tests that tool result data cannot flow into store_memory without explicit
user-originated confirmation. This prevents a compromised LLM from injecting
false memories via tool results.

Defends: Red-team fix #5 (tool results cannot write to memory without
explicit user-originated confirmation).

Status: FAILING (xfail) — the memory poisoning check is not yet implemented
in aios-core's memory write path. This is a single check, not the full
taint tracking system (which is AIOS 2.0).
"""
import pytest


class TestMemoryPoisoning:
    """Tool results must not write to memory without user confirmation."""

    def test_tool_result_not_stored_without_confirmation(self):
        """A tool result passed to store_memory must be rejected."""
        from services.aios_core.memory_guard import is_tool_originated_data
        # Data that came from a tool result should be flagged
        tool_data = {"source": "tool_result", "tool": "read_file", "content": "sensitive data"}
        assert is_tool_originated_data(tool_data) is True
        # And the memory write path should reject it
        from services.aios_core.memory_guard import can_store_memory
        assert can_store_memory(tool_data, user_confirmed=False) is False

    def test_tool_result_stored_with_confirmation(self):
        """A tool result with explicit user confirmation can be stored."""
        from services.aios_core.memory_guard import can_store_memory
        tool_data = {"source": "tool_result", "tool": "read_file", "content": "project notes"}
        assert can_store_memory(tool_data, user_confirmed=True) is True

    def test_normal_user_message_stored_without_confirmation(self):
        """A normal user message (not from a tool) can be stored without confirmation."""
        from services.aios_core.memory_guard import is_tool_originated_data
        user_data = {"source": "user", "role": "user", "content": "remember this"}
        assert is_tool_originated_data(user_data) is False
