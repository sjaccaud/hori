"""Tests for PoC 15.2: Tool Allowlist Registry.

Verifies that the registry contains exactly the approved read-only tools,
that unregistered tools cannot be looked up, and that the prompt generation
produces the correct tool descriptions for the LLM.
"""
from services.tool_daemon import registry
from services.tool_daemon.registry import (
    get_tool,
    get_tool_prompt_block,
    get_tool_schemas_for_prompt,
    is_registered,
    list_tool_names,
    list_tools,
)
from services.tool_daemon.schema import SafetyLevel

from pathlib import Path
PROJECTS_DIR = str(Path.home() / "Projects")
AIOS_DIR = str(Path(__file__).resolve().parents[2])


def test_registry_has_four_read_only_tools():
    """The spine should have exactly 4 read-only filesystem tools."""
    names = list_tool_names()
    assert set(names) == {"list_dir", "read_file", "count_files", "search_files"}
    assert len(names) == 4


def test_all_tools_are_read_only():
    """Every tool in the spine must be READ_ONLY — no side-effects or destructive."""
    for tool in list_tools():
        assert tool.safety_level == SafetyLevel.READ_ONLY, (
            f"Tool '{tool.name}' has safety level {tool.safety_level}, "
            f"expected READ_ONLY"
        )


def test_get_tool_returns_schema_for_registered():
    """get_tool should return the schema for a registered tool."""
    tool = get_tool("count_files")
    assert tool is not None
    assert tool.name == "count_files"
    assert "pattern" in [p.name for p in tool.parameters]


def test_get_tool_returns_none_for_unregistered():
    """get_tool should return None for a tool not in the registry."""
    assert get_tool("delete_file") is None
    assert get_tool("execute_command") is None
    assert get_tool("write_file") is None
    assert get_tool("rm_rf") is None
    assert get_tool("") is None
    assert get_tool("nonexistent") is None


def test_is_registered():
    """is_registered should correctly identify registered and unregistered tools."""
    assert is_registered("list_dir") is True
    assert is_registered("read_file") is True
    assert is_registered("delete_file") is False
    assert is_registered("shell") is False


def test_tool_schemas_for_prompt():
    """get_tool_schemas_for_prompt should return all tools as dicts."""
    schemas = get_tool_schemas_for_prompt()
    assert len(schemas) == 4
    names = {s["name"] for s in schemas}
    assert names == {"list_dir", "read_file", "count_files", "search_files"}
    # Each schema should have the required fields
    for s in schemas:
        assert "name" in s
        assert "description" in s
        assert "parameters" in s
        assert "safety_level" in s


def test_tool_prompt_block_contains_all_tools():
    """The prompt block should mention all 4 tools and the JSON format."""
    block = get_tool_prompt_block()
    assert "list_dir" in block
    assert "read_file" in block
    assert "count_files" in block
    assert "search_files" in block
    assert "tool_call" in block
    assert "JSON" in block


def test_registry_is_static():
    """The registry should not allow dynamic registration of new tools at runtime.

    This is a design property: adding a tool requires a code change, not a
    runtime call. We verify by checking that the registry dict is populated
    at import time and has no public registration function.
    """
    # The _register function exists but is private (underscore prefix).
    # There is no public 'register' function.
    assert not hasattr(registry, "register")
    # The internal registry dict should have exactly 4 tools
    assert len(registry._REGISTRY) == 4


def test_all_tools_have_allowed_paths():
    """Every filesystem tool must declare allowed path prefixes."""
    for tool in list_tools():
        assert len(tool.allowed_paths) > 0, (
            f"Tool '{tool.name}' has no allowed_paths — this is a safety hole"
        )
        # The allowed paths should include the Projects directory
        assert PROJECTS_DIR in tool.allowed_paths
