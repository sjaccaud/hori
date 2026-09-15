"""Unit tests for the MCP-over-SSE server in aios_core.

Traces to .devin/prd/remote-mcp-tools.md.
"""

import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

from services.aios_core.main import app
from services.aios_core.mcp_server import list_mcp_tools


@pytest.fixture
def admin_token():
    old = os.environ.get("AIOS_ADMIN_TOKEN")
    os.environ["AIOS_ADMIN_TOKEN"] = "test-admin-token"
    yield
    if old is None:
        os.environ.pop("AIOS_ADMIN_TOKEN", None)
    else:
        os.environ["AIOS_ADMIN_TOKEN"] = old


def test_list_mcp_tools_returns_core_tools(admin_token):
    """list_mcp_tools() must expose the tools in the interface contract."""
    tools = list_mcp_tools()
    names = [t.name for t in tools]
    expected = [
        "get_status",
        "read_file",
        "run_safe_command",
        "search_web",
        "fetch_url",
        "memory_search",
        "memory_store",
        "thread_list",
        "thread_read",
        "episode_append",
        "episode_search",
    ]
    assert names == expected


def test_episode_tools_round_trip(admin_token, tmp_path, monkeypatch):
    """episode_append then thread_read/thread_list over call_mcp_tool."""
    import services.aios_core.episodes as episodes
    from services.aios_core.mcp_server import call_mcp_tool

    root = tmp_path / "threads"
    tdir = root / "system-memory"
    (tdir / "episodes").mkdir(parents=True)
    (tdir / "state.md").write_text("---\nstatus: active\n---\n\n# s\n")
    monkeypatch.setattr(episodes, "THREADS_ROOT", root)

    res = asyncio.run(call_mcp_tool("episode_append", {
        "thread": "system-memory",
        "title": "MCP round trip",
        "summary": "written via tools/call",
        "author": "test-mcp",
        "open_issues": ["none"],
    }))
    assert res.is_error is False
    data = json.loads(res.content[0].text)
    assert data["ok"] is True

    res = asyncio.run(call_mcp_tool("thread_read", {"name": "system-memory"}))
    data = json.loads(res.content[0].text)
    assert data["episodes"][0]["frontmatter"]["author"] == "test-mcp"

    res = asyncio.run(call_mcp_tool("thread_list", {}))
    data = json.loads(res.content[0].text)
    assert data[0]["name"] == "system-memory"
    assert data[0]["episode_count"] == 1

    res = asyncio.run(call_mcp_tool("episode_search", {"query": "round trip"}))
    data = json.loads(res.content[0].text)
    assert len(data) == 1


def test_episode_append_rejects_bad_thread(admin_token, tmp_path, monkeypatch):
    """A traversal thread name over MCP returns is_error, writes nothing."""
    import services.aios_core.episodes as episodes
    from services.aios_core.mcp_server import call_mcp_tool

    root = tmp_path / "threads"
    root.mkdir()
    monkeypatch.setattr(episodes, "THREADS_ROOT", root)

    res = asyncio.run(call_mcp_tool("episode_append", {
        "thread": "../escape",
        "title": "t",
        "summary": "s",
        "author": "evil",
    }))
    assert res.is_error is True
    assert list(root.iterdir()) == []


def test_call_tool_get_status_returns_system_state(admin_token):
    """call_mcp_tool('get_status') must return the current system state snapshot."""
    from services.aios_core.mcp_server import call_mcp_tool

    result = asyncio.run(call_mcp_tool("get_status", {}))
    assert result.is_error is False
    text = result.content[0].text
    data = json.loads(text)
    assert data["status"] in ("healthy", "degraded", "unknown")
    assert "services" in data


def test_mcp_event_stream_yields_endpoint_event(admin_token):
    """_mcp_event_stream must yield an 'endpoint' event with a session id."""
    import services.aios_core.mcp_server as mcp_server
    from services.aios_core.mcp_server import _mcp_event_stream

    queue = asyncio.Queue()
    session_id = "test-session-001"
    mcp_server._mcp_sessions[session_id] = queue
    try:
        gen = _mcp_event_stream(session_id, queue)
        first = asyncio.run(gen.__anext__())
        assert first["event"] == "endpoint"
        assert "session_id=" in first["data"]
    finally:
        mcp_server._mcp_sessions.pop(session_id, None)


def test_handle_mcp_message_tools_list(admin_token):
    """_handle_mcp_message must respond to a tools/list JSON-RPC request."""
    from services.aios_core.mcp_server import _handle_mcp_message, list_mcp_tools

    response = asyncio.run(_handle_mcp_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert "result" in response
    assert len(response["result"]["tools"]) == len(list_mcp_tools())


def test_call_tool_read_file_and_run_command(admin_token, tmp_path):
    """read_file and run_safe_command delegate to the local-tools server."""
    from services.aios_core.mcp_server import call_mcp_tool

    file = tmp_path / "mcp_test.txt"
    file.write_text("hello mcp tools")

    result = asyncio.run(call_mcp_tool("read_file", {"path": str(file)}))
    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert data.get("ok") is True
    assert "hello mcp tools" in data.get("content", "")

    result = asyncio.run(call_mcp_tool("run_safe_command", {"command": "uname", "args": ["-r"]}))
    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert data.get("ok") is True
    assert data.get("stdout")


def test_call_tool_search_web_and_fetch_url(admin_token, monkeypatch):
    """search_web returns a capped result list; fetch_url returns page text."""
    from services.aios_core import multi_search
    from services.aios_core.mcp_server import call_mcp_tool

    async def fake_multi_search(query, max_results=5, enrich=False):
        return [
            multi_search.MultiSearchResult(
                title="SearXNG test",
                url="http://example.com",
                snippet="result",
                source="searxng",
                score=1.0,
            )
        ]

    monkeypatch.setattr("services.aios_core.mcp_server.multi_search", fake_multi_search)

    result = asyncio.run(call_mcp_tool("search_web", {"query": "test", "max_results": 1}))
    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert len(data) == 1
    assert data[0]["title"] == "SearXNG test"

    # fetch_url hits the live aios_core /health endpoint on 127.0.0.1:5680.
    result = asyncio.run(call_mcp_tool("fetch_url", {"url": "http://127.0.0.1:5680/health"}))
    assert result.is_error is False
    text = result.content[0].text
    assert "status" in text


def test_call_tool_memory_search_and_store(admin_token, monkeypatch):
    """memory_search and memory_store use the aios_core memory backend."""
    from services.aios_core import memory
    from services.aios_core.mcp_server import call_mcp_tool

    stored = []

    def fake_store_memory(*, content, role, conversation_id, tier, **kwargs):
        point_id = "point-123"
        stored.append({"content": content, "role": role, "conversation_id": conversation_id, "tier": tier})
        return point_id

    def fake_retrieve_memory(query, tier="project", limit=5, score_threshold=0.6):
        return [
            {"content": "prior memory", "role": "user", "tier": tier, "conversation_id": "conv-123"}
        ]

    monkeypatch.setattr("services.aios_core.mcp_server.store_memory", fake_store_memory)
    monkeypatch.setattr("services.aios_core.mcp_server.retrieve_memory", fake_retrieve_memory)

    result = asyncio.run(call_mcp_tool("memory_store", {
        "content": "remember this",
        "role": "user",
        "conversation_id": "conv-abc",
        "tier": "working",
    }))
    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert data["id"] == "point-123"
    assert len(stored) == 1

    result = asyncio.run(call_mcp_tool("memory_search", {
        "query": "remember",
        "tier": "working",
        "limit": 1,
    }))
    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert len(data) == 1
    assert data[0]["content"] == "prior memory"
