"""Tests for PoC 15.1: Tool Schema Framework.

Verifies that tool schemas are correctly defined, serialized, and rendered
for the LLM prompt. These are the foundational data structures that the
registry (15.2), validation (15.4), and structured output (15.3) build on.
"""
from services.tool_daemon.schema import ParameterSchema, SafetyLevel, ToolSchema

from pathlib import Path
PROJECTS_DIR = str(Path.home() / "Projects")
AIOS_DIR = str(Path(__file__).resolve().parents[2])


def test_safety_level_enum():
    """SafetyLevel should have exactly three levels."""
    assert SafetyLevel.READ_ONLY.value == "read_only"
    assert SafetyLevel.SIDE_EFFECTS.value == "side_effects"
    assert SafetyLevel.DESTRUCTIVE.value == "destructive"


def test_parameter_schema_basic():
    """A basic required string parameter should serialize correctly."""
    param = ParameterSchema(
        name="path",
        type="string",
        description="The file path.",
        required=True,
    )
    d = param.to_dict()
    assert d["name"] == "path"
    assert d["type"] == "string"
    assert d["required"] is True
    # Optional fields should not be in the dict when not set
    assert "default" not in d
    assert "enum" not in d
    assert "min" not in d


def test_parameter_schema_with_constraints():
    """A parameter with enum, min, max should include them in the dict."""
    param = ParameterSchema(
        name="count",
        type="integer",
        description="Number of results.",
        required=False,
        default=10,
        min_value=1,
        max_value=100,
    )
    d = param.to_dict()
    assert d["default"] == 10
    assert d["min"] == 1
    assert d["max"] == 100


def test_tool_schema_required_params():
    """ToolSchema should correctly identify required parameters."""
    tool = ToolSchema(
        name="test_tool",
        description="A test tool.",
        parameters=[
            ParameterSchema(name="path", type="string", description="p", required=True),
            ParameterSchema(name="limit", type="integer", description="l", required=False, default=10),
        ],
    )
    assert tool.required_params == ["path"]
    assert "limit" not in tool.required_params


def test_tool_schema_to_dict():
    """ToolSchema.to_dict should produce a serializable dict."""
    tool = ToolSchema(
        name="count_files",
        description="Count files matching a pattern.",
        parameters=[
            ParameterSchema(name="path", type="string", description="Dir path.", required=True),
            ParameterSchema(name="pattern", type="string", description="Glob pattern.", required=True),
        ],
        safety_level=SafetyLevel.READ_ONLY,
        allowed_paths=[PROJECTS_DIR],
    )
    d = tool.to_dict()
    assert d["name"] == "count_files"
    assert d["safety_level"] == "read_only"
    assert d["allowed_paths"] == [PROJECTS_DIR]
    assert len(d["parameters"]) == 2
    assert d["parameters"][0]["name"] == "path"


def test_tool_schema_to_llm_prompt():
    """to_llm_prompt should produce a human-readable description for the LLM."""
    tool = ToolSchema(
        name="list_dir",
        description="List directory contents.",
        parameters=[
            ParameterSchema(name="path", type="string", description="Directory path.", required=True),
        ],
    )
    prompt = tool.to_llm_prompt()
    assert "list_dir" in prompt
    assert "List directory contents." in prompt
    assert "path" in prompt
    assert "[required]" in prompt
    assert "read_only" in prompt
