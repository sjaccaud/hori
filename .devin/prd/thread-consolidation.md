# PRD — Thread Consolidation Loop (SLICE-17)

## 1. Intent trace

- **Pillar VI (memory that compounds):** episodes accumulate; this slice
  is what folds them back into curated state and surfaces staleness.
- **Pillar VII:** LLM output is untrusted — consolidation *proposes*,
  humans approve. Proposals can never self-apply.
- **Roadmap:** extends the Tier 1B consolidation PoC (Sleep & Dream) to
  the workstream layer; marked off-roadmap PoC alongside SLICE-15.
- **Approved scope (user decision 2026-09-15):** hybrid writes —
  mechanical state.md fields auto-update, prose changes go to a review
  queue; rule mining IS in scope (proposals only).

## 2. Interface contract

New module `services/aios_core/thread_consolidation.py`:

```python
REVIEW_ROOT: Path   # default ~/.local/share/hori/review/

def plan_mechanical_update(thread_dir: Path) -> dict
    # -> {"updated": "<date>"} from latest episode date; {} if none/newer

def apply_mechanical(thread_dir: Path, updates: dict) -> bool
    # Rewrites ONLY whitelisted state.md frontmatter keys
    # ({"updated"}). Body and all other keys preserved byte-for-byte.

def detect_stale(root: Path, sessions_log: Path, days: int = 7) -> list[dict]
    # Threads whose cwd saw sessions but no new episodes in window.

def write_proposal(review_dir: Path, *, kind: str, thread: str,
                   proposed: str, rationale: str,
                   sources: list[str]) -> dict
    # Always status: pending. kind ∈ {"state-prose", "rule"}.

def propose_state_update(thread_dir: Path, call_llm) -> dict | None
def mine_rule_proposals(root: Path, call_llm, limit: int = 3) -> list[dict]
    # LLM-assisted; call_llm is injected so tests never hit the network.
```

New script `scripts/thread_consolidation.py` (thin CLI wrapper):

- Iterates threads under `HORI_THREADS_ROOT`.
- Applies mechanical updates unconditionally (pure code, no LLM).
- If `/run/ai-power-manager/state` == `game`, or the LLM endpoint is
  unreachable → skips LLM stages, notes it in the report (fail-open).
- Writes a run report to `~/.local/share/hori/consolidation/<date>.md`.

New systemd units `services/systemd/aios-thread-consolidation.{service,timer}`
— OnCalendar `*-*-* 03:20`, `Persistent=true`, same `%i`/`%h`
install-script convention as `aios-consolidation`.

Review queue item format — `review/NNN-<kind>-<thread>.md` with
frontmatter `{kind, thread, created, status: pending, sources}` and the
proposed diff/text in the body. Applied by a human editing the target
file; no auto-apply path exists.

## 3. Safety classification

**Side-effect:** writes to curated `state.md` (mechanical keys only) and
the review dir. Not spine-touching, but it is an *automated writer over
curated memory* — full adversarial treatment:

- **Whitelisted keys:** `apply_mechanical` may only set `updated` —
  any other key in `updates` is rejected.
- **Proposals are inert:** `write_proposal` hard-sets `status: pending`
  regardless of input; proposal bodies are never executed or applied.
- **Confinement:** all writes resolve inside the threads root or the
  review dir; thread dirs are still slug-validated via `episodes.py`.
- **LLM is a collaborator, not an authority:** `call_llm` output feeds
  `write_proposal` only — it can never reach `apply_mechanical`.
- **Game mode:** LLM stages skip when the power manager says `game`.

## 4. Test contract

- **Unit:** `services/aios_core/test_thread_consolidation.py` —
  `test_mechanical_updates_only_updated_key`, `test_mechanical_preserves_body`,
  `test_plan_finds_latest_episode_date`, `test_detect_stale_flags_sessions_without_episodes`,
  `test_write_proposal_forces_pending`, `test_llm_down_skips_proposals_cleanly`.
- **Adversarial (WRITTEN FIRST):**
  `tests/adversarial/test_thread_consolidation_confined.py` —
  `test_mechanical_rejects_non_whitelisted_keys`,
  `test_proposal_status_cannot_be_overridden`,
  `test_llm_output_never_writes_to_state_md`,
  `test_no_writes_outside_threads_and_review`.
  Defends curated state.md + filesystem against an automated writer
  consuming untrusted LLM output. Pillar VII.
- **Integration:** none new — the job is exercised by a manual service
  run after install; timer verified via `systemctl list-timers`.

## 5. Module placement

Core logic in `services/aios_core/thread_consolidation.py` (importable,
unit-testable); `scripts/thread_consolidation.py` is the thin CLI used
by the systemd unit. Review queue lives under the threads data dir, not
the repo (`~/.local/share/hori/review/`).

## 6. Out of scope

- Auto-applying proposals (never — that is the invariant).
- Semantic/embedding retrieval over episodes (Slice D).
- Rewriting `memory_consolidation.py` (this is a sibling, not a merge).
- MCP exposure of the review queue (a `proposal_list` tool could come
  later once the format proves out).

## 7. Work-order link

`core/state/proposed_work_orders/7c2b4d6e-1a3f-4b5c-9d8e-0f1a2b3c4d5e.json`
