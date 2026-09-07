# Handoff — SLICE-13 Remote MCP Tools + Tailnet Hardening

> **This slice is COMPLETE.** This file is kept as a tombstone; the canonical
> state lives in `.devin/SLICE_LOG.md` and `core/state/proposed_work_orders/60b7e3e7-f9b6-404b-9848-8c5c87750700.json`.

## Branch

`rebrand/hori` (merged to rebrand/hori; main merge deferred to final rebrand integration)

## What was done

- Wrote `.devin/prd/remote-mcp-tools.md` + `core/state/proposed_work_orders/60b7e3e7-f9b6-404b-9848-8c5c87750700.json` (now `complete`).
- Implemented `services/aios_core/mcp_server.py`: admin-token-gated MCP-over-SSE with 7 tools.
- Wired `/mcp/sse` and `/mcp/messages` in `services/aios_core/main.py`.
- Added unit tests (`services/aios_core/test_mcp_server.py`) and adversarial test (`tests/adversarial/test_mcp_unauthorized.py`).
- Added integration test `services/integration_tests/test_remote_mcp.py` for the full SSE+POST `tools/list` round-trip.
- Updated Tailscale Serve scripts:
  - `scripts/hardening/harden_funnel.sh` — strict port-aware allowlist.
  - `scripts/hardening/audit_funnel.sh` — port-aware audit.
- Added Makefile `test-mcp` target.
- Exposed nano on `https://<tailnet>:8444/`.
- Updated documentation:
  - `docs/operations.md` — MCP endpoints, Tailnet reachability, ports, and nano endpoint.
  - `.devin/AGENTS.md` — Tailscale Serve paths and MCP/nano surface bullets.
  - `.devin/ubiquitous_language.md` — `MCP-over-SSE` and `Nano Chat` terms.
- Ran live verification:
  - `audit_funnel.sh` passes.
  - `https://<tailnet>/mcp/sse` returns `event: endpoint` with session_id.
  - Unauthenticated `mcp/sse` returns `401`.
  - `https://<tailnet>:8444/v1/models` returns `nano`.
- `make test` passed: 451 unit, 43 integration, 26 regression, 148 adversarial (5 skipped total; one integration skip is the MCP round-trip when `AIOS_ADMIN_TOKEN` is unset).

## What is still open

- None. aios_core is active and was restarted by the user.
- The `AIOS_ADMIN_TOKEN` rotation decision was left as-is per the user.
