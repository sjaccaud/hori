"""MCP-over-SSE server for remote Jacko tools.

Exposes a small, admin-token-gated MCP surface on aios_core so
Tailnet clients (Cline/Claude on a MacBook, scripts on a phone) can call
Jacko tools without public internet exposure.

Traces to:
  .devin/prd/remote-mcp-tools.md
  docs/manifesto.md Pillar II (Distributed Mesh) and Pillar IV (Remote Interaction)

SECURITY:
  - Tailnet-only: the endpoint is not exposed via Funnel.
  - Admin bearer token: the same token used for /admin/api. Fail-closed
    if the token is not configured.
  - `read_file` and `run_safe_command` are delegated to the existing
    local-tools server (127.0.0.1:8766), inheriting its allowlists and
    command filtering.
  - `search_web` and `fetch_url` make external network calls but are
    read-only and capped.
  - `memory_search` and `memory_store` use the aios_core memory module
    (Qdrant/SQLite) and write to memory tiers.
"""

import asyncio
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


def _require_admin_token(authorization: Optional[str]) -> None:
    """Fail-closed admin token validation.

    Raises HTTPException 401 or 403 for missing/invalid tokens. This is the
    same boundary used for /admin/api/* in main.py.
    """
    expected = _admin_token()
    if not expected:
        # If the admin token is not configured at all, the privileged
        # surface is disabled.
        raise HTTPException(status_code=403, detail="Admin API disabled (admin token not set)")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization[7:]
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")


def mcp_auth_dependency(authorization: Optional[str] = Header(None)) -> None:
    """FastAPI dependency for MCP endpoints."""
    _require_admin_token(authorization)


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
        resp.raise_for_status()
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


async def _tool_fetch_url(url: str) -> Dict[str, Any]:
    """Fetch a URL and return the response body as text.

    Tries a direct HTTP GET first, capped at 200 KB. If the page is HTML
    and a readable conversion is available in the future, it can be added
    here. Jina Reader is avoided to keep the surface self-hosted.
    """
    import httpx

    MAX_BYTES = 200_000
    TIMEOUT = 20.0

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            text = resp.text[:MAX_BYTES]
            return {"ok": True, "url": url, "content": text}
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

        return _tool_error(f"Unknown tool: {name}")
    except Exception as exc:
        logger.exception(f"MCP tool {name!r} failed")
        return _tool_error(f"Tool {name!r} failed: {exc}")


# Live SSE sessions: session_id -> asyncio.Queue of JSON-RPC responses.
# Aios-core runs as a single uvicorn worker, so a module-level dict is safe.
_mcp_sessions: Dict[str, asyncio.Queue] = {}


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


async def _handle_mcp_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch a single JSON-RPC message and return the response, or None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications have no `id`; we do not return a response for them.
    if method in ("initialized", "notifications/initialized"):
        return None

    if method == "initialize":
        return _mcp_response(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aios-mcp", "version": "1.0.0"},
        })

    if method == "ping":
        return _mcp_response(msg_id, {})

    if method == "tools/list":
        tools = [t.model_dump(by_alias=True) for t in list_mcp_tools()]
        return _mcp_response(msg_id, {"tools": tools})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        result = await call_mcp_tool(name, arguments)
        return _mcp_response(msg_id, result.model_dump(by_alias=True))

    return _mcp_error(msg_id, -32601, f"Method not found: {method}")


async def mcp_sse_endpoint(
    request: Request,
    _admin_auth: None = Depends(mcp_auth_dependency),
):
    """GET /mcp/sse — establish an SSE session.

    Returns an SSE stream. The first event tells the client where to POST
    JSON-RPC messages. Subsequent events carry JSON-RPC responses.
    """
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _mcp_sessions[session_id] = queue
    return EventSourceResponse(_mcp_event_stream(session_id, queue))


async def mcp_messages_endpoint(
    request: Request,
    _admin_auth: None = Depends(mcp_auth_dependency),
):
    """POST /mcp/messages?session_id=... — submit a JSON-RPC message."""
    session_id = request.query_params.get("session_id")
    queue = _mcp_sessions.get(session_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Session not found")

    body = await request.body()
    try:
        msg = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    response = await _handle_mcp_message(msg)
    if response is not None:
        await queue.put(response)

    return Response(status_code=202)
