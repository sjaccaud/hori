"""Integration test: Tool-augmented voice chat (PoC 16.1).

Tests the full flow: user asks a question → LLM emits a tool call →
tool daemon executes it → result fed back to LLM → natural language
response to the user.

This test mocks the LLM (no real inference server needed) and uses a
real ToolDaemon in degraded mode (no Landlock/seccomp) to verify the
tool execution path end-to-end.

Defends: PoC 16.1 (Tool-Augmented Voice Chat).
"""
import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.tool_daemon.server import ToolDaemon
from services.tool_daemon.tool_client import ToolClient

from pathlib import Path
PROJECTS_DIR = str(Path.home() / "Projects")
AIOS_DIR = str(Path(__file__).resolve().parents[2])


@pytest.fixture
def running_daemon():
    """Start a real tool daemon in degraded mode on a temp socket."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")
        audit_path = os.path.join(tmpdir, "audit.jsonl")
        sherpa_path = os.path.join(tmpdir, "cap_level")
        daemon = ToolDaemon(
            socket_path=socket_path,
            audit_log_path=audit_path,
            degraded_mode=True,
            sherpa_capability_path=sherpa_path,
        )
        # Write a Level 0 capability file so the Sherpa check passes
        from services.tool_daemon.sherpa_interface import SherpaCapabilityFile
        cap = SherpaCapabilityFile(path=sherpa_path)
        cap.set_level(0)

        # Start the daemon in a background thread
        import threading
        server_thread = threading.Thread(target=daemon.start, daemon=True)
        server_thread.start()

        # Wait for the socket to appear
        import time
        for _ in range(50):
            if os.path.exists(socket_path):
                break
            time.sleep(0.1)
        else:
            pytest.fail("Tool daemon socket did not appear")

        yield socket_path, audit_path

        # Cleanup: the daemon thread is daemon=True, so it dies with the test


class TestToolAugmentedChat:
    """End-to-end: LLM tool call → tool daemon → result → LLM response."""

    def test_tool_client_can_connect(self, running_daemon):
        """The tool client can connect to a running tool daemon."""
        socket_path, _ = running_daemon
        client = ToolClient(socket_path=socket_path)
        assert client.is_available() is True

    def test_tool_client_calls_count_files(self, running_daemon):
        """The tool client can call count_files and get a real result."""
        socket_path, _ = running_daemon
        client = ToolClient(socket_path=socket_path)
        result = client.call_tool("count_files", {
            "path": AIOS_DIR,
            "pattern": "*.py",
        })
        assert "error" not in result
        assert "result" in result
        # The result should contain a count
        count = result["result"].get("count", 0)
        assert count > 0, f"Expected some .py files, got {count}"

    def test_tool_client_calls_list_dir(self, running_daemon):
        """The tool client can call list_dir and get directory entries."""
        socket_path, _ = running_daemon
        client = ToolClient(socket_path=socket_path)
        result = client.call_tool("list_dir", {
            "path": AIOS_DIR,
        })
        assert "error" not in result
        entries = result["result"].get("entries", [])
        # list_dir returns a list of dicts with "name" keys
        names = [e.get("name", "") for e in entries if isinstance(e, dict)]
        assert "services" in names or "docs" in names

    def test_tool_client_handles_invalid_tool(self, running_daemon):
        """The tool client properly returns errors for invalid tools."""
        socket_path, _ = running_daemon
        client = ToolClient(socket_path=socket_path)
        result = client.call_tool("delete_file", {"path": "/etc/passwd"})
        assert "error" in result
        assert "validation_failed" in result

    def test_tool_client_handles_unavailable_daemon(self):
        """The tool client handles a missing daemon gracefully."""
        client = ToolClient(socket_path="/tmp/nonexistent-daemon.sock")
        assert client.is_available() is False
        result = client.call_tool("count_files", {"path": "/tmp", "pattern": "*"})
        assert "error" in result
        assert result.get("daemon_unavailable") is True

    def test_full_tool_flow_with_mocked_llm(self, running_daemon):
        """Full flow: mocked LLM emits a tool call → tool daemon executes
        it → result fed back to LLM → second LLM call produces natural text.

        This tests the _maybe_call_tool helper in aios_core/main.py.
        """
        socket_path, _ = running_daemon

        # Mock the LLM: _maybe_call_tool calls _call_llm_with_messages once
        # (for the second response, after the tool result is fed back).
        # The first "response" (the tool call) is passed as llm_response param.
        async def mock_llm(system_prompt, messages):
            return "There are 42 Python files in the AIOS project."

        # Patch both the tool client (to use our test socket) and the
        # LLM call function (to avoid needing a real inference server)
        with patch("services.aios_core.main.get_tool_client") as mock_get_client, \
             patch("services.aios_core.main._call_llm_with_messages", new=mock_llm):
            mock_client = ToolClient(socket_path=socket_path)
            mock_get_client.return_value = mock_client
            mock_client.is_available = lambda: True

            from services.aios_core.main import _maybe_call_tool, OAIMessage

            messages = [OAIMessage(role="user", content="How many Python files do I have?")]

            tool_call_response = (
                '{"tool_call": {"name": "count_files", "args": '
                f'{{"path": "{AIOS_DIR}", "pattern": "*.py"}}}}}}'
            )
            result = asyncio.run(
                _maybe_call_tool(
                    llm_response=tool_call_response,
                    system_prompt="test prompt",
                    messages=messages,
                    conv_id="test-conv",
                )
            )

            final_text, tool_was_called = result

            assert tool_was_called is True
            assert "42" in final_text

    def test_no_tool_call_returns_original(self, running_daemon):
        """If the LLM doesn't emit a tool call, the original response is
        returned unchanged and tool_was_called is False."""
        socket_path, _ = running_daemon

        with patch("services.aios_core.main.get_tool_client") as mock_get_client:
            mock_client = ToolClient(socket_path=socket_path)
            mock_get_client.return_value = mock_client

            from services.aios_core.main import _maybe_call_tool, OAIMessage

            messages = [OAIMessage(role="user", content="What's the weather?")]

            result = asyncio.run(
                _maybe_call_tool(
                    llm_response="I don't know the weather. I can't access the internet.",
                    system_prompt="test prompt",
                    messages=messages,
                )
            )

            final_text, tool_was_called = result
            assert tool_was_called is False
            assert "weather" in final_text
