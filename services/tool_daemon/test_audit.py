"""Tests for PoC 15.9: Audit Log (with Permission Separation).

Verifies that the audit logger correctly records tool calls, sanitizes
large data, and supports append-only operation.
"""
import json
import os
import tempfile

import pytest

from services.tool_daemon.audit import AuditEntry, AuditLogger

from pathlib import Path
PROJECTS_DIR = str(Path.home() / "Projects")
AIOS_DIR = str(Path(__file__).resolve().parents[2])



@pytest.fixture
def audit_logger():
    """Create a temporary audit logger."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "tool_audit.jsonl")
        logger = AuditLogger(log_path)
        yield logger, log_path


class TestAuditEntry:
    def test_to_jsonl(self):
        """AuditEntry should serialize to a single JSON line."""
        entry = AuditEntry(
            timestamp=1234567890.0,
            tool_name="count_files",
            args={"path": PROJECTS_DIR, "pattern": "*.py"},
            result={"count": 5, "sample": []},
            success=True,
        )
        line = entry.to_jsonl()
        parsed = json.loads(line)
        assert parsed["tool_name"] == "count_files"
        assert parsed["success"] is True
        assert parsed["args"]["pattern"] == "*.py"
        assert "\n" not in line  # Must be a single line

    def test_to_jsonl_with_optional_fields(self):
        """AuditEntry should include optional fields when set."""
        entry = AuditEntry(
            timestamp=1234567890.0,
            tool_name="read_file",
            args={"path": AIOS_DIR + "/README.md"},
            result={"content": "file content"},
            success=True,
            llm_reasoning="User asked to read the README",
            data_tainted=True,
            conversation_id="conv-123",
            turn_id="turn-1",
        )
        parsed = json.loads(entry.to_jsonl())
        assert parsed["llm_reasoning"] == "User asked to read the README"
        assert parsed["data_tainted"] is True
        assert parsed["conversation_id"] == "conv-123"


class TestAuditLogger:
    def test_log_creates_file(self, audit_logger):
        """Logging should create the log file if it doesn't exist."""
        logger, log_path = audit_logger
        assert os.path.exists(log_path)

    def test_log_appends_entries(self, audit_logger):
        """Multiple log calls should append to the file."""
        logger, log_path = audit_logger
        logger.log("list_dir", {"path": "/home"}, {"entries": []})
        logger.log("count_files", {"path": "/home", "pattern": "*.py"}, {"count": 3})
        entries = logger.read_entries()
        assert len(entries) == 2
        assert entries[0]["tool_name"] == "list_dir"
        assert entries[1]["tool_name"] == "count_files"

    def test_log_records_success(self, audit_logger):
        """Successful tool calls should have success=True."""
        logger, _ = audit_logger
        logger.log("count_files", {"path": "/x", "pattern": "*.py"}, {"count": 5})
        entries = logger.read_entries()
        assert entries[0]["success"] is True

    def test_log_records_failure(self, audit_logger):
        """Failed tool calls should have success=False."""
        logger, _ = audit_logger
        logger.log("read_file", {"path": "/x"}, {"error": "File not found"})
        entries = logger.read_entries()
        assert entries[0]["success"] is False

    def test_log_sanitizes_large_content(self, audit_logger):
        """Large file content in results should be truncated in the log."""
        logger, _ = audit_logger
        large_content = "A" * 10000
        logger.log("read_file", {"path": "/x"}, {"content": large_content})
        entries = logger.read_entries()
        # The content should be truncated to 500 chars + truncation marker
        assert len(entries[0]["result"]["content"]) < 600
        assert "truncated" in entries[0]["result"]["content"]

    def test_log_sanitizes_large_args(self, audit_logger):
        """Large string args should be truncated in the log."""
        logger, _ = audit_logger
        large_arg = "B" * 5000
        logger.log("search_files", {"path": large_arg, "pattern": "*.py"}, {"results": []})
        entries = logger.read_entries()
        assert len(entries[0]["args"]["path"]) < 1100
        assert "truncated" in entries[0]["args"]["path"]

    def test_log_sanitizes_large_lists(self, audit_logger):
        """Large lists in results should be truncated in the log."""
        logger, _ = audit_logger
        large_list = [f"file_{i}.py" for i in range(100)]
        logger.log("search_files", {"path": "/x", "pattern": "*.py"}, {"results": large_list})
        entries = logger.read_entries()
        # The list should be truncated
        assert len(entries[0]["result"]["results"]) <= 55  # 50 + truncation marker

    def test_get_entry_count(self, audit_logger):
        """get_entry_count should return the number of entries."""
        logger, _ = audit_logger
        assert logger.get_entry_count() == 0
        logger.log("list_dir", {"path": "/x"}, {"entries": []})
        logger.log("count_files", {"path": "/x", "pattern": "*.py"}, {"count": 1})
        assert logger.get_entry_count() == 2

    def test_read_entries_empty_file(self, audit_logger):
        """read_entries on an empty file should return an empty list."""
        logger, _ = audit_logger
        assert logger.read_entries() == []

    def test_read_entries_skips_malformed(self, audit_logger):
        """read_entries should skip malformed JSON lines."""
        logger, log_path = audit_logger
        logger.log("list_dir", {"path": "/x"}, {"entries": []})
        # Append a malformed line
        with open(log_path, "a") as f:
            f.write("not valid json\n")
        logger.log("count_files", {"path": "/x", "pattern": "*.py"}, {"count": 1})
        entries = logger.read_entries()
        assert len(entries) == 2  # Skipped the malformed line

    def test_append_only_mode(self, audit_logger):
        """The log should be append-only — existing entries are not modified."""
        logger, log_path = audit_logger
        logger.log("list_dir", {"path": "/a"}, {"entries": []})
        logger.log("list_dir", {"path": "/b"}, {"entries": []})

        # Read the file content
        with open(log_path, "r") as f:
            content_after_two = f.read()

        # Add a third entry
        logger.log("list_dir", {"path": "/c"}, {"entries": []})

        with open(log_path, "r") as f:
            content_after_three = f.read()

        # The first two entries should still be there, unchanged
        assert content_after_two in content_after_three
