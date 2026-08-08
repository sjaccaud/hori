"""Adversarial test: fail-closed enforcement.

Tests that the tool daemon refuses to start if Landlock, seccomp, or the
Sherpa's capability file cannot be verified. No silent fallback to
unrestricted execution.

Defends: PoC 15.38 (Fail-Closed Enforcement Gate).

Status: FAILING (xfail) — the fail-closed gate (2C) is not yet implemented.

From the PraisonAI CVE lesson: a sandbox that fails open is worse than no
sandbox. If Landlock is unavailable, the daemon must refuse to start, not
fall back to unrestricted execution.
"""
import pytest


class TestFailClosed:
    """The tool daemon must fail closed if safety mechanisms are unavailable."""

    def test_daemon_refuses_to_start_without_landlock(self):
        """If Landlock is unavailable, the daemon must refuse to start."""
        from services.tool_daemon.fail_closed import check_safety_prerequisites
        # Simulate Landlock unavailable
        result = check_safety_prerequisites(landlock_available=False, seccomp_available=True)
        assert result.can_start is False
        assert "Landlock" in result.reason

    def test_daemon_refuses_to_start_without_seccomp(self):
        """If seccomp is unavailable, the daemon must refuse to start."""
        from services.tool_daemon.fail_closed import check_safety_prerequisites
        result = check_safety_prerequisites(landlock_available=True, seccomp_available=False)
        assert result.can_start is False
        assert "seccomp" in result.reason

    def test_daemon_starts_with_all_safety(self):
        """With all safety mechanisms available, the daemon can start."""
        from services.tool_daemon.fail_closed import check_safety_prerequisites
        result = check_safety_prerequisites(landlock_available=True, seccomp_available=True)
        assert result.can_start is True

    def test_no_silent_fallback(self):
        """There must be no code path that falls back to unrestricted execution."""
        from services.tool_daemon.fail_closed import check_safety_prerequisites
        # Even if someone sets a "degraded mode" flag, it should require
        # explicit, logged, human-acknowledged opt-in.
        result = check_safety_prerequisites(
            landlock_available=False,
            seccomp_available=False,
            degraded_mode_opt_in=False,
        )
        assert result.can_start is False
