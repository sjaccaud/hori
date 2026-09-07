"""Adversarial test: MCP remote surface requires admin token.

Tests that the new MCP-over-SSE endpoints on aios_core are not reachable
without a valid admin bearer token. The endpoints expose privileged tools
(read_file, run_safe_command, search_web, fetch_url, memory) to the Tailnet;
without auth, any device on the Tailnet could invoke them.

Defends: the new remote MCP surface and the admin-token boundary.

Status: PASSING — the /mcp/sse and /mcp/messages routes exist and reject
unauthenticated requests before any tool logic runs.
"""

import os

from fastapi.testclient import TestClient

from services.aios_core.main import app


def test_mcp_endpoints_reject_unauthenticated_requests():
    """Unauthenticated GET /mcp/sse and POST /mcp/messages must fail."""
    old_token = os.environ.get("AIOS_ADMIN_TOKEN")
    os.environ["AIOS_ADMIN_TOKEN"] = "test-admin-token"
    try:
        client = TestClient(app)

        # No Authorization header
        sse = client.get("/mcp/sse")
        assert sse.status_code in (401, 403), f"Expected 401/403, got {sse.status_code}"

        # Wrong token
        wrong = client.get(
            "/mcp/sse",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert wrong.status_code in (401, 403), f"Expected 401/403, got {wrong.status_code}"

        # No token on message endpoint
        msg = client.post("/mcp/messages")
        assert msg.status_code in (401, 403), f"Expected 401/403, got {msg.status_code}"
    finally:
        if old_token is None:
            os.environ.pop("AIOS_ADMIN_TOKEN", None)
        else:
            os.environ["AIOS_ADMIN_TOKEN"] = old_token
