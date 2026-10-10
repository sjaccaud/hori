"""MCP-over-SSE server for remote Jacko tools.

Exposes a small, admin-token-gated MCP surface on aios_core so
Tailnet clients (Cline/Claude on a MacBook, scripts on a phone) can call
Jacko tools without public internet exposure.

Traces to:
  .devin/prd/remote-mcp-tools.md
  docs/manifesto.md Pillar II (Distributed Mesh) and Pillar IV (Remote Interaction)

SECURITY:
  - Tailnet-only: the endpoint is not exposed via Funnel.
  - Scoped bearer tokens, fail-closed when none are configured:
    the admin token (also used for /admin/api) gets all tools; the
    read-scoped token (AIOS_MCP_READ_TOKEN / hori.yaml mcp.read_token)
    authenticates for MCP only and cannot invoke write tools
    (memory_store, episode_append). Scope binds to the SSE session at
    connect time.
  - `read_file` and `run_safe_command` are delegated to the existing
    local-tools server (127.0.0.1:8766), inheriting its allowlists and
    command filtering.
  - `search_web` and `fetch_url` make external network calls but are
    read-only and capped.
  - `memory_search` and `memory_store` use the aios_core memory module
    (Qdrant/SQLite) and write to memory tiers.
"""

import asyncio
import contextlib
import hmac
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Request
from sse_starlette.sse import EventSourceResponse
from starlette.responses import Response

import mcp.types as mcp_types

from . import episodes as episode_store
from .memory import retrieve_memory, store_memory
from .multi_search import multi_search

logger = logging.getLogger(__name__)


def _admin_token() -> Optional[str]:
    """Return the configured admin token, or None if not set.

    Order: env var, then hori.config.ADMIN_TOKEN. Matches the logic in
    services.aios_core.main._check_admin_auth.
    """
    expected = os.environ.get("AIOS_ADMIN_TOKEN") or os.environ.get("HORI_ADMIN_TOKEN")
    if not expected:
        try:
            from hori.config import ADMIN_TOKEN

            expected = ADMIN_TOKEN
        except (ImportError, AttributeError):
            expected = None
    return expected


def _mcp_read_token() -> Optional[str]:
    """Return the configured read-scoped MCP token, or None.

    Order: env var, then hori.config.MCP_READ_TOKEN. Mirrors _admin_token.
    """
    expected = os.environ.get("AIOS_MCP_READ_TOKEN") or os.environ.get(
        "HORI_MCP_READ_TOKEN"
    )
    if not expected:
        try:
            from hori.config import MCP_READ_TOKEN

            expected = MCP_READ_TOKEN
        except (ImportError, AttributeError):
            expected = None
    return expected or None


def _resolve_scope(authorization: Optional[str]) -> str:
    """Resolve a bearer token to its MCP scope: "admin" or "read".

    Fail-closed: raises 401 for missing/malformed credentials, 403 for
    unknown tokens, and 403 when no token is configured at all. The admin
    token keeps full access (all MCP tools + /admin/api); the read token
    authenticates for MCP only and may not invoke write tools.
    """
    admin = _admin_token()
    read = _mcp_read_token()
    if not admin and not read:
        # If no token is configured at all, the privileged surface is
        # disabled.
        raise HTTPException(status_code=403, detail="MCP disabled (no tokens configured)")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization[7:]
    if admin and hmac.compare_digest(token, admin):
        return "admin"
    if read and hmac.compare_digest(token, read):
        return "read"
    raise HTTPException(status_code=403, detail="Invalid token")


def mcp_auth_dependency(authorization: Optional[str] = Header(None)) -> str:
    """FastAPI dependency for MCP endpoints. Returns the token's scope."""
    return _resolve_scope(authorization)


# Tools a read-scoped token may NOT invoke. Everything else in the catalog
# is read-only by design (or already constrained by local-tools policy).
_WRITE_TOOLS = frozenset({"memory_store", "episode_append"})


def _tools_for_scope(scope: str) -> List[mcp_types.Tool]:
    """Return the tool catalog visible to a scope."""
    tools = list_mcp_tools()
    if scope == "admin":
        return tools
    return [t for t in tools if t.name not in _WRITE_TOOLS]


def _tool(name: str, description: str, schema: dict[str, Any]) -> mcp_types.Tool:
    """Build an mcp.types.Tool with a JSON Schema input."""
    return mcp_types.Tool(
        name=name,
        description=description,
        input_schema=schema,
    )


def list_mcp_tools() -> List[mcp_types.Tool]:
    """Return the canonical list of remote MCP tools.

    This is the tool catalog sent to clients during `tools/list`. The
    names and schema must stay stable; clients (Cline, Claude Desktop)
    depend on them.
    """
    return [
        _tool(
            "get_status",
            "Return the full AIOS system state: service health, VRAM, GPU, disk.",
            {"type": "object", "properties": {}, "required": []},
        ),
        _tool(
            "read_file",
            "Read a text file from an allowed local path.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative file path"},
                },
                "required": ["path"],
            },
        ),
        _tool(
            "run_safe_command",
            "Run one allow-listed host inspection command with optional arguments.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}, "default": []},
                },
                "required": ["command"],
            },
        ),
        _tool(
            "search_web",
            "Search the web via the local SearXNG instance and other no-key sources.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        ),
        _tool(
            "fetch_url",
            "Fetch a URL and return its content as Markdown (via Jina Reader / fallback).",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
        ),
        _tool(
            "memory_search",
            "Semantic search over a memory tier (working, project, or longterm).",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "tier": {"type": "string", "enum": ["working", "project", "longterm"], "default": "project"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        ),
        _tool(
            "memory_store",
            "Store a memory point in a specific tier.",
            {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "role": {"type": "string", "default": "user"},
                    "conversation_id": {"type": "string"},
                    "tier": {"type": "string", "enum": ["working", "project", "longterm"], "default": "working"},
                },
                "required": ["content", "conversation_id"],
            },
        ),
        _tool(
            "thread_list",
            "List workstream threads with status, updated date, episode count, and pending review proposals.",
            {"type": "object", "properties": {}, "required": []},
        ),
        _tool(
            "thread_read",
            "Read a thread's state.md plus its latest episodes.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Thread slug, e.g. 'system-memory'"},
                    "episodes": {"type": "integer", "default": 1, "maximum": 10},
                },
                "required": ["name"],
            },
        ),
        _tool(
            "episode_append",
            "Append a structured episode to a thread (create-only, no overwrite).",
            {
                "type": "object",
                "properties": {
                    "thread": {"type": "string"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "author": {"type": "string", "description": "Tool/model that wrote this, e.g. 'devin'"},
                    "verified_by": {"type": "string"},
                    "open_issues": {"type": "array", "items": {"type": "string"}},
                    "files_touched": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["thread", "title", "summary", "author"],
            },
        ),
        _tool(
            "episode_search",
            "Substring search over thread state files and episodes.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "thread": {"type": "string"},
                    "limit": {"type": "integer", "default": 10, "maximum": 100},
                },
                "required": ["query"],
            },
        ),
    ]


def _tool_ok(result: Any) -> mcp_types.CallToolResult:
    """Wrap a successful result as an MCP CallToolResult."""
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(text=json.dumps(result))],
        is_error=False,
    )


def _tool_error(message: str) -> mcp_types.CallToolResult:
    """Wrap an error message as an MCP CallToolResult."""
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(text=json.dumps({"error": message}))],
        is_error=True,
    )


async def _tool_get_status() -> dict[str, Any]:
    """Fetch the system state snapshot from aios_core."""
    # Imported here to avoid a circular import: main.py imports this module
    # at app startup to wire the /mcp routes, but the tool handler needs to
    # call back into the existing system_state endpoint.
    from .main import system_state

    return await system_state()


async def _call_local_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool on the local-tools HTTP server (127.0.0.1:8766).

    Delegates to the existing allowlisted read_file / run_safe_command
    endpoints so the MCP surface does not bypass the local-tools security
    model (path allowlist, command allowlist, output caps, audit log).
    """
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"http://127.0.0.1:8766/{tool_name}",
            json=arguments,
            timeout=30.0,
        )
        if resp.status_code >= 400:
            # local-tools returns the reason (deny.conf citation, policy
            # error) in the error body — surface it so the model sees WHY
            # instead of a bare status code it can't act on.
            try:
                detail = resp.json().get("error") or resp.text
            except Exception:
                detail = resp.text
            raise RuntimeError(
                f"{tool_name} refused ({resp.status_code}): "
                f"{str(detail)[:400]}"
            )
        return resp.json()


async def _tool_search_web(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Search the web via SearXNG and other no-key sources."""
    results = await multi_search(query, max_results=max_results, enrich=False)
    return [
        {
            "title": r.title,
            "url": r.url,
            "snippet": r.snippet,
            "source": r.source,
            "score": r.score,
        }
        for r in results
    ]


# Internal-name suffixes — tailnet, mDNS, common LAN/corp TLDs. Refused
# before resolution so internal names fail even if DNS rewrites them.
# Same policy as idea_factory.netguard — keep the two guards aligned.
_BAD_HOST_SUFFIXES = (".ts.net", ".local", ".internal", ".lan", ".home",
                      ".corp", ".localhost", ".test", ".example")
_BAD_HOSTS = {"localhost"}


def _resolve_public(url: str) -> tuple[Optional[list], Optional[str]]:
    """Resolve and validate url → ([ips], None) or (None, error).

    Refuses non-http(s) schemes, internal host names, and any host that
    resolves to a non-globally-routable address (is_global covers
    loopback/private/link-local/reserved/multicast/CGNAT).
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return None, "invalid URL"
    if parsed.scheme not in ("http", "https"):
        return None, "only http/https URLs are allowed"
    host = (parsed.hostname or "").lower()
    if not host:
        return None, "invalid URL"
    if host in _BAD_HOSTS or host.endswith(_BAD_HOST_SUFFIXES):
        return None, f"blocked: internal host name {host}"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None, f"cannot resolve host: {host}"
    ips = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None, f"unparseable address for {host}"
        if not ip.is_global:
            return None, f"blocked: {host} resolves to non-public address {ip}"
        ips.append(str(ip))
    return (ips, None) if ips else (None, f"no addresses for {host}")


def _fetch_url_blocked(url: str) -> Optional[str]:
    """Cheap pre-check wrapper: error string if url is unsafe, else None."""
    return _resolve_public(url)[1]


@contextlib.contextmanager
def _pinned_dns(host: str, ips: list):
    """Answer getaddrinfo(host) with the pre-validated IPs only, for the
    duration of the block — the connection uses the SAME addresses the
    check approved, closing the resolve→connect rebind window (TOCTOU).

    Process-global patch: other hosts pass through, and concurrent pins
    for the same host can only substitute each other's validated IPs —
    worst case is still a public, validated target. Ported from
    idea_factory.netguard._pinned_dns; keep aligned.
    """
    import socket

    orig = socket.getaddrinfo

    def fake(h, port, *a, **kw):
        if h == host:
            out = []
            for ip in ips:
                fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
                sa = (ip, port, 0, 0) if fam == socket.AF_INET6 else (ip, port)
                out.append((fam, socket.SOCK_STREAM, 6, "", sa))
            return out
        return orig(h, port, *a, **kw)

    socket.getaddrinfo = fake
    try:
        yield
    finally:
        socket.getaddrinfo = orig


async def _tool_fetch_url(url: str) -> Dict[str, Any]:
    """Fetch a URL and return the response body as text.

    Direct GET capped at 200 KB, SSRF-guarded per hop: redirects are
    followed manually (max 5) and each target is resolved, validated,
    and DNS-pinned before the request is sent.
    """
    import httpx
    from urllib.parse import urlparse

    MAX_BYTES = 200_000
    TIMEOUT = 20.0

    current = url
    async with httpx.AsyncClient() as client:
        try:
            for _ in range(5):
                ips, err = _resolve_public(current)
                if err:
                    return {"ok": False, "url": url, "error": err}
                host = (urlparse(current).hostname or "").lower()
                with _pinned_dns(host, ips):
                    resp = await client.get(
                        current, timeout=TIMEOUT, follow_redirects=False
                    )
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        break
                    current = str(httpx.URL(current).join(location))
                    continue
                resp.raise_for_status()
                return {
                    "ok": True,
                    "url": str(resp.url),
                    "content": resp.text[:MAX_BYTES],
                }
            return {"ok": False, "url": url, "error": "too many redirects"}
        except httpx.HTTPStatusError as exc:
            return {"ok": False, "url": url, "error": f"HTTP {exc.response.status_code}"}
        except Exception as exc:
            return {"ok": False, "url": url, "error": str(exc)}


def _tool_memory_search(query: str, tier: str = "project", limit: int = 5) -> List[Dict[str, Any]]:
    """Semantic search over a memory tier."""
    return retrieve_memory(query=query, tier=tier, limit=limit)


def _tool_memory_store(
    content: str,
    role: str,
    conversation_id: str,
    tier: str = "working",
) -> Dict[str, Any]:
    """Store a memory point and return its id."""
    point_id = store_memory(
        content=content,
        role=role,
        conversation_id=conversation_id,
        surface="mcp",
        tier=tier,
    )
    return {"ok": True, "id": point_id}


async def call_mcp_tool(name: str, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    """Dispatch an MCP tool call by name.

    This is the function the JSON-RPC `tools/call` handler invokes. New
    tools are added here; the SSE transport does not need to change.
    """
    try:
        if name == "get_status":
            result = await _tool_get_status()
            return _tool_ok(result)

        if name in ("read_file", "run_safe_command"):
            result = await _call_local_tool(name, arguments)
            return _tool_ok(result)

        if name == "search_web":
            result = await _tool_search_web(
                query=arguments.get("query", ""),
                max_results=arguments.get("max_results", 5),
            )
            return _tool_ok(result)

        if name == "fetch_url":
            result = await _tool_fetch_url(arguments.get("url", ""))
            return _tool_ok(result)

        if name == "memory_search":
            result = _tool_memory_search(
                query=arguments.get("query", ""),
                tier=arguments.get("tier", "project"),
                limit=arguments.get("limit", 5),
            )
            return _tool_ok(result)

        if name == "memory_store":
            result = _tool_memory_store(
                content=arguments.get("content", ""),
                role=arguments.get("role", "user"),
                conversation_id=arguments.get("conversation_id", ""),
                tier=arguments.get("tier", "working"),
            )
            return _tool_ok(result)

        if name == "thread_list":
            return _tool_ok(episode_store.list_threads())

        if name == "thread_read":
            result = episode_store.read_thread(
                name=arguments.get("name", ""),
                episodes=arguments.get("episodes", 1),
            )
            return _tool_ok(result)

        if name == "episode_append":
            result = episode_store.append_episode(
                thread=arguments.get("thread", ""),
                title=arguments.get("title", ""),
                summary=arguments.get("summary", ""),
                author=arguments.get("author", ""),
                verified_by=arguments.get("verified_by"),
                open_issues=arguments.get("open_issues"),
                files_touched=arguments.get("files_touched"),
            )
            return _tool_ok(result)

        if name == "episode_search":
            result = episode_store.search_episodes(
                query=arguments.get("query", ""),
                thread=arguments.get("thread"),
                limit=arguments.get("limit", 10),
            )
            return _tool_ok(result)

        return _tool_error(f"Unknown tool: {name}")
    except Exception as exc:
        logger.exception(f"MCP tool {name!r} failed")
        return _tool_error(f"Tool {name!r} failed: {exc}")


# Live SSE sessions: session_id -> {"queue": asyncio.Queue, "scope": str}.
# Scope is bound at SSE-connect time so a session cannot be escalated by a
# later POST carrying a broader token. Aios-core runs as a single uvicorn
# worker, so a module-level dict is safe.
_mcp_sessions: Dict[str, Dict[str, Any]] = {}


async def _mcp_event_stream(session_id: str, queue: asyncio.Queue) -> Any:
    """SSE event generator for an MCP session.

    Yields an `endpoint` event first, then waits on the queue for
    JSON-RPC responses and yields them as `message` events. If the client
    disconnects, the generator is closed and the session is removed.
    """
    try:
        yield {"event": "endpoint", "data": f"/mcp/messages?session_id={session_id}"}

        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # No traffic for 30s; just keep the connection open.
                # The next loop iteration will continue waiting.
                continue
            if msg is None:
                break
            yield {"event": "message", "data": json.dumps(msg)}
    finally:
        _mcp_sessions.pop(session_id, None)


def _mcp_response(msg_id: Any, result: Any) -> Dict[str, Any]:
    """Build a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _mcp_error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    """Build a JSON-RPC 2.0 error response."""
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


async def _handle_mcp_message(
    msg: Dict[str, Any], scope: str = "admin"
) -> Optional[Dict[str, Any]]:
    """Dispatch a single JSON-RPC message and return the response, or None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications have no `id`; we do not return a response for them.
    if method in ("initialized", "notifications/initialized"):
        return None

    # Any message carrying a method but no `id` is a JSON-RPC
    # notification: fire-and-forget — never emit a response, even for
    # unknown methods (monkey-tested live: notifications/cancelled
    # produced a stray -32601). A methodless object is a malformed
    # Request — -32600 with null id.
    if method is not None and "id" not in msg:
        return None
    if method is None:
        return _mcp_error(msg_id, -32600, "Invalid Request")

    if method == "initialize":
        return _mcp_response(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aios-mcp", "version": "1.0.0"},
        })

    if method == "ping":
        return _mcp_response(msg_id, {})

    if method == "tools/list":
        tools = [t.model_dump(by_alias=True) for t in _tools_for_scope(scope)]
        return _mcp_response(msg_id, {"tools": tools})

    if method == "tools/call":
        name = params.get("name")
        if scope != "admin" and name in _WRITE_TOOLS:
            return _mcp_error(
                msg_id, -32603,
                f"Tool {name!r} not permitted for token scope {scope!r}",
            )
        arguments = params.get("arguments") or {}
        result = await call_mcp_tool(name, arguments)
        return _mcp_response(msg_id, result.model_dump(by_alias=True))

    return _mcp_error(msg_id, -32601, f"Method not found: {method}")


async def mcp_sse_endpoint(
    request: Request,
    scope: str = Depends(mcp_auth_dependency),
):
    """GET /mcp/sse — establish an SSE session.

    Returns an SSE stream. The first event tells the client where to POST
    JSON-RPC messages. Subsequent events carry JSON-RPC responses. The
    connecting token's scope is bound to the session.
    """
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _mcp_sessions[session_id] = {"queue": queue, "scope": scope}
    return EventSourceResponse(_mcp_event_stream(session_id, queue))


async def mcp_messages_endpoint(
    request: Request,
    _auth: str = Depends(mcp_auth_dependency),
):
    """POST /mcp/messages?session_id=... — submit a JSON-RPC message.

    Any valid token may POST, but the SESSION's bound scope (set at
    SSE-connect time) decides what the message may do — a broader token on
    the POST cannot escalate a narrower session.
    """
    session_id = request.query_params.get("session_id")
    session = _mcp_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    body = await request.body()
    try:
        msg = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    response = await _handle_mcp_message(msg, scope=session["scope"])
    if response is not None:
        await session["queue"].put(response)

    return Response(status_code=202)


async def mcp_streamable_endpoint(
    request: Request,
    scope: str = Depends(mcp_auth_dependency),
):
    """POST /mcp/http — MCP streamable-HTTP transport (2025-03-26 spec).

    Native MCP clients (Open-WebUI >=0.6.31's streamablehttp_client) talk
    to a single POST endpoint. Stateless by design: no mcp-session-id is
    issued, and scope is re-resolved from the bearer on EVERY request —
    strictly stronger than the SSE lane's connect-time session binding.

    Per spec: a POST carrying only notifications gets 202 empty; a POST
    with requests gets application/json with the response(s) — batch
    input yields a batch response. GET/DELETE are 405 (no session stream
    exists to read or tear down).
    """
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    batch = isinstance(payload, list)
    messages = payload if batch else [payload]
    responses = []
    for msg in messages:
        if not isinstance(msg, dict):
            responses.append(_mcp_error(None, -32600, "Invalid Request"))
            continue
        resp = await _handle_mcp_message(msg, scope=scope)
        if resp is not None:
            responses.append(resp)

    if not responses:
        return Response(status_code=202)
    out = responses if batch else responses[0]
    from starlette.responses import JSONResponse

    return JSONResponse(out, media_type="application/json")
