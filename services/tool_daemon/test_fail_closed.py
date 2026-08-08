"""Tests for PoC 15.38: Fail-Closed Enforcement Gate.

Verifies that the daemon refuses to start without Landlock and seccomp,
and that degraded mode requires explicit opt-in.
"""
import pytest

from services.tool_daemon.fail_closed import (
    SafetyCheckResult,
    check_safety_prerequisites,
)


class TestFailClosed:
    def test_refuses_without_landlock(self):
        """Without Landlock, the daemon must refuse to start."""
        result = check_safety_prerequisites(
            landlock_available=False,
            seccomp_available=True,
        )
        assert result.can_start is False
        assert "Landlock" in result.reason

    def test_refuses_without_seccomp(self):
        """Without seccomp, the daemon must refuse to start."""
        result = check_safety_prerequisites(
            landlock_available=True,
            seccomp_available=False,
        )
        assert result.can_start is False
        assert "seccomp" in result.reason

    def test_refuses_without_both(self):
        """Without both, the daemon must refuse to start."""
        result = check_safety_prerequisites(
            landlock_available=False,
            seccomp_available=False,
        )
        assert result.can_start is False

    def test_starts_with_all_safety(self):
        """With all safety mechanisms, the daemon can start."""
        result = check_safety_prerequisites(
            landlock_available=True,
            seccomp_available=True,
        )
        assert result.can_start is True
        assert result.degraded_mode is False

    def test_degraded_mode_requires_opt_in(self):
        """Degraded mode without opt-in must still refuse to start."""
        result = check_safety_prerequisites(
            landlock_available=False,
            seccomp_available=True,
            degraded_mode_opt_in=False,
        )
        assert result.can_start is False

    def test_degraded_mode_with_opt_in(self):
        """Degraded mode with explicit opt-in can start but is flagged."""
        result = check_safety_prerequisites(
            landlock_available=False,
            seccomp_available=True,
            degraded_mode_opt_in=True,
        )
        assert result.can_start is True
        assert result.degraded_mode is True
        assert "DEGRADED" in result.reason

    def test_degraded_mode_for_seccomp(self):
        """Degraded mode for seccomp also works with opt-in."""
        result = check_safety_prerequisites(
            landlock_available=True,
            seccomp_available=False,
            degraded_mode_opt_in=True,
        )
        assert result.can_start is True
        assert result.degraded_mode is True

    def test_no_silent_fallback(self):
        """There must be no code path that falls back to unrestricted execution."""
        # Even with both missing and no opt-in, can_start must be False
        result = check_safety_prerequisites(
            landlock_available=False,
            seccomp_available=False,
            degraded_mode_opt_in=False,
        )
        assert result.can_start is False

    def test_safety_check_result_fields(self):
        """SafetyCheckResult should report which mechanisms are available."""
        result = check_safety_prerequisites(
            landlock_available=True,
            seccomp_available=False,
        )
        assert result.landlock_available is True
        assert result.seccomp_available is False
