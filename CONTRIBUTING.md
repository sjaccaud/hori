# Contributing to HORI

HORI is built by a solo creator and an AI co-architect. This document
describes the workflow so any contributor (human or AI) can be productive
on the first commit.

## Slice-based development

Work is organized in **slices** — vertical pieces of working functionality
that can be demoed. A slice is defined by what you'll be able to *do*
after it's done, not what code changes.

### One branch per slice

```
rebrand/hori              ← integration branch (always working, always demoable)
├── slice/05-hori-detect  ← feature branch for a slice
├── slice/06-sqlite-memory
└── slice/07-readme-license
```

Each slice gets its own branch off `rebrand/hori`. When the slice is
complete and demoed, it merges back with `--no-ff`.

### Three-phase rhythm

```
PLAN → BUILD → DEMO
        ↑           │
        └── RETRO ──┘
```

1. **PLAN** — agree on what the slice does and the demo criterion
2. **BUILD** — TDD where safety is involved, tests where useful
3. **DEMO** — show working software, get feedback
4. **RETRO** — what did we learn? Adjust the next slice?

### Commit messages

```
[SLICE-NN] Short description of what changed
```

Commit at every natural stopping point (after a failing test, after
making it pass, after a refactor).

## Build & test

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .

# Run tests
make test              # all tests
make test-unit         # unit tests only (~30s)
make test-adversarial  # safety property tests
make test-integration  # cross-service contract tests
make test-stress       # smoke stress tests (~120s)

# Lint
make lint              # ruff check
```

## Code style

- **Python:** ruff for linting (E, F, I rules). Line length 88.
- **Type hints:** use them. `from __future__ import annotations` at the
  top of new files.
- **Docstrings:** every module explains *why* it exists, *what* it
  defends against (if safety-related), and *which source-of-truth
  document* it traces to.
- **Comments:** don't add or remove comments unless asked. If you
  accidentally delete one, put it back.
- **Dependencies:** check `requirements.txt` / `pyproject.toml` before
  adding a new dependency. Prefer the standard library. If you must add
  one, pin the version and prefer releases at least 7 days old.

## Safety-first testing

Safety properties use **adversarial TDD**: the test is written FIRST,
must FAIL, then the safety is built until it passes. See
`tests/adversarial/` for the existing suite.

The fundamental principle: **The LLM is untrusted. The architecture is
trusted.** Any change to the safety architecture — even slightly — gets
a dedicated conversation before implementation.

## Documentation

When code and docs diverge, that is a bug. If you change behavior, update
the relevant doc:

- Hardware/model config → `docs/stack.md`
- Runtime operations → `docs/operations.md`
- Project status → `docs/roadmap.md`
- Safety architecture → `docs/tool_safety.md`
- Current work status → `docs/SLICE_LOG.md`

## Crash recovery

The repo IS the state. No external tracking, no databases. To resume
after a crash or in a new session:

1. Read `docs/SLICE_LOG.md` → "where are we?"
2. `git branch` → "what branch are we on?"
3. `git log --oneline -5` → "what was the last commit?"
4. `git status` → "are there uncommitted changes?"
5. Run the tests → "does the code currently work?"

Never leave the code broken. Commit in a state where tests pass (or the
failing test is the next step, clearly marked `[RED]`).

## Questions?

- Read [AGENTS.md](AGENTS.md) for the full developer guide
- Read [docs/manifesto.md](docs/manifesto.md) for the project philosophy
- Read [docs/README.md](docs/README.md) for the documentation index
