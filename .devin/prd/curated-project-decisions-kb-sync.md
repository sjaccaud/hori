# PRD — Curated Project-Decisions Knowledge Sync

## 1. Intent trace

- **Manifesto pillars:** III (Persistent Context & Memory) and VII (Engineering Discipline). HORI's knowledge base must surface not only the current runtime state but also the rationale, architecture, and decisions that produced it. The stale `project-decisions.md` in Open-WebUI contains useful ideas (manifesto, roadmap, safety principles, FDFE operations) but also outdated local-AI details. This feature separates "current state" from "curated project decisions" and keeps both in sync.
- **Roadmap:** Off-roadmap maintenance. Touches the same Open-WebUI adapter as the original `system-state-kb-sync` slice and supports PoC 15.23 (source citation) by making project decisions a first-class, citable knowledge source.
- **Why now:** Open-WebUI previously answered benchmark questions from an old, consolidated `project-decisions.md`. The product owner noted it still contained good ideas. The current `aios-system-state.md` is authoritative for runtime state but does not include the broader decision history. We need a second, curated knowledge file that is also regenerated from local canonical sources.

## 2. Interface contract

Public surface in `services/open_webui/system_state_sync.py`:

```python
def render_project_decisions_md(
    sources: list[Path],
    allow_list: Optional[Iterable[Path]] = None,
) -> str:
    """Return a markdown synthesis of the supplied decision/architecture docs.
    Each source is included under a '## Source: <path>' heading.
    Raises ValueError if any source is outside the configured allow-list."""


def render_system_state_md(
    benchmark_dir: Optional[Path] = None,
    agents_path: Optional[Path] = None,
    router_path: Optional[Path] = None,
    project_decisions_path: Optional[Path] = None,
    allow_list: Optional[Iterable[Path]] = None,
) -> str:
    """As before, but if project_decisions_path is provided and allowed,
    its content is appended as a '## Project Decisions' section."""


def sync_project_decisions() -> str:
    """One-call convenience: render the canonical project decisions
    from a small allow-listed source list and upsert it to the
    'Project Decisions & Architecture' knowledge collection as
    'project-decisions.md'. Returns the Open-WebUI file id."""
```

CLI entry points (for timers / post-doc-change hooks):

```bash
python -c "from services.open_webui.system_state_sync import sync; sync()"
python -c "from services.open_webui.system_state_sync import sync_project_decisions; sync_project_decisions()"
```

## 3. Safety classification

- **Classification:** side-effect (creates or replaces a knowledge file and reindexes a collection). Not destructive to user data or the safety spine.
- **Fail-closed behavior:** if Open-WebUI auth, upload, or processing fails, the script exits non-zero and leaves the existing knowledge file in place. Path validation raises `ValueError` for disallowed paths before any read or upload.
- **No safety-spine involvement:** the module does not call tool-daemon, registry, Sherpa, or guard rails.
- **Trust boundary:** only local files in the allow-list are read; the module must not accept remote URLs or arbitrary paths.

## 4. Test contract

- **Unit test:** `services/open_webui/test_system_state_sync.py`
  - Proves `render_project_decisions_md` concatenates a list of source files under per-source headings.
  - Proves `render_project_decisions_md` raises `ValueError` when a source is outside the allow-list.
  - Proves `render_system_state_md` includes the `project_decisions_path` content when provided.
  - Proves `sync_project_decisions` calls the renderer and returns the uploaded file id.

- **Adversarial test:** `tests/adversarial/test_project_decisions_path_escape.py`
  - Passes a malicious source path and asserts the renderer refuses to read outside the configured allow-list.
  - Must be written first and fail before the path validation is implemented, per Pillar VII TDD discipline.

## 5. Module placement

- **Module:** `services/open_webui/system_state_sync.py`
- **Why:** Co-locates with the existing Open-WebUI knowledge-sync adapter. The renderer and uploader for project decisions reuse the same path validation, sign-in, collection-lookup, and file-replacement primitives already built for `aios-system-state.md`.
- **Deep module boundary:** all source-file reading, Markdown assembly, and Open-WebUI REST details live behind the public functions in section 2. Callers only use `sync()` and `sync_project_decisions()`.

## 6. Out of scope

- Refreshing the content of the underlying source documents themselves (e.g. rewriting `docs/operations.md`). The PRD assumes a small, manually-curated source list. Content curation happens outside this feature; the feature only automates the render-and-sync pipeline.
- Changes to Open-WebUI upstream source code.
- Real-time, event-driven sync from document edits (timers/hooks are the first slice).
- Generalizing to other knowledge bases outside Open-WebUI.

## 7. Work-order link

- `core/state/proposed_work_orders/1a6ee2c5-5f9d-4a77-8f3e-8c6a9b0d7e4f.json`
