# HORI - Agent & Developer Guide

## Documentation

**Read docs/README.md first.** It is the single source of truth for all
HORI documentation, with a conflict resolution hierarchy.

Key documents:
- `docs/stack.md` - Current hardware, software, model config (canonical)
- `docs/operations.md` - Runtime operational details, build/test commands
- `docs/roadmap.md` - Project status and planned work (6 tiers, 156 PoCs + 11 STRAT items, 99 complete)
- `docs/tool_safety.md` + `docs/tool_safety_redteam.md` - Safety architecture
- `docs/manifesto.md` - Mission and philosophy (7 pillars, incl. Engineering Discipline)

If docs conflict, `docs/stack.md` and `docs/operations.md` win for technical
configuration. `docs/manifesto.md` is philosophy, not current config.

## Hardware Detection & Setup

```bash
hori init      # setup wizard: detect hardware + create config + create data dir
hori detect    # detect hardware and recommend a model tier (no config changes)
```

`hori init` combines hardware detection with config creation. It detects
GPU/VRAM, recommends a model tier, writes `~/.config/hori/hori.yaml` with
the recommended model and SQLite memory backend, creates
`~/.local/share/hori/`, and prints next steps. Use `--force` to overwrite
an existing config.

`hori detect` just shows the detection report without writing anything.

## Build & Test

```bash
make test              # all tests (unit + integration + regression + adversarial)
make test-unit         # unit tests only
make test-integration  # integration tests only
make test-regression   # regression tests only
make test-stress       # stress smoke tests (10-turn entropy + safety, ~120s)
make test-stress-entropy   # entropy/drift stress smoke (chained conversation)
make test-stress-safety    # safety stress smoke (hallucination bait + Sherpa)
```

Stress tests live in `tests/stress/`:
- `test_ten_thousand_turns.py` — entropy & context drift (chained turns,
  planning prompts, recall quality metrics). Supports 100/1K/10K turns.
  Recall quality uses a deflection detector covering both contracted
  ("I don't know") and uncontracted ("I do not know", "I cannot see")
  forms. See PoC 13.7 and docs/elastic_context_window.md for the
  deflection mitigation work.
- `test_safety_stress.py` — safety (stateless turns, both endpoints,
  hallucination bait + injection + Sherpa triggers). Supports both
  `/v1/chat/completions` and `/v1/voice/chat`.
- The admin panel can run smoke tests inline and start background runs
  for longer tests via the Stress Runner section.
- Integration tests (`make test-integration`) include:
  - The Sherpa wire contract test (`test_sherpa_wire_contract.py`) which
    runs the actual Go binary against real Python AuditLogger entries.
    This catches cross-language contract mismatches that unit tests miss.
  - The reboot survival test (`test_reboot_survival.py`) which verifies
    the tool daemon's `systemd-tmpfiles` workspace-recreation fix in three
    layers: 4 repo static tests (check the repo tmpfiles.d config and
    service file, including a regression guard against the broken
    `ExecStartPre` approach), 5 installed static tests (check the deployed
    files at `/etc/systemd/system/` and `/etc/tmpfiles.d/` match the repo
    — catches "committed but not deployed"), and 2 privileged tests
    (require root) that simulate a reboot by wiping `/tmp/hori-workspace`
    + stale socket, running `daemon-reload` + `systemd-tmpfiles --create`,
    and confirming the service recreates the workspace and serves tool
    calls. The privileged tests skip gracefully when not root. Run the
    full suite with: `sudo env PYTHONPATH=. ./venv/bin/python3 -m pytest
    services/integration_tests/test_reboot_survival.py -v`

## Post-Reboot Health Check

After every reboot, run:
```bash
sudo scripts/hardening/post_reboot_health.sh
```
This verifies functional health (socket reachable, Sherpa not blind,
tool daemon end-to-end call), not just process health. Created after
the Aug 8 2026 incident where both the tool daemon and Sherpa were
broken post-reboot but appeared healthy to systemd.

## Running Services

```bash
# aios-core (the intelligence layer, port 5680) — systemd service
sudo systemctl restart aios_core   # note: underscore, not hyphen
sudo systemctl status aios_core
sudo journalctl -u aios_core -f    # live logs

# llama-server is a systemd service (port 8080)
systemctl status llamacpp

# Embedding server (port 8081, CPU-only, start manually)
llama-server --model ~/ai-models/nomic-embed-text-v1.5.Q8_0.gguf --host 127.0.0.1 --port 8081 --ctx-size 2048 --embedding --n-gpu-layers 0
```

## Current Model

Qwen3.6-27B IQ4_NL on llama.cpp (Spiritbuun fork) with TurboQuant turbo4 KV
cache. See `docs/stack.md` and `docs/operations.md` for full configuration.

## Safety

The safety spine (Tier 2, HORI 1.5) is COMPLETE and deployed. The LLM now
has access to 4 read-only filesystem tools (list_dir, read_file, count_files,
search_files) through a multi-layer defense:

1. **Kernel** (Landlock + seccomp) — filesystem/network/syscall isolation
2. **Registry** — only 4 read-only tools exist, no dynamic registration
3. **Validation** — path traversal, symlink escape, prefix matching
4. **Guard rails** — rate limiting, emergency stop, response verification
5. **Sherpa** — Go behavioral guardian, fail-closed if it dies

Tool-augmented voice chat (Tier 3, PoC 16.1) is implemented: the LLM can
call tools via JSON tool_call objects, results are fed back for natural
language responses. See `docs/tool_safety.md` for the architecture and
`docs/roadmap.md` for the tier structure.

The fundamental principle: **The LLM is untrusted. The architecture is
trusted.** The LLM proposes; the architecture executes; the user confirms.
The Sherpa watches the pattern.

**Gate status (HORI 1.6 → 2.0):** Tier 3 is deployed. The 2-week soak is
in progress. Verify gate criteria with `sudo scripts/audit_review.py --gate`
or via the admin panel at `http://<your-tailscale-ip>:5680/admin` (Tailscale only).
The hallucination interception rate is now measurable from the safety
events log (every `verify_and_log()` call emits a `response_verified`
event). The admin panel can restart services (requires
`scripts/hardening/setup_admin_sudoers.sh` to be run once).

## Engineering Discipline

Per Manifesto Pillar VII:
- **TDD for safety properties:** adversarial tests are written FIRST, must
  fail, then the safety is built until they pass. See `tests/adversarial/`
  (92 tests, Tier 2F complete).
- **Documentation stays in sync with code:** every module docstring explains
  *why* it exists, *what* it defends against, and *which source-of-truth
  document* it traces to. When code and docs diverge, that is a bug.

## Devin Skills

Project-scoped skills live in `.devin/skills/<name>/SKILL.md` (committed to
git). They operationalize Manifesto Pillars VI/VII and emit artifacts in the
native intent-hierarchy shape rather than generic PRDs.

- `/grill-me` — adversarial design interview; refuses to code until a shared
  design concept is reached, then writes a proposed work order under
  `core/state/proposed_work_orders/`. User-trigger only (intentional friction).
- `/write-prd` — produces `docs/prd/<slug>.md` with an interface contract +
  test contract, linked to a work order. Delegates implementation to `/tdd`.
- `/tdd` — strict red/green/refactor loop; generalizes the adversarial-TDD
  discipline to all code. Uses Makefile targets, never ad-hoc pytest.
- `/ubiquitous-language` — read-only subagent that scans docs + code and
  reports proposed updates to `docs/ubiquitous_language.md` (the canonical
  glossary; fills a prior gap). Edits applied by the caller after review.

`/improve-architecture` (deep-module refactoring) was deliberately **not**
added: it tensions with Pillar VII's "Simplicity as Security / build less."
Apply deep-module refactors manually on a case-by-case basis (e.g. the
`aios_core/main.py` monolith) rather than via a standing auto-triggered skill.

## Web Surfaces

All web surfaces are served via **Tailscale Serve** (Tailnet-only HTTPS).
Funnel (public internet) is OFF — nothing is exposed outside the Tailnet.
This gives iOS the HTTPS it needs for the Web Speech API while keeping
everything private. Verify with `./scripts/hardening/audit_funnel.sh`.

- **Voice app:** `https://<your-tailnet>.ts.net/voice` (streaming SSE, mobile-first)
- **Chat app:** `https://<your-tailnet>.ts.net/chat` (keyboard-first text, MacBook typing surface)
- **Presence SSE:** `https://<your-tailnet>.ts.net/v1/presence` (ambient presence stream)
- **Admin panel:** `https://<your-tailnet>.ts.net/admin` (system vitals,
  tests, logs, service control — requires `scripts/hardening/setup_admin_sudoers.sh`
  for service restart buttons AND admin auth token. All `/admin/api/*`
  endpoints require a bearer token (`HORI_ADMIN_TOKEN` in
  `/etc/hori/secrets.env`). Fail-closed: if the token is not set, the
  admin API returns 403. The setup script generates the token
  automatically; restart aios_core after running it.)
- Apple Shortcut setup at `docs/apple_shortcut_setup.md` (streaming deep-link with `?autolisten=1`, or non-streaming WAV fallback)
- Default voice: bf_emma (Kokoro-82M TTS, #1 TTS Arena, 24kHz, CPU)

**Tailscale Serve paths** must be explicitly proxied. Current paths:
`/voice`, `/chat`, `/admin`, `/admin/api`, `/health`, `/static`,
`/v1/voice/chat`, `/v1/voice/chat/audio`, `/v1/voice/chat/stream`,
`/v1/chat/completions`, `/v1/presence`, `/v1/wake`. Add new paths with:
`tailscale serve --bg --set-path /path http://localhost:5680/path`

## Working Agreement

HORI is built by a solo creator (product owner) and an AI co-architect
(Devin). This section defines how they collaborate. It is binding across
sessions — any new Devin instance reads this on startup and follows it.

### Slice-based, not task-based

Work is planned in **slices** — vertical pieces of working functionality
that the product owner can interact with. A slice is defined by what
you'll be able to *do* after it's done, not what code changes.

Each slice has a **demo criterion** — the thing shown to the product owner
when it's done. If it can't be demoed in a way the product owner can
interact with or observe, it's not a slice. It's a subtask inside a slice.

### Three-phase rhythm per slice

```
PLAN → BUILD → DEMO
        ↑           │
        └── RETRO ──┘
```

**PLAN (product owner + Devin, 10-15 min):**
- Devin proposes the next slice in terms of what the product owner will
  be able to do
- Product owner says yes/no/adjust
- Devin names the demo criterion
- Devin flags what's uncertain
- Agree on the "this might not work" zone

**BUILD (Devin, alone, variable time):**
- TDD where safety is involved, tests where useful
- Commit as work progresses, merge to `rebrand/hori` only after demo
- If something unexpected happens — test won't pass, approach doesn't
  work, dependency is broken — STOP and bring it to the product owner
  rather than pushing through silently
- Write a `docs/SLICE_LOG.md` entry: what was built, what surprised,
  what's unsure, what was skipped

**DEMO (product owner + Devin, 15-30 min):**
- Show the working slice — product owner interacts with it or observes
- Not a code review. A demo. "Here's what happens when you run this."
- Product owner gives product feedback: does this feel right?
- Devin gives engineering reality: what's solid, what's fragile, what's
  a hack

**RETRO (product owner + Devin, 10 min):**
- What did we learn? What surprised us? Should we adjust the next slice?
- This is where the product owner steers — not on code, but on direction.

### When Devin stops and brings the product owner in

- **A slice is complete** — always. No exceptions. Show the demo.
- **About to change the safety architecture** — even slightly. The safety
  spine is what makes HORI different. Any change gets a dedicated
  conversation.
- **About to change direction** — if the plan needs to pivot, bring it
  to the product owner first.
- **Need product judgment** — "should the setup wizard be CLI or web?"
  is a product question, not an engineering one.
- **Something surprised that affects the product** — not internal
  architecture surprises, but things that change what the product owner
  will experience or need to decide.

### When Devin proceeds autonomously (relaxed HITL)

For architectural slices where the product owner can't critically
evaluate the tradeoffs (config systems, de-personalization, dependency
management, refactors that don't change behavior), Devin can:

- Skip the PLAN approval step and proceed directly to BUILD
- Commit and merge without explicit sign-off
- Report results in a batch (demo + retro together) rather than
  stopping at each checkpoint

Devin still stops for: safety architecture changes, direction changes,
product judgment questions, and anything that affects the product owner's
experience.

### When Devin doesn't stop

- Individual commits within a slice
- Refactors that don't change behavior
- Test additions
- Documentation updates
- Dependency version bumps
- Fixing a typo

### Show, don't tell

Devin will not describe code to the product owner. Devin will show
working software. If something hasn't been verified yet, say "I haven't
verified this yet" rather than describing what it should do in theory.

## Crash Recovery Protocol

Devin sessions can crash mid-work. This protocol ensures we recover
quickly with minimal loss. It is git-native — no external state, no
database, just git history and a few committed files.

### One branch per slice

```
rebrand/hori              ← integration branch (always working, always demoable)
├── slice/01-requirements ← feature branch for slice 1
├── slice/02-config       ← feature branch for slice 2
└── slice/03-depersonalize ← feature branch for slice 3
```

Each slice gets its own branch off `rebrand/hori`. Work happens on the
slice branch. When the slice is complete and demoed, it merges back to
`rebrand/hori`. If Devin crashes mid-slice, the damage is isolated to
the slice branch — `rebrand/hori` is untouched and still works.

### Commit every meaningful step

Within a slice, commit at every natural stopping point:
- After writing a failing test
- After making the test pass
- After a refactor
- After a file is changed

Commit messages start with the slice ID:
```
[SLICE-01] Add hori.yaml schema, replace ~/ai-models in config.py
[SLICE-01] Replace hardcoded personal references in main.py
```

### Session state file: docs/SLICE_LOG.md

The single source of truth for "where are we right now." Lives in the
repo, committed, survives crashes. Any new session reads this first.

### Resume protocol (run at the start of every new session)

1. Read `docs/SLICE_LOG.md` → "where are we?"
2. `git branch` → "what branch are we on?"
3. `git log --oneline -5` → "what was the last commit?"
4. `git status` → "are there uncommitted changes?"
5. Run the tests → "does the code currently work?"

This takes 30 seconds. No chat history needed. No memory of the previous
session needed. The repo IS the state.

### Never leave the code broken

Commit in a state where:
- Tests pass (or the failing test is the next step, clearly marked `[RED]`)
- The code runs (even if the feature is incomplete)
- The commit message says what's done and what's not

If a crash happens mid-edit, the worst case is uncommitted changes in
the working directory (recoverable with `git stash` or `git checkout`).
The last commit is in a working state.

### Slice sizing

Slices are sized to complete in one session. If a slice is too big,
break it into sub-slices. Better to ship 3 small slices than crash
midway through one big one.

### Uncertainty register: docs/uncertainty.md

A living document tracking things Devin is not sure about. Not bugs,
not TODOs — genuine unknowns. The product owner can read it anytime and
weigh in. It's the product owner's window into the parts of the project
that aren't settled yet.
