# PRD: Workspace Markdown Tools

## 1. Intent trace

- **Manifesto:** Pillar VII, Engineering Discipline — the LLM proposes; trusted
  architecture validates and executes; tests and audit are part of the feature.
- **Safety:** docs/safety.md Layer 3/4/5 — side-effect tools require explicit
  confirmation, bounded execution, and audit. This is intentionally
  confirmation-gated rather than direct write access.
- **Roadmap:** early, scoped off-roadmap use of the Tier 4 side-effect-tool
  pattern (PoC 15.10 confirmation gate). It does not implement the full Tier 4
  confirmation architecture; it reuses the existing dry-run/token pattern from
  safe project creation and confines the blast radius to `~/Projects`.

## 2. Interface contract

Expose a dedicated Open-WebUI tool server on `127.0.0.1:8771` with three
operations:

- `propose_workspace_write(description: str, entries: list[WorkspaceEntry]) -> dict`
  - `WorkspaceEntry.path` is a relative path under the configured workspace root.
  - `WorkspaceEntry.is_dir` creates a directory when true.
  - File entries must end in `.md` (case-insensitive) and carry UTF-8 `content`.
  - Returns a dry-run manifest plus a short-lived confirmation token.
- `confirm_workspace_write(token: str, confirmed: bool) -> dict`
  - `confirmed=false` returns the dry-run again and writes nothing.
  - `confirmed=true` creates directories and Markdown files only if the whole
    preflight passes.
- `rollback_workspace_write(token: str) -> dict`
  - Within a short rollback window, removes only files/directories recorded as
    created by that token. Existing files and directories are never removed.

Configuration:

- `WORKSPACE_TOOLS_ROOT` controls the writable root; default is `~/Projects`.
  No project-specific path is hard-coded. The current content work can target
  `content_factory/...` as a relative path.

## 3. Safety classification

**Side-effect.** Safety controls:

- relative paths only; absolute paths, `..`, empty paths, and NUL bytes rejected
- canonical path resolution plus explicit symlink-component rejection
- create-only: existing files cannot be overwritten; rollback never removes
  pre-existing paths
- `.md` files only; directories allowed
- manifest/token confirmation before any write
- entry-count, content-size, and HTTP request-body caps
- JSONL audit log with paths, hashes, sizes, action, token, and result
- Open-WebUI attaches the tool server only to `qwen-deepdive` initially
- systemd sandbox keeps the rest of the home directory read-only except the
  configured workspace and audit-data directories

Fail-closed behavior: invalid manifests return an error; confirmation failures
write nothing; partial writes are rolled back best-effort; rollback refuses any
path not recorded in the audit log.

## 4. Test contract

Write the adversarial test first:

- `tests/adversarial/test_workspace_write_path_escape.py`
  - rejects `../`, absolute paths, symlink components, non-Markdown files, and
    attempts to overwrite an existing file
  - must fail before implementation and pass after

Unit/integration tests:

- `services/aios_core/test_workspace_writes.py`
  - propose/confirm happy path creates nested directories and `.md` files
  - unconfirmed proposals write nothing
  - expired/unknown tokens cannot write
  - rollback removes only paths created by the token
- `services/open-webui/test_workspace_tools_server.py`
  - health/OpenAPI expose the three operations
  - HTTP endpoints round-trip a proposal, confirmation, and rollback
- Existing `services/integration_tests/test_web_search_pipeline.py` static
  config coverage may be extended to assert the workspace server is attached
  only to `qwen-deepdive`.

## 5. Module placement

- `services/aios_core/workspace_writes.py` — validation, proposal lifecycle,
  confirmation, rollback, and audit. This keeps the side-effect policy behind a
  small interface and outside the HTTP layer.
- `services/open-webui/workspace_tools_server.py` — thin OpenAPI adapter.
- `services/open-webui/workspace-tools.service` — user service definition with
  `WORKSPACE_TOOLS_ROOT=%h/Projects` and minimal filesystem write paths.
- `services/open-webui/fix_tooling.py` — canonical Open-WebUI registration and
  per-model attachment.

## 6. Out of scope

- arbitrary text/code file writes
- overwrite, append, rename, move, or delete operations
- shell access or package installation
- direct unconfirmed writes
- exposing the write server to every local model
- public network access
- full Tier 4 confirmation UI (this uses the existing proposal/token pattern)

## 7. Work-order link

`core/state/proposed_work_orders/13f799b2-2256-494b-bdfe-c24d0aa63a50.json`
