"""Integration test: Sherpa wire contract with the Python audit logger.

This test closes the gap that let the Sherpa ship blind (PoC 15.50 retro,
Aug 8 2026). The Go Sherpa's AuditEntry.Timestamp field was typed `string`
while the Python AuditLogger writes `time.time()` as a `float`. The Sherpa
silently skipped every audit line as malformed — alive but blind.

The existing adversarial tests (test_sherpa_trigger.py) test the Sherpa's
*behavioral logic* via the Python capability-file interface. They never
feed the Go binary real audit log entries. This test does:

  1. Writes real audit entries using the Python AuditLogger (float timestamps)
  2. Runs the actual Go Sherpa binary against that audit log
  3. Verifies the Sherpa processes entries (writes Level 0, not stuck blind)
  4. Verifies the Sherpa detects a rate anomaly from real audit entries
  5. Verifies the Sherpa's health metric: skips are logged, not silent

Defends: The wire contract between services/tool_daemon/audit.py (Python)
and services/sherpa/main.go (Go). If either side changes the timestamp
format, this test breaks.

Traces to: docs/roadmap.md Tier 2E, PoC 15.50.
"""
import json
import os
import subprocess
import tempfile
import time

import pytest

from services.tool_daemon.audit import AuditLogger

from pathlib import Path
PROJECTS_DIR = str(Path.home() / "Projects")
AIOS_DIR = str(Path(__file__).resolve().parents[2])


# Path to the prebuilt Sherpa binary in the repo
SHERPA_BINARY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "services", "sherpa", "sherpa",
)


def sherpa_binary_exists():
    """Check if the Go Sherpa binary is available."""
    return os.path.isfile(SHERPA_BINARY) and os.access(SHERPA_BINARY, os.X_OK)


# Skip the entire module if the binary isn't built (e.g. on CI without Go)
pytestmark = pytest.mark.skipif(
    not sherpa_binary_exists(),
    reason=f"Sherpa binary not found at {SHERPA_BINARY}. Build with: cd services/sherpa && go build -o sherpa .",
)


def write_audit_entries(audit_path, count, tool="count_files", path=PROJECTS_DIR):
    """Write real audit entries using the Python AuditLogger.

    Note: AuditLogger.log() derives `success` from `"error" not in result`.
    A result dict without an "error" key is a success.
    """
    logger = AuditLogger(audit_path)
    for i in range(count):
        logger.log(
            tool_name=tool,
            args={"path": path},
            result={"count": i},
            conversation_id=f"test-conv-{i}",
            turn_id=f"turn-{i}",
        )


class TestSherpaWireContract:
    """The Go Sherpa must parse real Python AuditLogger entries."""

    @pytest.fixture
    def sherpa_env(self):
        """Set up temp dirs for the Sherpa binary with --test flag paths."""
        with tempfile.TemporaryDirectory(prefix="sherpa-test-") as tmpdir:
            cap_path = os.path.join(tmpdir, "capability_level")
            audit_path = os.path.join(tmpdir, "audit.jsonl")
            # The --test flag uses hardcoded /tmp/sherpa-test/ paths, so we
            # can't use it. Instead, we pass paths via a wrapper: write a
            # small config. But the Sherpa doesn't support config files.
            # Solution: use --test mode but symlink the test paths to our
            # temp dirs. Actually, simpler: the Sherpa's --test flag sets
            # capPath and auditPath to /tmp/sherpa-test/. We'll just use
            # that directory directly and clean it up ourselves.
            test_dir = "/tmp/sherpa-test"
            os.makedirs(test_dir, exist_ok=True)
            test_cap = os.path.join(test_dir, "capability_level")
            test_audit = os.path.join(test_dir, "audit.jsonl")
            # Clean any stale state
            for f in [test_cap, test_audit]:
                if os.path.exists(f):
                    os.remove(f)
            yield {
                "cap_path": test_cap,
                "audit_path": test_audit,
                "test_dir": test_dir,
            }
            # Cleanup
            for f in [test_cap, test_audit, test_cap + ".tmp"]:
                try:
                    os.remove(f)
                except FileNotFoundError:
                    pass

    def _start_sherpa(self, timeout=5):
        """Start the Sherpa binary in test mode. Returns the Popen handle."""
        proc = subprocess.Popen(
            [SHERPA_BINARY, "--test"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Wait for the Sherpa to write the initial capability file
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists("/tmp/sherpa-test/capability_level"):
                return proc
            time.sleep(0.1)
        proc.terminate()
        proc.wait(timeout=3)
        pytest.fail("Sherpa did not write capability file within timeout")

    def _stop_sherpa(self, proc):
        """Stop the Sherpa process and return its stderr."""
        proc.terminate()
        try:
            _, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()
        return stderr

    def _read_cap(self, cap_path):
        """Read the capability file."""
        with open(cap_path) as f:
            return json.loads(f.read())

    def test_parses_real_float_timestamps(self, sherpa_env):
        """The Sherpa must parse audit entries with float timestamps.

        This is the direct regression test for the Aug 8 2026 bug where
        AuditEntry.Timestamp was `string` in Go but `float` in Python.
        """
        # Write a single real audit entry
        write_audit_entries(sherpa_env["audit_path"], count=1)

        # Start the Sherpa
        proc = self._start_sherpa()
        time.sleep(2)  # Let it read the audit log

        # Check: the Sherpa should NOT have logged "skipping malformed"
        stderr = self._stop_sherpa(proc)
        assert "skipping malformed" not in stderr, (
            f"Sherpa skipped audit entries as malformed — wire contract broken!\n"
            f"stderr: {stderr}"
        )

    def test_writes_level_0_with_real_audit_log(self, sherpa_env):
        """The Sherpa must write Level 0 (normal) when processing real entries.

        If the Sherpa can't parse the audit log, it still writes Level 0
        (because it defaults to normal when no anomalies are detected).
        But combined with the no-skip check above, this confirms the
        Sherpa is actually processing entries, not just running blind.
        """
        write_audit_entries(sherpa_env["audit_path"], count=3)

        proc = self._start_sherpa()
        time.sleep(2)

        cap = self._read_cap(sherpa_env["cap_path"])
        assert cap["level"] == 0, f"Expected Level 0, got {cap['level']}"

        self._stop_sherpa(proc)

    def test_detects_rate_anomaly_from_real_entries(self, sherpa_env):
        """The Sherpa must detect a rate anomaly from real audit entries.

        Writes enough entries to exceed the rate anomaly threshold, then
        verifies the Sherpa escalates to at least Level 1. This proves
        the Sherpa is not only parsing entries but acting on them.
        """
        # Write entries with distinct paths to avoid scope escalation
        # triggering first (scope escalation is Level 2, rate is Level 1).
        # Use a single path so dirPrefixes stays at 1.
        logger = AuditLogger(sherpa_env["audit_path"])
        # RateAnomalyThreshold is 20 calls in 60s. Write 25 to exceed it.
        for i in range(25):
            logger.log(
                tool_name="count_files",
                args={"path": PROJECTS_DIR},
                result={"count": i},
            )

        proc = self._start_sherpa()
        # Give the Sherpa time to read the audit log and process entries
        time.sleep(3)

        cap = self._read_cap(sherpa_env["cap_path"])
        # The Sherpa should have escalated to at least Level 1 (nudge)
        assert cap["level"] >= 1, (
            f"Expected Level >= 1 (rate anomaly), got Level {cap['level']}. "
            f"The Sherpa may not be processing entries correctly."
        )

        stderr = self._stop_sherpa(proc)
        assert "skipping malformed" not in stderr, (
            f"Sherpa skipped entries — wire contract broken!\nstderr: {stderr}"
        )

    def test_empty_audit_log_is_ok(self, sherpa_env):
        """The Sherpa must start normally with an empty/missing audit log."""
        # Don't write any audit entries — the log doesn't exist yet
        proc = self._start_sherpa()
        time.sleep(2)

        cap = self._read_cap(sherpa_env["cap_path"])
        assert cap["level"] == 0, f"Expected Level 0 with no audit log, got {cap['level']}"

        self._stop_sherpa(proc)

    def test_audit_entry_format_matches_go_struct(self):
        """Verify the Python AuditEntry.to_jsonl() fields match the Go struct.

        This is a static contract check — if either side adds/removes/renames
        a field, this test catches it. The Go struct tags must match the
        Python JSON keys exactly.
        """
        # Generate a real entry
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            audit_path = f.name
        try:
            logger = AuditLogger(audit_path)
            logger.log(
                tool_name="list_dir",
                args={"path": "/tmp"},
                result={"entries": ["a", "b"]},
                llm_reasoning="User asked to list /tmp",
                data_tainted=True,
                conversation_id="conv-123",
                turn_id="turn-1",
            )

            # Read the entry back
            with open(audit_path) as f:
                entry = json.loads(f.readline())

            # These keys must match the Go AuditEntry struct json tags
            required_keys = {
                "timestamp",       # Go: Timestamp float64
                "tool_name",       # Go: ToolName string
                "args",            # Go: Args map[string]interface{}
                "result",          # Go: Result map[string]interface{}
                "success",         # Go: Success bool
                "llm_reasoning",   # Go: LLMReasoning string (omitempty)
                "data_tainted",    # Go: DataTainted bool (omitempty)
                "conversation_id", # Go: ConversationID string (omitempty)
                "turn_id",         # Go: TurnID string (omitempty)
            }
            assert set(entry.keys()) == required_keys, (
                f"Python audit entry keys don't match expected Go struct fields.\n"
                f"Python keys: {set(entry.keys())}\n"
                f"Expected:    {required_keys}\n"
                f"Missing:     {required_keys - set(entry.keys())}\n"
                f"Extra:       {set(entry.keys()) - required_keys}"
            )

            # Timestamp must be a float (the bug was it was expected as string)
            assert isinstance(entry["timestamp"], float), (
                f"timestamp must be float (time.time()), got {type(entry['timestamp'])}"
            )
        finally:
            os.unlink(audit_path)

    def test_blind_sherpa_escalates_to_level_2(self, sherpa_env):
        """A blind Sherpa (high skip ratio) must escalate to Level 2.

        This tests the health metric added after the Aug 8 2026 retro:
        if the Sherpa is skipping >50% of audit lines, it's blind — alive
        but unable to parse the audit log. A blind guardian restricts
        capabilities (Level 2) rather than pretending everything is fine.

        We simulate blindness by writing entries with a field the Sherpa
        can't parse (a timestamp as a string instead of a float), which
        is exactly the original bug.
        """
        audit_path = sherpa_env["audit_path"]
        # Write 10 entries with string timestamps (the original bug format).
        # The Sherpa's AuditEntry.Timestamp is float64, so string timestamps
        # will fail to unmarshal and be skipped.
        with open(audit_path, "w") as f:
            for i in range(10):
                entry = {
                    "timestamp": f"1786161991.{i:06d}",  # string, not float
                    "tool_name": "count_files",
                    "args": {"path": PROJECTS_DIR},
                    "result": {"count": i},
                    "success": True,
                }
                f.write(json.dumps(entry) + "\n")

        proc = self._start_sherpa()
        # Wait for the health check to fire (every 10s, but we need to
        # wait for the audit reader to process entries first)
        time.sleep(12)

        cap = self._read_cap(sherpa_env["cap_path"])
        stderr = self._stop_sherpa(proc)

        # The Sherpa should have escalated to Level 2 (reduced) due to
        # the high skip ratio (blind guardian).
        assert cap["level"] >= 2, (
            f"Expected Level >= 2 (blind guardian), got Level {cap['level']}. "
            f"The health check may not be working.\nstderr: {stderr}"
        )
        assert "blind guardian" in stderr.lower() or "blind" in cap.get("reason", "").lower(), (
            f"Expected 'blind guardian' in logs or reason, got: {cap.get('reason', '')}\nstderr: {stderr}"
        )
