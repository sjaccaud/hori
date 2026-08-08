"""Tests for PoC 15.3: Structured Output Enforcement.

Verifies that the parser correctly extracts tool calls from LLM responses
in various formats (plain JSON, code-fenced, embedded in text) and rejects
malformed or non-tool-call responses.
"""
from services.tool_daemon.output_parser import parse_tool_call, strip_tool_call_artifacts
from pathlib import Path

PROJECTS_DIR = str(Path.home() / "Projects")
AIOS_DIR = str(Path(__file__).resolve().parents[2])


HOME_DIR = str(Path.home())


# --- Valid tool calls ---

def test_plain_json_tool_call():
    """A plain JSON tool call should be parsed correctly."""
    response = f'{{"tool_call": {{"name": "count_files", "args": {{"path": "{PROJECTS_DIR}", "pattern": "*.py"}}}}}}'
    result = parse_tool_call(response)
    assert result.found is True
    assert result.name == "count_files"
    assert result.args == {"path": PROJECTS_DIR, "pattern": "*.py"}


def test_code_fenced_tool_call():
    """A tool call wrapped in markdown code fences should be parsed."""
    response = f'```json\n{{"tool_call": {{"name": "list_dir", "args": {{"path": "{PROJECTS_DIR}"}}}}}}\n```'
    result = parse_tool_call(response)
    assert result.found is True
    assert result.name == "list_dir"
    assert result.args == {"path": PROJECTS_DIR}


def test_tool_call_embedded_in_text():
    """A tool call embedded in surrounding text should be extracted."""
    response = (
        "Let me check that for you.\n\n"
        f'{{"tool_call": {{"name": "count_files", "args": {{"path": "{AIOS_DIR}", "pattern": "*.midi"}}}}}}\n\n'
        "I'll get back to you with the results."
    )
    result = parse_tool_call(response)
    assert result.found is True
    assert result.name == "count_files"
    assert result.args["pattern"] == "*.midi"


def test_tool_call_with_no_args():
    """A tool call with no args key should default to empty dict."""
    response = '{"tool_call": {"name": "list_dir"}}'
    result = parse_tool_call(response)
    assert result.found is True
    assert result.name == "list_dir"
    assert result.args == {}


def test_tool_call_with_empty_args():
    """A tool call with empty args should be parsed."""
    response = '{"tool_call": {"name": "list_dir", "args": {}}}'
    result = parse_tool_call(response)
    assert result.found is True
    assert result.name == "list_dir"
    assert result.args == {}


# --- Non-tool-call responses ---

def test_normal_text_response():
    """A normal text response without tool_call should return found=False."""
    response = "I cannot access the filesystem. I don't have tools available."
    result = parse_tool_call(response)
    assert result.found is False


def test_empty_response():
    """An empty response should return found=False."""
    assert parse_tool_call("").found is False
    assert parse_tool_call("   ").found is False


def test_none_response():
    """A None response should return found=False."""
    assert parse_tool_call(None).found is False  # type: ignore


def test_text_mentions_tool_call_but_no_json():
    """Text that mentions 'tool_call' but has no JSON should return found=False."""
    response = "I would make a tool_call but I don't know how."
    result = parse_tool_call(response)
    assert result.found is False


def test_hallucinated_result_not_parsed():
    """A hallucinated result ('I found 847 files') should NOT be parsed as a tool call."""
    response = "I found 847 MIDI files in your Projects directory."
    result = parse_tool_call(response)
    assert result.found is False


# --- Malformed tool calls ---

def test_missing_name():
    """A tool call without a 'name' field should return found=False."""
    response = f'{{"tool_call": {{"args": {{"path": "{PROJECTS_DIR}"}}}}}}'
    result = parse_tool_call(response)
    assert result.found is False


def test_name_not_string():
    """A tool call where name is not a string should return found=False."""
    response = '{"tool_call": {"name": 123, "args": {}}}'
    result = parse_tool_call(response)
    assert result.found is False


def test_args_not_dict():
    """A tool call where args is not a dict should return found=False."""
    response = '{"tool_call": {"name": "list_dir", "args": "not a dict"}}'
    result = parse_tool_call(response)
    assert result.found is False


def test_malformed_json():
    """Malformed JSON should return found=False, not crash."""
    response = '{"tool_call": {"name": "list_dir", "args": {"path": "'
    result = parse_tool_call(response)
    assert result.found is False


def test_tool_call_not_in_json():
    """The string 'tool_call' outside a JSON object should not trigger parsing."""
    response = 'tool_call count_files with path ~/Projects'
    result = parse_tool_call(response)
    assert result.found is False


# --- strip_tool_call_artifacts ---

def test_strip_canonical_tool_call():
    """A canonical {"tool_call": {...}} object should be stripped."""
    text = '{"tool_call": {"name": "count_files", "args": {"path": "/tmp"}}}'
    result = strip_tool_call_artifacts(text)
    assert "tool_call" not in result
    assert "count_files" not in result
    assert result == ""


def test_strip_markdown_wrapper():
    """A [tool_call ...] markdown wrapper should be stripped."""
    text = '[tool_call {"name": "list_dir", "path": "/home"}]'
    result = strip_tool_call_artifacts(text)
    assert "tool_call" not in result
    assert "list_dir" not in result


def test_strip_bare_name_args():
    """A bare {"name": "...", "args": {...}} object should be stripped."""
    text = 'Here is the result. {"name": "count_files", "args": {"path": "/tmp"}}'
    result = strip_tool_call_artifacts(text)
    assert "count_files" not in result
    assert "Here is the result." in result


def test_strip_bare_name_path():
    """A bare {"name": "...", "path": "..."} object should be stripped."""
    text = f'{{"name": "list_dir", "path": "{HOME_DIR}"}}'
    result = strip_tool_call_artifacts(text)
    assert "list_dir" not in result


def test_strip_preserves_normal_text():
    """Normal text without tool-call artifacts should be unchanged."""
    text = "There are 42 Python files in the project."
    result = strip_tool_call_artifacts(text)
    assert result == text


def test_strip_preserves_json_without_name():
    """JSON objects without a 'name' key should NOT be stripped."""
    text = 'The result is {"count": 42, "path": "/tmp"}'
    result = strip_tool_call_artifacts(text)
    assert result == text


def test_strip_empty_string():
    """Empty string input should return empty string."""
    assert strip_tool_call_artifacts("") == ""


def test_strip_none_safe():
    """None input should not crash (returns None)."""
    assert strip_tool_call_artifacts(None) is None


def test_strip_multiple_artifacts():
    """Multiple tool-call artifacts in one response should all be stripped."""
    text = (
        'Let me check. {"tool_call": {"name": "count_files", "args": {}}}'
        ' and also {"name": "list_dir", "path": "/home"}'
    )
    result = strip_tool_call_artifacts(text)
    assert "count_files" not in result
    assert "list_dir" not in result
    assert "Let me check." in result


def test_strip_cleans_extra_whitespace():
    """Stripping should clean up excessive newlines left behind."""
    text = '{"tool_call": {"name": "count_files", "args": {}}}\n\n\n\nDone.'
    result = strip_tool_call_artifacts(text)
    assert result == "Done."
