# PRD — Episode Threads over MCP (SLICE-15)

## 1. Intent trace

- **Pillar II (Distributed Mesh) / Pillar IV (Remote Interaction):** the
  workstream store becomes reachable by every client over the existing
  admin-token-gated MCP surface — same boundary as SLICE-13.
- **Pillar VI (memory that compounds):** threads/episodes are the
  persistent workstream layer above conversational semantic memory.
- **Roadmap:** extends the memory tier (Tier 1B) and remote-tools (Tier 3
  MCP) work; marked off-roadmap as a named PoC — justified because it is
  the substrate for the "hippocampus" direction (see
  `~/.local/share/hori/threads/system-memory/state.md`).

## 2. Interface contract

Four new tools on the existing `/mcp/sse` + `/mcp/messages` transport —
no new ports, no new services, same bearer auth.

| Tool | Args | Returns |
|---|---|---|
| `thread_list` | — | `[{name, status, updated, episode_count}]` |
| `thread_read` | `name` (slug), `episodes` (int, default 1, max 10) | `{name, state, episodes: [{file, frontmatter, body}]}` |
| `episode_append` | `thread` (slug), `title`, `summary`, `author`, `verified_by?`, `open_issues?` (list), `files_touched?` (list) | `{ok, file}` — creates `episodes/NNN-<slug>.md`, append-only |
| `episode_search` | `query` (substring, case-insensitive), `thread?`, `limit` (default 10) | `[{file, line, context}]` |

Module-level API (`services/aios_core/episodes.py`):

```python
THREADS_ROOT: Path          # env HORI_THREADS_ROOT, default ~/.local/share/hori/threads
def list_threads(root=THREADS_ROOT) -> list[dict]
def read_thread(name, episodes=1, root=THREADS_ROOT) -> dict
def append_episode(thread, title, summary, author, verified_by=None,
                   open_issues=None, files_touched=None,
                   root=THREADS_ROOT) -> dict
def search_episodes(query, thread=None, limit=10, root=THREADS_ROOT) -> list[dict]
```

## 3. Safety classification

**Side-effect** (filesystem writes under the threads root). Not a safety
spine component — does not touch tool_daemon, registry, or Sherpa — but
it is a *remote write surface*, so it gets the full treatment:

- **Validation (name safety):** thread names must match
  `^[a-z0-9][a-z0-9-]{0,63}$`. Anything else → hard error, no path is
  ever constructed from it. This is the load-bearing invariant.
- **Append-only:** `episode_append` creates `episodes/NNN-<slug>.md` with
  `open(..., "x")` semantics — no overwrite, no delete, no write to
  `state.md` (state stays human/agent-edited through normal file means).
- **Caps:** episode body ≤ 64 KiB; ≤ 500 episodes/thread; title ≤ 200
  chars; each list field ≤ 50 items.
- **Frontmatter containment:** field values are emitted as YAML-safe
  plain scalars; embedded newlines/`---` in `title` are rejected, other
  fields are emitted as block content or escaped.
- **Fail-closed:** invalid input → `CallToolResult(is_error=True)`;
  nothing is created.
- **Auth boundary:** unchanged — same `_require_admin_token` dependency
  as every other MCP tool.

## 4. Test contract

- **Unit:** `services/aios_core/test_episodes.py` —
  `test_append_creates_episode_file`, `test_read_thread_returns_state_and_latest_episode`,
  `test_list_threads_returns_status`, `test_search_finds_substring`,
  `test_append_is_append_only_no_overwrite`, `test_missing_thread_errors`.
- **MCP dispatch:** extend `services/aios_core/test_mcp_server.py` —
  tools/list contains the four tools; `tools/call episode_append` round
  trip on a tmp root.
- **Adversarial (WRITTEN FIRST, must fail before implementation):**
  `tests/adversarial/test_episode_writes_confined.py` — defends the
  threads root against writes outside it and corrupted state:
  `test_rejects_traversal_thread_names` (`..`, `/`, `\`, absolute),
  `test_rejects_oversized_episode`, `test_rejects_frontmatter_breakout`
  (`---` / newlines in `title`), `test_never_writes_outside_threads_root`.
  Traces to Pillar VII (adversarial TDD) — write surface on a remote API.

## 5. Module placement

`services/aios_core/episodes.py` — the store is an aios_core concern
(memory/state lives there; the MCP module stays a thin dispatch layer).
Deep module: all path validation, frontmatter serialization, and file
layout live behind the four functions. `mcp_server.py` gains only schema
entries + dispatch branches.

Store root: `HORI_THREADS_ROOT` env, default `~/.local/share/hori/threads`
(the seeded Phase-0 store).

## 6. Out of scope

- Semantic/vector search over episodes (use `memory_search`; a fused
  retriever is a later slice).
- Auto-rewriting `state.md` from episodes (deliberate — state is
  curated, episodes are history; consolidation can propose, Slice C).
- Editing/deleting episodes (append-only by design).
- Unauthenticated or per-thread ACL access (single admin token today).

## 7. Work-order link

`core/state/proposed_work_orders/a1f0c9e2-3b4d-4e5f-8a6b-7c8d9e0f1a2b.json`
