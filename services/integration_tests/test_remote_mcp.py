"""Integration test: MCP-over-SSE remote surface.

Exercises the full HTTP path for the admin-token-gated MCP server on
aios_core: open an SSE session, read the endpoint event, POST a
`tools/list` JSON-RPC request, and read the response from the SSE stream.

Defends: the new remote MCP surface and Tailnet-facing HTTP wiring.

Traces to: .devin/prd/remote-mcp-tools.md
"""

import asyncio
import json
import os

import httpx
import pytest

from services.aios_core.mcp_server import list_mcp_tools

BASE = "http://127.0.0.1:5680"


def _admin_token() -> str:
    """Return the admin token used by aios_core.

    Prefers the env var already exported by the runtime, then falls back
    to the committed config. The config token is the same secret used for
    /admin/api; it is not printed or logged.
    """
    token = os.environ.get("AIOS_ADMIN_TOKEN") or os.environ.get("HORI_ADMIN_TOKEN")
    if not token:
        from hori.config import ADMIN_TOKEN
        token = ADMIN_TOKEN
    if not token:
        pytest.skip("No admin token configured")
    return token


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


async def _read_sse_event(line_iter) -> tuple[str, str]:
    """Read one SSE event from an httpx line iterator.

    Returns (event_name, data). An event ends at a blank line; the
    data field may be split across multiple `data:` lines.
    """
    event: str | None = None
    data_parts: list[str] = []
    async for line in line_iter:
        line = line.rstrip("\r")
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_parts.append(line[len("data:") :].strip())
        elif line == "" and event is not None:
            return event, "".join(data_parts)
    if event is None:
        raise RuntimeError("SSE stream ended before any event")
    return event, "".join(data_parts)


def test_mcp_sse_rejects_unauthenticated():
    """GET /mcp/sse without a token must fail closed."""
    with httpx.Client(timeout=5) as client:
        r = client.get(f"{BASE}/mcp/sse")
        assert r.status_code in (401, 403)


def test_mcp_tools_list_over_sse():
    """Full round-trip: SSE session -> tools/list POST -> SSE response."""
    headers = _auth_header()
    expected_tools = len(list_mcp_tools())

    async def _run():
        async with httpx.AsyncClient(timeout=10) as client:
            async with client.stream(
                "GET",
                f"{BASE}/mcp/sse",
                headers=headers,
                timeout=10,
            ) as sse:
                assert sse.status_code == 200
                lines = sse.aiter_lines()
                event, data = await _read_sse_event(lines)
                assert event == "endpoint"
                assert "session_id=" in data
                session_id = data.split("session_id=")[1]

                # Start reading the next SSE event before POSTing, so the
                # response is captured as soon as it is queued.
                next_event = asyncio.create_task(_read_sse_event(lines))

                r = await client.post(
                    f"{BASE}/mcp/messages",
                    params={"session_id": session_id},
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/list",
                        "params": {},
                    },
                    timeout=5,
                )
                assert r.status_code == 202

                event, data = await asyncio.wait_for(next_event, timeout=10)
                assert event == "message"
                msg = json.loads(data)
                assert msg["jsonrpc"] == "2.0"
                assert msg["id"] == 1
                assert "result" in msg
                assert len(msg["result"]["tools"]) == expected_tools

    asyncio.run(_run())
