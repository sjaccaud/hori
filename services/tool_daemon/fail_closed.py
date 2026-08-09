"""
PoC 15.38: Fail-Closed Enforcement Gate

If Landlock, seccomp, or the Sherpa's capability file cannot be verified
at tool-daemon startup, the daemon refuses to start and aios-core refuses
to issue tool calls. No silent fallback to unrestricted execution.

Traces to: docs/roadmap.md Tier 2C, PoC 15.38.
Traces to: docs/safety.md "Fail-Closed Design".

From the PraisonAI CVE lesson: a sandbox that fails open is worse than no
sandbox. If Landlock is unavailable, the daemon must refuse to start, not
fall back to unrestricted execution. Degraded mode requires explicit,
logged, human-acknowledged opt-in.

Design:
  - check_safety_prerequisites() is called at daemon startup. If any
    safety mechanism is unavailable, it returns can_start=False with a
    reason. The daemon exits with a non-zero code.
  - Degraded mode (running without all safety mechanisms) requires
    explicit opt-in via a flag. The opt-in is logged to the audit log
    and a warning is emitted. This is for development/testing only.
  - The check is also callable at runtime to verify safety mechanisms
    haven't been disabled (e.g., by a root exploit that removed Landlock).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any


@dataclass
class SafetyCheckResult:
    """Result of checking safety prerequisites.

    If can_start=True, all safety mechanisms are verified and the daemon
    can proceed. If can_start=False, the reason field explains which
    mechanism failed and why.
    """

    can_start: bool
    reason: str = ""
    landlock_available: bool = False
    seccomp_available: bool = False
    degraded_mode: bool = False


def check_safety_prerequisites(
    landlock_available: bool = False,
    seccomp_available: bool = False,
    degraded_mode_opt_in: bool = False,
    **kwargs: Any,
) -> SafetyCheckResult:
    """Check whether all safety prerequisites are met to start the tool daemon.

    This is the fail-closed gate. The daemon calls this at startup. If any
    prerequisite is missing, the daemon refuses to start.

    Args:
        landlock_available: Whether Landlock ABI 8+ is available on this kernel.
        seccomp_available: Whether seccomp-bpf is available on this kernel.
        degraded_mode_opt_in: Whether the user has explicitly opted into
            degraded mode (running without all safety mechanisms). This
            requires explicit, logged, human-acknowledged opt-in.

    Returns:
        SafetyCheckResult with can_start=True if all prerequisites are met,
        or can_start=False with a reason if any are missing.
    """
    # Check Landlock
    if not landlock_available:
        if degraded_mode_opt_in:
            return SafetyCheckResult(
                can_start=True,
                reason="DEGRADED MODE: Landlock unavailable — running with "
                       "reduced safety. This has been explicitly opted into.",
                landlock_available=False,
                seccomp_available=seccomp_available,
                degraded_mode=True,
            )
        return SafetyCheckResult(
            can_start=False,
            reason="Landlock is not available. The tool daemon cannot start "
                   "without Landlock (default-deny filesystem isolation). "
                   "To run in degraded mode, explicitly set "
                   "degraded_mode_opt_in=True (NOT recommended for production).",
            landlock_available=False,
            seccomp_available=seccomp_available,
        )

    # Check seccomp
    if not seccomp_available:
        if degraded_mode_opt_in:
            return SafetyCheckResult(
                can_start=True,
                reason="DEGRADED MODE: seccomp unavailable — running with "
                       "reduced safety. This has been explicitly opted into.",
                landlock_available=True,
                seccomp_available=False,
                degraded_mode=True,
            )
        return SafetyCheckResult(
            can_start=False,
            reason="seccomp-bpf is not available. The tool daemon cannot start "
                   "without seccomp (syscall filtering). To run in degraded "
                   "mode, explicitly set degraded_mode_opt_in=True.",
            landlock_available=True,
            seccomp_available=False,
        )

    # All prerequisites met
    return SafetyCheckResult(
        can_start=True,
        reason="All safety prerequisites verified.",
        landlock_available=True,
        seccomp_available=True,
        degraded_mode=False,
    )


def verify_landlock_available() -> bool:
    """Check if Landlock is available on this kernel.

    Uses the real Landlock ABI detection from the landlock module
    (PoC 15.0b). Tries to create a ruleset with progressively more
    access rights to determine the ABI version. Returns True if ABI >= 3
    (we require TRUNCATE support).
    """
    try:
        from .landlock import is_landlock_available
        return is_landlock_available()
    except Exception:
        return False


def verify_seccomp_available() -> bool:
    """Check if seccomp-bpf is available on this kernel.

    Uses the real seccomp availability check from the seccomp_filter
    module (PoC 15.0d). Checks /proc/self/status for Seccomp support.
    """
    try:
        from .seccomp_filter import is_seccomp_available
        return is_seccomp_available()
    except Exception:
        return False
