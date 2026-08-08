"""Adversarial test: emergency stop.

Tests that the /system/abort endpoint (PoC 15.13) immediately halts all
tool execution and locks the tool layer.

Defends: PoC 15.13 (Emergency Stop).

Status: FAILING (xfail) — the emergency stop (2D) is not yet implemented.
"""
import pytest


class TestEmergencyStop:
    """Emergency stop must immediately halt all tool execution."""

    def test_abort_halts_tool_execution(self):
        """After /system/abort, tool calls must be rejected."""
        from services.tool_daemon.guard_rails import EmergencyStop
        es = EmergencyStop()
        es.abort()
        assert es.is_aborted() is True
        # Tool calls should be rejected while aborted
        assert es.can_execute_tools() is False

    def test_abort_requires_manual_unlock(self):
        """After abort, a manual unlock is required to resume."""
        from services.tool_daemon.guard_rails import EmergencyStop
        es = EmergencyStop()
        es.abort()
        # Unlock should require explicit action, not automatic timeout
        es.unlock()
        assert es.is_aborted() is False
        assert es.can_execute_tools() is True

    def test_abort_is_idempotent(self):
        """Calling abort multiple times should be safe."""
        from services.tool_daemon.guard_rails import EmergencyStop
        es = EmergencyStop()
        es.abort()
        es.abort()  # Should not crash
        es.abort()
        assert es.is_aborted() is True
