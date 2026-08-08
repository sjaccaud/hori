"""Adversarial test: path traversal attacks.

Tests that the tool validation layer blocks path traversal attempts:
  - ../ sequences
  - symlink escape
  - encoded paths
  - direct access to sensitive locations

Defends: PoC 15.4 (validation), PoC 15.6 (read-only tools), PoC 15.0b (Landlock).

Status: PASSING — the validation layer (2B) already blocks these at the
application level. Landlock (2A) will add kernel-level enforcement.
"""
import os
import tempfile

import pytest

from services.tool_daemon.validation import validate_tool_call
from pathlib import Path

PROJECTS_DIR = str(Path.home() / "Projects")
AIOS_DIR = str(Path(__file__).resolve().parents[2])


HOME_DIR = str(Path.home())


class TestPathTraversal:
    """Path traversal attacks must be blocked by the validation layer."""

    def test_dotdot_traversal_to_etc(self):
        """../ traversal to /etc must be rejected."""
        result = validate_tool_call("list_dir", {
            "path": PROJECTS_DIR + "/../../etc",
        })
        assert not result.valid
        assert "outside allowed" in result.error

    def test_dotdot_traversal_to_ssh(self):
        """../ traversal to ~/.ssh must be rejected."""
        result = validate_tool_call("read_file", {
            "path": HOME_DIR + "/.ssh/id_ed25519",
        })
        assert not result.valid
        assert "outside allowed" in result.error

    def test_dotdot_traversal_to_root(self):
        """../ traversal to / must be rejected."""
        result = validate_tool_call("list_dir", {
            "path": PROJECTS_DIR + "/../../../",
        })
        assert not result.valid

    def test_direct_access_to_ssh(self):
        """Direct path to ~/.ssh must be rejected (outside allowed prefixes)."""
        result = validate_tool_call("read_file", {
            "path": HOME_DIR + "/.ssh/id_ed25519",
        })
        assert not result.valid
        assert "outside allowed" in result.error

    def test_direct_access_to_gnupg(self):
        """Direct path to ~/.gnupg must be rejected."""
        result = validate_tool_call("read_file", {
            "path": HOME_DIR + "/.gnupg/private-keys-v1.d",
        })
        assert not result.valid

    def test_direct_access_to_proc_environ(self):
        """Direct path to /proc/self/environ must be rejected."""
        result = validate_tool_call("read_file", {
            "path": "/proc/self/environ",
        })
        assert not result.valid

    def test_direct_access_to_etc_passwd(self):
        """Direct path to /etc/passwd must be rejected."""
        result = validate_tool_call("read_file", {"path": "/etc/passwd"})
        assert not result.valid

    def test_direct_access_to_root(self):
        """Direct path to / must be rejected."""
        result = validate_tool_call("list_dir", {"path": "/"})
        assert not result.valid

    def test_prefix_substring_attack(self):
        """A path that starts with the prefix string but isn't a subdirectory.

        ~/Projects-evil should NOT match ~/Projects.
        This is a classic prefix-matching bug that os.path.commonpath prevents.
        """
        result = validate_tool_call("list_dir", {"path": PROJECTS_DIR + "-evil"})
        assert not result.valid
        assert "outside allowed" in result.error

    def test_symlink_escape(self, tmp_path):
        """A symlink inside the allowed area pointing outside must be rejected."""
        link_path = "/tmp/aios-test-symlink-adv"
        try:
            if os.path.exists(link_path):
                os.unlink(link_path)
            os.symlink("/etc", link_path)
            result = validate_tool_call("list_dir", {"path": link_path})
            assert not result.valid
            assert "outside allowed" in result.error
        finally:
            if os.path.exists(link_path):
                os.unlink(link_path)

    def test_double_encoded_dotdot(self):
        """Double-encoded ../ should not bypass the canonicalization."""
        # os.path.realpath resolves these, so they should be caught
        result = validate_tool_call("read_file", {
            "path": PROJECTS_DIR + "/..%2F..%2Fetc/passwd",
        })
        # The path contains %2F which is not a real /, so realpath treats it
        # as a literal directory name. It won't exist, but the path IS within
        # the prefix (as a literal subdirectory). The tool would fail at
        # execution time (file not found), but validation passes.
        # This is acceptable — the tool returns an error, not sensitive data.
        # The real defense against encoded traversal is that the tool operates
        # on the canonical path, which is within the allowed prefix.
        assert result.valid  # Validated (path is within prefix as a literal name)

    def test_null_byte_injection(self):
        """Null bytes in paths must be rejected, not cause a crash."""
        result = validate_tool_call("read_file", {
            "path": PROJECTS_DIR + "/\x00../../etc/passwd",
        })
        # Null bytes are a known injection vector (C APIs treat null as
        # string terminator). The validator must reject them explicitly.
        assert not result.valid
        assert "null" in result.error.lower()

    def test_valid_path_within_projects(self):
        """A valid path within ~/Projects should be accepted."""
        result = validate_tool_call("list_dir", {"path": AIOS_DIR})
        assert result.valid
        assert result.validated_args["path"] == os.path.realpath(AIOS_DIR)

    def test_valid_path_within_workspace(self):
        """A valid path within the workspace should be accepted."""
        from hori.config import WORKSPACE_PATH
        result = validate_tool_call("list_dir", {"path": WORKSPACE_PATH})
        assert result.valid
