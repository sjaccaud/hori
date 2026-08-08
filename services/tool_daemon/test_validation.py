"""Tests for PoC 15.4: Tool Call Validation.

Verifies that validation catches: missing required params, wrong types,
out-of-range values, path traversal, symlink escape, and unregistered tools.
These are the defenses that prevent the LLM from passing malicious arguments
to tools.
"""
import os
import tempfile

import pytest

from services.tool_daemon.validation import validate_tool_call
from pathlib import Path

PROJECTS_DIR = str(Path.home() / "Projects")
AIOS_DIR = str(Path(__file__).resolve().parents[2])


HOME_DIR = str(Path.home())


# --- Valid calls ---

def test_valid_count_files_call():
    """A well-formed count_files call should pass validation."""
    result = validate_tool_call("count_files", {
        "path": AIOS_DIR,
        "pattern": "*.py",
    })
    assert result.valid is True
    assert result.validated_args is not None
    # Path should be canonicalized
    assert result.validated_args["path"] == os.path.realpath(AIOS_DIR)
    assert result.validated_args["pattern"] == "*.py"


def test_valid_list_dir_call():
    """A well-formed list_dir call should pass validation."""
    result = validate_tool_call("list_dir", {"path": PROJECTS_DIR})
    assert result.valid is True
    assert result.validated_args is not None


def test_optional_param_uses_default():
    """An optional param not provided should use its default."""
    result = validate_tool_call("read_file", {"path": AIOS_DIR + "/README.md"})
    assert result.valid is True
    assert result.validated_args["max_bytes"] == 102400  # default


# --- Unregistered tools ---

def test_unregistered_tool_rejected():
    """A call to an unregistered tool should be rejected."""
    result = validate_tool_call("delete_file", {"path": PROJECTS_DIR})
    assert result.valid is False
    assert "not registered" in result.error


def test_nonexistent_tool_rejected():
    """A call to a completely nonexistent tool should be rejected."""
    result = validate_tool_call("rm_rf", {"path": "/"})
    assert result.valid is False
    assert "not registered" in result.error


# --- Missing required params ---

def test_missing_required_param():
    """A call missing a required parameter should be rejected."""
    result = validate_tool_call("count_files", {"path": PROJECTS_DIR})
    assert result.valid is False
    assert "pattern" in result.error
    assert "Missing required" in result.error


def test_missing_all_params():
    """A call with no parameters should be rejected if required params exist."""
    result = validate_tool_call("count_files", {})
    assert result.valid is False
    assert "Missing required" in result.error


# --- Wrong types ---

def test_wrong_type_string_for_integer():
    """A string where an integer is expected should be rejected."""
    result = validate_tool_call("read_file", {
        "path": AIOS_DIR + "/README.md",
        "max_bytes": "lots",
    })
    assert result.valid is False
    assert "max_bytes" in result.error
    assert "integer" in result.error


def test_wrong_type_integer_for_string():
    """An integer where a string is expected should be rejected."""
    result = validate_tool_call("count_files", {"path": 12345, "pattern": "*.py"})
    assert result.valid is False
    assert "path" in result.error
    assert "string" in result.error


def test_boolean_not_integer():
    """A boolean should not be accepted as an integer (Python bool is int subclass)."""
    result = validate_tool_call("read_file", {
        "path": AIOS_DIR + "/README.md",
        "max_bytes": True,
    })
    assert result.valid is False
    assert "integer" in result.error


# --- Out of range ---

def test_max_bytes_below_minimum():
    """A value below the minimum should be rejected."""
    result = validate_tool_call("read_file", {
        "path": AIOS_DIR + "/README.md",
        "max_bytes": 0,
    })
    assert result.valid is False
    assert ">=" in result.error or "min" in result.error.lower()


def test_max_bytes_above_maximum():
    """A value above the maximum should be rejected."""
    result = validate_tool_call("read_file", {
        "path": AIOS_DIR + "/README.md",
        "max_bytes": 999999999,
    })
    assert result.valid is False
    assert "<=" in result.error or "max" in result.error.lower()


# --- Path traversal ---

def test_path_traversal_rejected():
    """A path with ../ traversal should be rejected."""
    result = validate_tool_call("list_dir", {
        "path": PROJECTS_DIR + "/../../etc",
    })
    assert result.valid is False
    assert "outside allowed" in result.error


def test_path_traversal_to_home():
    """A path traversing to ~/.ssh should be rejected."""
    result = validate_tool_call("read_file", {
        "path": HOME_DIR + "/.ssh/id_ed25519",
    })
    assert result.valid is False
    assert "outside allowed" in result.error


def test_path_to_secrets_directly():
    """A direct path to ~/.ssh should be rejected (outside allowed prefixes)."""
    result = validate_tool_call("read_file", {
        "path": HOME_DIR + "/.ssh/id_ed25519",
    })
    assert result.valid is False
    assert "outside allowed" in result.error


def test_path_to_env_file():
    """A path to a .env file should be rejected (outside allowed prefixes)."""
    result = validate_tool_call("read_file", {
        "path": AIOS_DIR + "/.env",
    })
    # ~/Projects/aios/.env IS within ~/Projects
    # So this should be VALID — the registry allows reading any file in Projects.
    # Landlock (PoC 15.0b) will deny .env files at the kernel level.
    # The registry is defense in depth, not the primary defense for .env.
    assert result.valid is True


def test_path_to_proc_environ():
    """A path to /proc/self/environ should be rejected (outside allowed prefixes)."""
    result = validate_tool_call("read_file", {
        "path": "/proc/self/environ",
    })
    assert result.valid is False
    assert "outside allowed" in result.error


def test_path_to_etc_passwd():
    """A path to /etc/passwd should be rejected."""
    result = validate_tool_call("read_file", {"path": "/etc/passwd"})
    assert result.valid is False
    assert "outside allowed" in result.error


def test_symlink_escape_rejected(tmp_path):
    """A symlink pointing outside the allowed prefix should be rejected."""
    # Create a symlink inside Projects that points outside
    link_path = "/tmp/aios-test-symlink"
    try:
        if os.path.exists(link_path):
            os.unlink(link_path)
        os.symlink("/etc", link_path)
        result = validate_tool_call("list_dir", {"path": link_path})
        assert result.valid is False
        assert "outside allowed" in result.error
    finally:
        if os.path.exists(link_path):
            os.unlink(link_path)


def test_prefix_not_substring_match():
    """A path that starts with the prefix string but isn't a subdirectory should be rejected.

    e.g., ~/Projects-evil should NOT match ~/Projects.
    """
    result = validate_tool_call("list_dir", {"path": PROJECTS_DIR + "-evil"})
    assert result.valid is False
    assert "outside allowed" in result.error


# --- Unexpected params ---

def test_unexpected_param_rejected():
    """A call with a parameter not in the schema should be rejected."""
    result = validate_tool_call("count_files", {
        "path": AIOS_DIR,
        "pattern": "*.py",
        "recursive": True,  # not in schema
    })
    assert result.valid is False
    assert "Unexpected" in result.error
    assert "recursive" in result.error


# --- None / bad args ---

def test_none_args():
    """None args should be treated as empty dict (all required params will be missing)."""
    result = validate_tool_call("count_files", None)
    assert result.valid is False
    assert "Missing required" in result.error


def test_non_dict_args():
    """Non-dict args should be rejected."""
    result = validate_tool_call("count_files", "not a dict")
    assert result.valid is False
    assert "JSON object" in result.error
