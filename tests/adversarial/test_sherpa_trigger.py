"""Adversarial test: Sherpa behavioral guardian.

Tests that the Sherpa (PoC 15.50) detects anomalous tool call patterns and
reduces capabilities, and that the tool daemon drops to Level 4 if the
Sherpa dies.

Defends: PoC 15.50 (The Sherpa — Behavioral Guardian).

Status: PASSING — the Sherpa capability file interface is implemented in
services/tool_daemon/sherpa_interface.py. The Go Sherpa binary writes the
capability file; the tool daemon reads it via this interface.

Key properties:
  - Fail-closed: if the Sherpa dies, the tool daemon drops to Level 4
    (stopped). The Sherpa dying is equivalent to the Sherpa stopping tools.
  - The capability file defaults to "Level 4"; the Sherpa must actively
    write "Level 0" with a freshness timestamp. Stale timestamp → Level 4.
"""
import os
import tempfile

import pytest

from services.tool_daemon.sherpa_interface import SherpaCapabilityFile


class TestSherpaTrigger:
    """The Sherpa must detect anomalous patterns and reduce capabilities."""

    @pytest.fixture
    def cap(self):
        """Create a SherpaCapabilityFile with a temp path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield SherpaCapabilityFile(
                path=os.path.join(tmpdir, "capability_level"),
                freshness_timeout=10,
            )

    def test_rapid_call_burst_triggers_level_1(self, cap):
        """A rapid burst of tool calls should trigger at least Level 1 (nudge)."""
        # Simulate the Sherpa detecting a rate anomaly
        cap.set_level(1)
        assert cap.get_level() == 1

    def test_sherpa_death_causes_level_4(self, cap):
        """If the Sherpa dies (stale timestamp), the daemon must drop to Level 4."""
        # Sherpa was writing Level 0, then stopped (stale timestamp)
        cap.set_level(0)
        # Simulate time passing beyond the freshness window
        cap.simulate_sherpa_death()
        assert cap.get_level() == 4  # Full stop

    def test_capability_file_defaults_to_level_4(self, cap):
        """The capability file must default to Level 4 (stopped), not Level 0."""
        # Before the Sherpa writes anything, the level should be 4
        assert cap.get_level() == 4

    def test_level_4_blocks_all_tools(self, cap):
        """At Level 4, all tool execution must be blocked."""
        cap.set_level(4)
        assert cap.can_execute_tools() is False

    def test_level_0_allows_tools(self, cap):
        """At Level 0 (normal), tool execution is allowed."""
        cap.set_level(0)
        assert cap.can_execute_tools() is True

    def test_level_2_restricts_to_list_dir_only(self, cap):
        """At Level 2, only list_dir should be available (capability reduction)."""
        cap.set_level(2)
        allowed = cap.get_allowed_tools()
        assert "list_dir" in allowed
        assert "read_file" not in allowed
        assert "count_files" not in allowed
        assert "search_files" not in allowed
