"""
PoC 15.50 (Python side): Sherpa capability file interface.

The tool daemon reads the capability file written by the Sherpa (a Go
binary running as user `sherpa`) to determine what capability level it
should operate at. This module is the tool daemon's view of the Sherpa.

WHY IT EXISTS:
  Per-call validation (PoC 15.4) can only see individual tool calls.
  The most dangerous attacks are PATTERNS — a sequence of individually-
  benign calls that collectively constitute an attack. The Sherpa watches
  the trajectory; this module reads its verdict.

WHAT IT DEFENDS AGAINST:
  - Rapid tool call bursts (scanning the filesystem for secrets)
  - Scope escalation (progressively more sensitive paths)
  - Claim mismatch (LLM claims actions not in audit log)
  - After-hours activity (tool calls when user is inactive)
  - Sherpa death (if the guardian dies, tools stop — fail closed)

FAIL-CLOSED DESIGN (red-team fix #3):
  The capability file defaults to Level 4 (stopped). The Sherpa must
  actively write Level 0 (normal) with a freshness timestamp every N
  seconds. If the tool daemon sees a stale or missing timestamp, it
  drops to Level 4. The Sherpa dying is equivalent to the Sherpa
  stopping tools — fail closed, always, even for the guardian itself.

CAPABILITY FILE FORMAT:
  JSON, one line:
  {"level": 0, "timestamp": 1691234567, "reason": "normal"}

  The timestamp is a Unix epoch second. The freshness window is
  FRESHNESS_TIMEOUT_SECONDS (default 10s). If now - timestamp > window,
  the level is treated as 4 regardless of what the file says.

TRACES TO:
  docs/roadmap.md Tier 2E, PoC 15.50.
  docs/safety.md "The Sherpa — Behavioral Guardian".
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The capability file path. In production, the Sherpa writes here and the
# tool daemon reads it. The directory /run/sherpa/ is created by the
# install script with permissions root:sherpa 0750 (Sherpa can write,
# aios-worker can read).
DEFAULT_CAPABILITY_FILE = "/run/sherpa/capability_level"

# The freshness window. If the Sherpa hasn't written to the capability
# file within this many seconds, the tool daemon assumes the Sherpa is
# dead and drops to Level 4.
FRESHNESS_TIMEOUT_SECONDS = 10

# ---------------------------------------------------------------------------
# Capability levels
# ---------------------------------------------------------------------------

LEVEL_0_NORMAL = 0       # All tools allowed
LEVEL_1_NUDGE = 1        # All tools allowed (nudge is just a message)
LEVEL_2_REDUCED = 2      # list_dir only
LEVEL_3_SEQUESTRATION = 3  # No tools (temporary pause)
LEVEL_4_FULL_STOP = 4    # No tools (requires manual unlock)

# Tool availability per level. At Level 0 and 1, all tools are available.
# At Level 2, only list_dir. At Level 3 and 4, no tools.
TOOLS_BY_LEVEL: dict[int, list[str]] = {
    LEVEL_0_NORMAL: ["list_dir", "read_file", "count_files", "search_files"],
    LEVEL_1_NUDGE: ["list_dir", "read_file", "count_files", "search_files"],
    LEVEL_2_REDUCED: ["list_dir"],
    LEVEL_3_SEQUESTRATION: [],
    LEVEL_4_FULL_STOP: [],
}


class SherpaCapabilityFile:
    """Read and write the Sherpa capability file.

    In production, the tool daemon creates this with the default path
    (/run/sherpa/capability_level) and only reads it. The Sherpa (Go
    binary) writes to it.

    In testing, a temporary path is used and the test can write levels
    and simulate Sherpa death.
    """

    def __init__(
        self,
        path: str = DEFAULT_CAPABILITY_FILE,
        freshness_timeout: float = FRESHNESS_TIMEOUT_SECONDS,
    ):
        self.path = Path(path)
        self.freshness_timeout = freshness_timeout
        # Ensure the parent directory exists (for testing)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_raw(self) -> dict | None:
        """Read the raw capability file. Returns None if missing or invalid."""
        try:
            content = self.path.read_text(encoding="utf-8").strip()
            if not content:
                return None
            data = json.loads(content)
            if not isinstance(data, dict):
                return None
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def get_level(self) -> int:
        """Get the current capability level (0-4).

        Returns Level 4 (full stop) if:
          - The capability file doesn't exist (Sherpa hasn't started)
          - The file is invalid JSON
          - The timestamp is stale (Sherpa is dead)
          - The level field is missing or invalid

        This is the fail-closed behavior: anything unexpected → Level 4.
        """
        data = self._read_raw()
        if data is None:
            import sys
            print("Sherpa get_level: data is None → Level 4", file=sys.stderr)
            return LEVEL_4_FULL_STOP

        # Check freshness
        timestamp = data.get("timestamp", 0)
        if not isinstance(timestamp, (int, float)):
            import sys
            print(f"Sherpa get_level: bad timestamp type {type(timestamp)} → Level 4", file=sys.stderr)
            return LEVEL_4_FULL_STOP

        now = time.time()
        age = now - timestamp
        if age > self.freshness_timeout:
            import sys
            print(f"Sherpa get_level: stale timestamp (age={age:.1f}s > {self.freshness_timeout}s) → Level 4", file=sys.stderr)
            return LEVEL_4_FULL_STOP

        # Check level
        level = data.get("level", LEVEL_4_FULL_STOP)
        if not isinstance(level, int) or level < 0 or level > 4:
            import sys
            print(f"Sherpa get_level: bad level {level} → Level 4", file=sys.stderr)
            return LEVEL_4_FULL_STOP

        return level

    def can_execute_tools(self) -> bool:
        """Check if any tool execution is allowed at the current level."""
        return self.get_level() < LEVEL_4_FULL_STOP

    def get_allowed_tools(self) -> list[str]:
        """Get the list of tools allowed at the current capability level.

        Returns an empty list at Level 3+ (no tools allowed).
        """
        level = self.get_level()
        return TOOLS_BY_LEVEL.get(level, [])

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a specific tool is allowed at the current level."""
        return tool_name in self.get_allowed_tools()

    # --- Write methods (for testing and Sherpa use) ---

    def set_level(self, level: int, reason: str = "") -> None:
        """Write a capability level with a freshness timestamp.

        In production, this is called by the Sherpa (Go binary). In
        testing, this is called by the test to simulate Sherpa behavior.
        """
        if not 0 <= level <= 4:
            raise ValueError(f"Level must be 0-4, got {level}")
        data = {
            "level": level,
            "timestamp": int(time.time()),
            "reason": reason or self._level_reason(level),
        }
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def simulate_sherpa_death(self) -> None:
        """Simulate the Sherpa dying by writing a stale timestamp.

        This writes a valid level (0) but with a timestamp far in the
        past, so get_level() will return 4 (stale → fail closed).
        """
        data = {
            "level": 0,
            "timestamp": int(time.time()) - self.freshness_timeout - 100,
            "reason": "simulated death",
        }
        self.path.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def _level_reason(level: int) -> str:
        reasons = {
            0: "normal",
            1: "nudge — high tool call rate",
            2: "capability reduction — scope escalation",
            3: "sequestration — concerning pattern",
            4: "full stop — manual unlock required",
        }
        return reasons.get(level, "unknown")
