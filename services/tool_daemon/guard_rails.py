"""
PoC 15.13: Emergency Stop

Kill switch: /system/abort endpoint immediately halts all tool execution
and locks the tool layer. Voice command "stop" or "abort" triggers it.
Requires manual unlock via CLI. Physical kill switch documented.

Traces to: docs/roadmap.md Tier 2D, PoC 15.13.
Traces to: docs/safety.md "Layer 4: The Leash".

Design:
  - EmergencyStop is a simple flag. When aborted, can_execute_tools()
    returns False and all tool calls are rejected.
  - Unlock requires explicit action (unlock() method). There is no
    automatic timeout — the user must manually unlock via CLI.
  - abort() is idempotent — calling it multiple times is safe.
  - In production, this is triggered by:
      1. /system/abort endpoint in aios-core
      2. Voice command "stop" or "abort"
      3. Physical kill switch (documented, not implemented in code)

Why this exists (what it defends against):
  If the user notices the LLM behaving oddly (e.g., reading many files
  rapidly, or the Sherpa has triggered but the user wants to stop
  immediately), the emergency stop gives them a single-action kill switch.
  No confirmation dialog, no "are you sure?" — just immediate halt.
"""
from __future__ import annotations

import threading


class EmergencyStop:
    """Emergency stop for tool execution.

    When aborted, all tool calls are rejected. Unlock requires explicit
    action — no automatic timeout.
    """

    def __init__(self):
        self._aborted = False
        self._lock = threading.Lock()

    def abort(self) -> None:
        """Trigger the emergency stop. All tool calls will be rejected.

        This is idempotent — calling it when already aborted is safe.
        """
        with self._lock:
            self._aborted = True

    def unlock(self) -> None:
        """Unlock the emergency stop. Tool calls will be allowed again.

        This requires explicit action — there is no automatic timeout.
        In production, this is called via CLI (e.g., `aios-tool-unlock`).
        """
        with self._lock:
            self._aborted = False

    def is_aborted(self) -> bool:
        """Check if the emergency stop is active."""
        with self._lock:
            return self._aborted

    def can_execute_tools(self) -> bool:
        """Check if tool execution is allowed.

        Returns False if the emergency stop is active, True otherwise.
        The tool daemon calls this before every tool execution.
        """
        with self._lock:
            return not self._aborted
