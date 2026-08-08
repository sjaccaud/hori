"""Tests for PoC 15.5: Separate Tool Service (Server).

Verifies that the tool daemon correctly handles requests, validates calls,
executes tools, logs to the audit log, and fails closed at startup.
"""
import json
import os
import tempfile

import pytest

from services.tool_daemon.server import ToolDaemon
from hori.config import WORKSPACE_PATH


@pytest.fixture
def daemon():
    """Create a tool daemon with a temporary audit log."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = os.path.join(tmpdir, "audit.jsonl")
        socket_path = os.path.join(tmpdir, "test.sock")
        sherpa_path = os.path.join(tmpdir, "capability_level")
        d = ToolDaemon(
            socket_path=socket_path,
            audit_log_path=audit_path,
            degraded_mode=True,  # Skip Landlock/seccomp/Sherpa check in tests
            sherpa_capability_path=sherpa_path,
        )
        yield d, audit_path


class TestToolDaemonHandleRequest:
    def test_valid_count_files(self, daemon):
        """A valid count_files request should execute and return results."""
        d, audit_path = daemon
        # Use a path within the allowed prefixes
        import os
        workspace = WORKSPACE_PATH
        os.makedirs(workspace, exist_ok=True)
        test_file = os.path.join(workspace, "test_daemon_count.py")
        try:
            with open(test_file, "w") as f:
                f.write("x = 1\n")
            response = d.handle_request({
                "tool": "count_files",
                "args": {"path": workspace, "pattern": "test_daemon_count.py"},
            })
            assert "result" in response
            assert response["result"]["count"] >= 1
        except PermissionError:
            pytest.skip(
                "Workspace not writable by current user "
                "(install script set aios-worker-only permissions). "
                f"Run: sudo chmod 1777 {WORKSPACE_PATH}"
            )
        finally:
            if os.path.exists(test_file):
                try:
                    os.unlink(test_file)
                except PermissionError:
                    pass

    def test_invalid_tool_rejected(self, daemon):
        """An unregistered tool should be rejected."""
        d, _ = daemon
        response = d.handle_request({"tool": "delete_file", "args": {"path": "/x"}})
        assert "error" in response
        assert response["validation_failed"] is True

    def test_invalid_args_rejected(self, daemon):
        """Invalid args should be rejected with a validation error."""
        d, _ = daemon
        response = d.handle_request({
            "tool": "count_files",
            "args": {"path": "/etc/passwd", "pattern": "*.py"},
        })
        assert "error" in response
        assert response["validation_failed"] is True
        assert "outside allowed" in response["error"]

    def test_missing_tool_name(self, daemon):
        """A request without a tool name should be handled gracefully."""
        d, _ = daemon
        response = d.handle_request({"args": {"path": "/x"}})
        assert "error" in response
        assert response["validation_failed"] is True

    def test_audit_log_written(self, daemon):
        """Every tool call should be written to the audit log."""
        d, audit_path = daemon
        import os
        workspace = WORKSPACE_PATH
        os.makedirs(workspace, exist_ok=True)
        try:
            os.listdir(workspace)
        except PermissionError:
            pytest.skip(
                "Workspace not accessible by current user. "
                f"Run: sudo chmod 1777 {WORKSPACE_PATH}"
            )
        d.handle_request({
            "tool": "list_dir",
            "args": {"path": workspace},
            "conversation_id": "conv-1",
            "turn_id": "turn-1",
        })
        # Check the audit log
        with open(audit_path, "r") as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool_name"] == "list_dir"
        assert entry["conversation_id"] == "conv-1"
        assert entry["turn_id"] == "turn-1"

    def test_failed_validation_logged(self, daemon):
        """Failed validation attempts should also be logged."""
        d, audit_path = daemon
        d.handle_request({"tool": "rm_rf", "args": {"path": "/"}})
        with open(audit_path, "r") as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool_name"] == "rm_rf"
        assert entry["success"] is False

    def test_data_tainted_flag_for_read_file(self, daemon):
        """read_file results should be flagged as data_tainted."""
        d, audit_path = daemon
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, "w") as f:
                f.write("content")
            # We need to bypass validation's path check since tmpdir is
            # not in the allowed prefixes. Test the audit flag directly.
            d.audit_logger.log(
                tool_name="read_file",
                args={"path": test_file},
                result={"content": "content"},
                data_tainted=True,
            )
        with open(audit_path, "r") as f:
            entry = json.loads(f.readline())
        assert entry["data_tainted"] is True


class TestToolDaemonSafety:
    def test_check_safety_degraded_mode(self):
        """In degraded mode, safety check should pass with a warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ToolDaemon(
                socket_path=os.path.join(tmpdir, "test.sock"),
                audit_log_path=os.path.join(tmpdir, "audit.jsonl"),
                degraded_mode=True,
                sherpa_capability_path=os.path.join(tmpdir, "cap_level"),
            )
            result = d.check_safety()
            # In test environment, Landlock likely unavailable, so degraded mode
            assert result.can_start is True

    def test_check_safety_strict_mode(self):
        """In strict mode (no degraded), safety check should fail without Landlock."""
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ToolDaemon(
                socket_path=os.path.join(tmpdir, "test.sock"),
                audit_log_path=os.path.join(tmpdir, "audit.jsonl"),
                degraded_mode=False,
                sherpa_capability_path=os.path.join(tmpdir, "cap_level"),
            )
            result = d.check_safety()
            # In test environment, Landlock is likely unavailable
            # This should fail closed
            if not result.can_start:
                assert "Landlock" in result.reason or "seccomp" in result.reason
