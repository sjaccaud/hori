"""Unit tests for the MCP-over-SSE server in aios_core.

Traces to .devin/prd/remote-mcp-tools.md.
"""

import asyncio
import json
import os

import pytest
from fastapi import HTTPException
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


@pytest.fixture
def read_token(admin_token, monkeypatch):
    """A read-scoped MCP token alongside the admin token."""
    monkeypatch.setenv("AIOS_MCP_READ_TOKEN", "test-read-token")
    yield "test-read-token"


def test_resolve_scope_admin_read_and_rejects(read_token):
    """Token->scope resolution: admin full, read restricted, garbage denied."""
    from services.aios_core.mcp_server import _resolve_scope

    assert _resolve_scope("Bearer test-admin-token") == "admin"
    assert _resolve_scope("Bearer test-read-token") == "read"
    with pytest.raises(HTTPException) as exc:
        _resolve_scope("Bearer wrong-token")
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        _resolve_scope(None)
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        _resolve_scope("Basic dGVzdA==")
    assert exc.value.status_code == 401


def test_tools_list_scoped_excludes_writes(read_token):
    """tools/list under a read-scoped session must not offer write tools."""
    from services.aios_core.mcp_server import _handle_mcp_message, list_mcp_tools

    admin_resp = asyncio.run(_handle_mcp_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ))
    read_resp = asyncio.run(_handle_mcp_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        scope="read",
    ))
    admin_names = {t["name"] for t in admin_resp["result"]["tools"]}
    read_names = {t["name"] for t in read_resp["result"]["tools"]}
    assert len(admin_names) == len(list_mcp_tools())
    assert {"memory_store", "episode_append"} <= admin_names
    assert {"memory_store", "episode_append"}.isdisjoint(read_names)
    assert {"thread_read", "episode_search", "memory_search"} <= read_names


def test_tools_call_write_denied_under_read_scope(read_token, monkeypatch):
    """A read-scoped session cannot invoke write tools — the call is
    refused before dispatch, and admin scope is unaffected."""
    from services.aios_core import mcp_server
    from services.aios_core.mcp_server import _handle_mcp_message

    for tool in ("memory_store", "episode_append"):
        resp = asyncio.run(_handle_mcp_message(
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
             "params": {"name": tool, "arguments": {}}},
            scope="read",
        ))
        assert "error" in resp, f"{tool} not denied under read scope"
        assert tool in resp["error"]["message"]

    # Admin scope still dispatches writes (mock the store so nothing writes).
    called = []
    monkeypatch.setattr(mcp_server, "store_memory",
                        lambda **kw: called.append(kw) or "p1")
    resp = asyncio.run(_handle_mcp_message(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": "memory_store", "arguments": {
             "content": "x", "role": "user", "conversation_id": "c"}}},
        scope="admin",
    ))
    assert "result" in resp
    assert len(called) == 1


def test_session_scope_cannot_be_escalated(read_token):
    """A session created under a read token stays read-scoped even when a
    later POST presents the admin token — scope binds to the session."""
    from services.aios_core import mcp_server
    from services.aios_core.mcp_server import _handle_mcp_message

    session_id = "sess-read"
    mcp_server._mcp_sessions[session_id] = {
        "queue": asyncio.Queue(), "scope": "read"}
    try:
        session = mcp_server._mcp_sessions[session_id]
        resp = asyncio.run(_handle_mcp_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "episode_append", "arguments": {}}},
            scope=session["scope"],
        ))
        assert "error" in resp
    finally:
        mcp_server._mcp_sessions.pop(session_id, None)


def test_mcp_messages_enforces_session_scope(read_token):
    """End-to-end: POST /mcp/messages on a read session refuses write
    tools; an admin bearer on the same session does NOT escalate it."""
    from services.aios_core import mcp_server

    client = TestClient(app)
    mcp_server._mcp_sessions["s1"] = {
        "queue": asyncio.Queue(), "scope": "read"}
    try:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "episode_append", "arguments": {}}}
        r = client.post("/mcp/messages?session_id=s1",
                        headers={"Authorization": "Bearer test-read-token"},
                        json=body)
        assert r.status_code == 202
        msg = mcp_server._mcp_sessions["s1"]["queue"].get_nowait()
        assert "error" in msg

        r = client.post("/mcp/messages?session_id=s1",
                        headers={"Authorization": "Bearer test-admin-token"},
                        json=body)
        assert r.status_code == 202
        msg = mcp_server._mcp_sessions["s1"]["queue"].get_nowait()
        assert "error" in msg

        r = client.post("/mcp/messages?session_id=s1",
                        headers={"Authorization": "Bearer wrong-token"},
                        json=body)
        assert r.status_code == 403
    finally:
        mcp_server._mcp_sessions.pop("s1", None)


def test_fetch_url_blocks_non_public_targets(read_token):
    """SSRF guard: loopback/link-local/reserved/metadata endpoints must
    be refused before any request is made."""
    from services.aios_core.mcp_server import _fetch_url_blocked

    for url in (
        "http://127.0.0.1:6333/collections",
        "http://localhost:9090/metrics",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]:5680/admin",
        "http://10.0.0.4/internal",
        "http://192.168.1.1/router",
        "file:///etc/passwd",
        "gopher://127.0.0.1/x",
        "not-a-url",
        # Internal names refused before resolution — no DNS needed.
        "http://device.tail0000.ts.net:3443/",
        "http://printer.local/",
        "http://nas.lan/share",
    ):
        assert _fetch_url_blocked(url) is not None, url

    # A literal public IP needs no DNS and must pass.
    assert _fetch_url_blocked("http://93.184.216.34/") is None


def test_fetch_url_redirect_to_private_blocked(read_token, monkeypatch):
    """A public URL that redirects to a private target must be refused
    at the redirect hop — the private request is never sent."""
    from services.aios_core import mcp_server

    calls = []

    class FakeResp:
        def __init__(self, status=302, location=None, text=""):
            self.status_code = status
            self.headers = {"location": location} if location else {}
            self.text = text
            self.url = "http://93.184.216.34/"
            self.is_redirect = location is not None

        def raise_for_status(self):
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, timeout=None, follow_redirects=False):
            calls.append(url)
            return FakeResp(302, "http://127.0.0.1:5680/admin")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = asyncio.run(mcp_server._tool_fetch_url("http://93.184.216.34/"))
    assert result["ok"] is False
    assert "127.0.0.1" not in calls  # private hop never requested
    assert len(calls) == 1


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

    # fetch_url used to hit the live /health endpoint on 127.0.0.1:5680 —
    # the SSRF guard now refuses loopback targets, which is the contract.
    result = asyncio.run(call_mcp_tool("fetch_url", {"url": "http://127.0.0.1:5680/health"}))
    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert data["ok"] is False
    assert "non-public" in data["error"]


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


# ---------------------------------------------------------------------------
# Streamable-HTTP transport (/mcp/http) — native OWUI MCP client target.
# ---------------------------------------------------------------------------

READ_HDR = {"Authorization": "Bearer test-read-token"}
ADMIN_HDR = {"Authorization": "Bearer test-admin-token"}


def test_streamable_requires_auth(read_token):
    """No token -> 401, wrong token -> 403. Fail-closed like the SSE lane."""
    client = TestClient(app)
    r = client.post("/mcp/http", json={"jsonrpc": "2.0", "id": 1,
                                     "method": "ping", "params": {}})
    assert r.status_code == 401
    r = client.post("/mcp/http", json={"jsonrpc": "2.0", "id": 1,
                                       "method": "ping", "params": {}},
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


def test_streamable_initialize_and_list(read_token):
    """initialize handshake then tools/list under read scope — the exact
    sequence OWUI's streamablehttp_client performs."""
    client = TestClient(app)
    r = client.post("/mcp/http", json={
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26",
                   "capabilities": {},
                   "clientInfo": {"name": "open-webui", "version": "0"}},
    }, headers=READ_HDR)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["result"]["serverInfo"]["name"] == "aios-mcp"
    assert body["result"]["capabilities"]["tools"] == {}

    r = client.post("/mcp/http", json={"jsonrpc": "2.0", "id": 1,
                                       "method": "tools/list", "params": {}},
                    headers=READ_HDR)
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"thread_list", "thread_read", "episode_search",
            "memory_search"} <= names
    assert {"memory_store", "episode_append"}.isdisjoint(names)


def test_streamable_notification_returns_202(read_token):
    """notifications/initialized (no id) -> 202, empty body."""
    client = TestClient(app)
    r = client.post("/mcp/http", json={
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }, headers=READ_HDR)
    assert r.status_code == 202


def test_streamable_write_tool_denied_read_scope(read_token):
    """read scope cannot invoke write tools — refused before dispatch."""
    client = TestClient(app)
    r = client.post("/mcp/http", json={
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "memory_store",
                   "arguments": {"content": "x", "conversation_id": "c"}},
    }, headers=READ_HDR)
    assert r.status_code == 200
    assert "error" in r.json()


def test_streamable_admin_scope_gets_writes(admin_token):
    """admin token on the same endpoint sees the full catalog."""
    client = TestClient(app)
    r = client.post("/mcp/http", json={"jsonrpc": "2.0", "id": 1,
                                       "method": "tools/list", "params": {}},
                    headers=ADMIN_HDR)
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"memory_store", "episode_append"} <= names


def test_streamable_bad_json_400(read_token):
    client = TestClient(app)
    r = client.post("/mcp/http", content=b"{not json", headers=READ_HDR)
    assert r.status_code == 400


def test_streamable_get_method_not_allowed(read_token):
    """No session stream on the stateless lane — GET is refused."""
    client = TestClient(app)
    r = client.get("/mcp/http", headers=READ_HDR)
    assert r.status_code == 405


def test_streamable_unknown_notification_swallowed(read_token):
    """JSON-RPC: a notification (no id) never gets a response — not even
    for a method the server doesn't know. Found live by monkey test:
    notifications/cancelled produced a -32601 with id:null."""
    client = TestClient(app)
    r = client.post("/mcp/http", json={
        "jsonrpc": "2.0", "method": "notifications/cancelled",
        "params": {"requestId": 9},
    }, headers=READ_HDR)
    assert r.status_code == 202
    assert r.content in (b"", b"null")


def test_streamable_notification_only_batch_202(read_token):
    """A batch of pure notifications -> 202, empty body."""
    client = TestClient(app)
    r = client.post("/mcp/http", json=[
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled",
         "params": {"requestId": 1}},
    ], headers=READ_HDR)
    assert r.status_code == 202


def test_streamable_batch_non_dict_item_errored(read_token):
    """Batch members that aren't objects get -32600, not a 500."""
    client = TestClient(app)
    r = client.post("/mcp/http", json=[
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        42,
        "garbage",
    ], headers=READ_HDR)
    assert r.status_code == 200
    arr = r.json()
    assert len(arr) == 3
    assert arr[0]["result"] == {}
    assert arr[1]["error"]["code"] == -32600
    assert arr[2]["error"]["code"] == -32600


def test_streamable_garbage_object_invalid_request(read_token):
    """A JSON object with no method isn't a notification — -32600."""
    client = TestClient(app)
    r = client.post("/mcp/http", json={"foo": "bar"}, headers=READ_HDR)
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32600


def test_handle_message_notification_no_response():
    """Dispatcher level (shared with the SSE lane): unknown notification
    methods must return None — fire-and-forget, never an error frame."""
    from services.aios_core import mcp_server as ms
    assert asyncio.run(ms._handle_mcp_message(
        {"jsonrpc": "2.0", "method": "notifications/cancelled"}
    )) is None
    assert asyncio.run(ms._handle_mcp_message(
        {"jsonrpc": "2.0", "method": "bogus/unknown.method"}
    )) is None


def test_call_local_tool_surfaces_error_body(monkeypatch):
    """local-tools returns its reason (deny.conf citation, policy error)
    in the 422 body — the MCP wrapper must surface it, not a bare
    '422 Unprocessable Content' the model can't act on."""
    import httpx
    from services.aios_core import mcp_server as ms

    class _Resp:
        status_code = 422
        text = '{"error": "denied by deny.conf rule rig-deny-i2c"}'

        def json(self):
            return {"error": "denied by deny.conf rule rig-deny-i2c"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError("422", request=None, response=None)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client())
    result = asyncio.run(ms.call_mcp_tool("run_safe_command", {"command": "x"}))
    text = json.dumps(result.model_dump())
    assert result.is_error
    assert "deny.conf" in text
