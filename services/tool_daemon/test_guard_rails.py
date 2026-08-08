"""Tests for PoC 15.13: Emergency Stop."""
from services.tool_daemon.guard_rails import EmergencyStop


class TestEmergencyStop:
    def test_abort_halts_tool_execution(self):
        """After abort, tool calls must be rejected."""
        es = EmergencyStop()
        es.abort()
        assert es.is_aborted() is True
        assert es.can_execute_tools() is False

    def test_abort_requires_manual_unlock(self):
        """After abort, a manual unlock is required to resume."""
        es = EmergencyStop()
        es.abort()
        es.unlock()
        assert es.is_aborted() is False
        assert es.can_execute_tools() is True

    def test_abort_is_idempotent(self):
        """Calling abort multiple times should be safe."""
        es = EmergencyStop()
        es.abort()
        es.abort()
        es.abort()
        assert es.is_aborted() is True

    def test_unlock_when_not_aborted(self):
        """Unlocking when not aborted should be safe (no-op)."""
        es = EmergencyStop()
        es.unlock()
        assert es.is_aborted() is False
        assert es.can_execute_tools() is True

    def test_default_state_is_not_aborted(self):
        """The default state should allow tool execution."""
        es = EmergencyStop()
        assert es.is_aborted() is False
        assert es.can_execute_tools() is True

    def test_abort_unlock_abort_cycle(self):
        """The abort/unlock cycle should work repeatedly."""
        es = EmergencyStop()
        es.abort()
        assert es.can_execute_tools() is False
        es.unlock()
        assert es.can_execute_tools() is True
        es.abort()
        assert es.can_execute_tools() is False
        es.unlock()
        assert es.can_execute_tools() is True
