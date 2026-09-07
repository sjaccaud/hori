# PRD — Remote Jacko Tools via MCP over Tailnet

## 1. Intent trace

- **Manifesto pillars:**
  - **II — Distributed Mesh Orchestration:** Expose Jacko tools to authorized Tailnet clients (macOS Cline, Claude Desktop) so the compute pool can be invoked from anywhere the mesh reaches.
  - **IV — Seamless Voice & Remote Interaction:** Remote text/tool access from the phone and MacBook on cellular/public wifi, using Tailnet HTTPS only (no public internet).
  - **VII — Engineering Discipline:** TDD, strict auth, and adversarial-first testing for a new privileged remote surface.
- **Roadmap:** Off-roadmap product slice. Builds on Tier 2 safety spine (tool daemon, Landlock, seccomp) and Tier 3 remote surfaces (voice/chat/admin). Does not extend the safety spine; it routes through it.
- **Why now:** The user wants OWUI chat models available remotely and wants to plug the search/local-tool stack into Cline/Claude on a MacBook on the go. Tailscale is already mesh-only; we need to harden the Serve surface and expose a clean MCP-over-SSE endpoint.

## 2. Interface contract

### 2.1 Tailscale Serve surface

All HTTPS, Tailnet-only. Funnel stays OFF.

| Port | Path(s) | Backend | Purpose |
|---|---|---|---|
| 3443 | `/` (catch-all) | `http://127.0.0.1:3000` | Open-WebUI web app + `/api/v1` OpenAI endpoint. Already required catch-all. |
| 8443 | `/` (catch-all) | `http://localhost:8080` | Raw llama-server router (`qwen-*`, `devstral-*` aliases). |
| 8444 | `/` (catch-all) | `http://localhost:8090` | `nano` dedicated server. New. |
| 443 | `/voice`, `/v1/voice/*`, `/chat`, `/health`, `/static`, `/v1/chat/completions`, `/mcp/*`, `/admin`, `/admin/api` | `http://localhost:5680` | aios_core endpoints. No catch-all on 443. |

The audit/harden scripts must become port-aware: catch-all is allowed only on dedicated backend ports (3443, 8443, 8444); port 443 must use an explicit allowlist.

### 2.2 MCP-over-SSE endpoint (aios_core)

- `GET /mcp/sse` — establish an SSE session. Requires `Authorization: Bearer <admin-token>`.
- `POST /mcp/messages?session_id=<uuid>` — JSON-RPC messages for the established session. Requires same `Authorization` header.

The SSE transport uses `mcp.server.sse.SseServerTransport` with endpoint `/mcp/messages`. On success the SSE stream returns an `endpoint` event telling the client where to POST.

### 2.3 Tools exposed via `tools/list` and `tools/call`

| Tool | Args | Side-effect | Uses |
|---|---|---|---|
| `get_status` | (none) | read-only | aios_core `/system/state` |
| `read_file` | `path: str` | read-only | local-tools server `127.0.0.1:8766/read_file` |
| `run_safe_command` | `command: str`, `args: list[str]` (optional) | side-effect (host inspection) | local-tools server `127.0.0.1:8766/run_safe_command` |
| `search_web` | `query: str`, `max_results: int` (default 5) | read-only (external network) | aios_core `multi_search` (SearXNG + no-key sources) |
| `fetch_url` | `url: str` | read-only (external network) | no-key Jina Reader or direct HTTP fetch, capped |
| `memory_search` | `query: str`, `tier: str` (`working`\|`project`\|`longterm`), `limit: int` | read-only | aios_core `memory.retrieve_memory` |
| `memory_store` | `content: str`, `role: str`, `conversation_id: str`, `tier: str` | side-effect (memory write) | aios_core `memory.store_memory` |

All tools return `CallToolResult` with `content=[types.TextContent(type="text", text=json.dumps(result))]`. Errors are caught and returned as `isError=true` with a short, non-leaking message.

## 3. Safety classification

- **Classification:** side-effect (`run_safe_command` and `memory_store` mutate host state / memory). Read-only tools are heavily used.
- **Fail-closed:**
  - If `AIOS_ADMIN_TOKEN` / `HORI_ADMIN_TOKEN` is not set, the MCP endpoints return `403 Admin API disabled`.
  - No token → no SSE connection; no `session_id` → no POST handling.
  - `run_safe_command` is delegated to the existing local-tools server, inheriting its command allowlist, timeout, and output caps.
  - `read_file` is delegated to the existing local-tools server, inheriting its path allowlist (`LOCAL_TOOLS_READ_ROOTS`).
- **Safety-spine involvement:**
  - Does not change the tool daemon, Landlock, seccomp, or Sherpa.
  - Reuses the privileged admin-token boundary from `/admin/api/*`.
  - `search_web` and `fetch_url` make external network calls from the aios_core process (which has normal network access). They are read-only and capped.
- **Trust boundary:** Tailnet + admin token. The Mac/phone client must know the token. No public internet exposure. Session tokens (UUIDs) are short-lived and bound to the SSE connection.

## 4. Test contract

- **Adversarial test (first):** `tests/adversarial/test_mcp_unauthorized.py`
  - `GET /mcp/sse` without `Authorization` must return 401/403 before any tool logic.
  - `GET /mcp/sse` with wrong `Authorization` must return 403.
  - `POST /mcp/messages` with no `session_id` or wrong token must fail before the message reaches the MCP server.
  - Must be written first and fail before auth is implemented.

- **Unit test:** `services/aios_core/test_mcp_server.py`
  - `list_tools()` returns the 7 tools above in a deterministic order.
  - `call_tool("get_status")` returns a dict with `status` and `services`.
  - `call_tool("read_file", {"path": "/tmp/test.txt"})` returns file content (mocked or local-tools allowed path).
  - `call_tool("memory_store")` then `call_tool("memory_search")` round-trips a memory point.
  - `call_tool("search_web", {"query": "SearXNG test"})` returns a list with `title`, `url`, `snippet`.

- **Integration test:** `services/integration_tests/test_remote_mcp.py`
  - Real `GET /mcp/sse` with admin token opens an SSE stream, receives an `endpoint` event, then `POST`s `tools/list` JSON-RPC and gets the 7 tools.
  - Real `tools/call` for `get_status` with admin token returns the full stack status.

- **Tailscale config test:** `scripts/hardening/test_funnel.sh` (new)
  - Runs `audit_funnel.sh` after hardening and asserts exit 0.
  - Verifies the new `:8444` and `/mcp` paths are present and the 443 port has no catch-all.

## 5. Module placement

- **Module:** `services/aios_core/mcp_server.py`
  - `create_mcp_server()` — builds the `mcp.server.lowlevel.server.Server` with tool handlers.
  - `mcp_sse_endpoint(request)` — FastAPI/Starlette handler for `GET /mcp/sse`; validates admin token, then runs `SseServerTransport.connect_sse()`.
  - `mcp_messages_endpoint(scope, receive, send)` — ASGI handler for `POST /mcp/messages`; delegates to `SseServerTransport.handle_post_message()`.
  - Tool handlers call `services/aios_core/multi_search.py`, `services/aios_core/memory.py`, and the local-tools HTTP server.
  - `_validate_admin_token(auth_header: str | None) -> None` — shared with the admin panel. Refactor from `main.py` or re-implement with fail-closed.

- **Wiring:** `services/aios_core/main.py`
  - Add `app.add_route("/mcp/sse", mcp_sse_endpoint)` and `app.add_route("/mcp/messages", mcp_messages_endpoint, methods=["POST"])`.

- **Tailscale scripts:** `scripts/hardening/harden_funnel.sh` and `scripts/hardening/audit_funnel.sh`
  - Update to be port-aware and include the new surface.

- **Mac client bridge (out of first slice or minimal):** `surfaces/macos/mcp_bridge.py`
  - Optional stdio ↔ SSE bridge for Cline, if Cline on macOS does not support SSE natively. First slice can include a minimal `npx mcp-remote` command in docs; a committed Python bridge is a follow-up.

## 6. Out of scope

- Public internet exposure (Funnel stays OFF).
- Project-creation tools in this slice; they live on `127.0.0.1:8769` and can be added later once the MCP transport is proven.
- A native iOS MCP client; the phone uses the OWUI web app.
- Streaming tool results; first slice is request/response.
- Multi-user MCP sessions / OAuth. Single admin token, same as `/admin/api`.

## 7. Work-order link

`core/state/proposed_work_orders/60b7e3e7-f9b6-404b-9848-8c5c87750700.json`
