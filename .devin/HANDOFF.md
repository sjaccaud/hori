# Handoff — SLICE-13 Remote MCP Tools + Tailnet Hardening

> Resume point for the next Devin session. Read this first, then
> `.devin/SLICE_LOG.md`, then `scripts/devin_resume.sh`.

## Branch

`rebrand/hori`

## What was done

- Wrote `.devin/prd/remote-mcp-tools.md` + `core/state/proposed_work_orders/60b7e3e7-f9b6-404b-9848-8c5c87750700.json`.
- Implemented `services/aios_core/mcp_server.py`: admin-token-gated MCP-over-SSE with 7 tools.
- Wired `/mcp/sse` and `/mcp/messages` in `services/aios_core/main.py`.
- Added unit tests (`services/aios_core/test_mcp_server.py`) and adversarial test (`tests/adversarial/test_mcp_unauthorized.py`).
- Updated Tailscale Serve scripts:
  - `scripts/hardening/harden_funnel.sh` — strict port-aware allowlist.
  - `scripts/hardening/audit_funnel.sh` — port-aware audit.
- Added Makefile `test-mcp` target.
- Exposed nano on `https://<tailnet>:8444/`.
- Ran live verification:
  - `audit_funnel.sh` passes.
  - `https://<tailnet>/mcp/sse` returns `event: endpoint` with session_id.
  - Unauthenticated `mcp/sse` returns `401`.
  - `https://<tailnet>:8444/v1/models` returns `nano`.
- `make test` passed: 451 unit, 42 integration, 26 regression, 148 adversarial (4 skipped total).
- Committed to `rebrand/hori`:
  - `6956d00` main implementation.
  - `b32aa5e` slice log handoff.

## What is still open

- Add `services/integration_tests/test_remote_mcp.py` for a full SSE+POST round-trip.
- Update `docs/operations.md` and `AGENTS.md` with the new endpoint and client setup.
- Canonicalize aios_core restart with `sudo systemctl restart aios_core` (currently a manually restarted process from this session).
- Decide whether to rotate the admin token (it was visible in local tool output during testing).

## Resume command for next session

Start a new chat and say:

> Continue from `.devin/HANDOFF.md` and `.devin/SLICE_LOG.md` — finish SLICE-13 (integration test, full test run, docs).

Or run `scripts/devin_resume.sh` and read those two files first.
