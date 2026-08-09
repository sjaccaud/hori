# Contributing to HORI

HORI is built by a solo creator and an AI co-architect. This document
is the public developer guide — how to build, test, and contribute code
that matches the project's conventions.

## Build & test

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .

# Run tests
make test              # all tests (unit + integration + regression + adversarial)
make test-unit         # unit tests only (~30s)
make test-adversarial  # safety property tests
make test-integration  # cross-service contract tests
make test-regression   # regression tests
make test-stress       # smoke stress tests (~120s)

# Lint
make lint              # ruff check
```

Stress tests live in `tests/stress/`:
- `test_ten_thousand_turns.py` — entropy & context drift (chained turns,
  planning prompts, recall quality metrics). Supports 100/1K/10K turns.
- `test_safety_stress.py` — safety (stateless turns, both endpoints,
  hallucination bait + injection + Sherpa triggers).

Integration tests include the Sherpa wire contract test (runs the actual
Go binary against real Python AuditLogger entries) and the reboot survival
test (verifies the tool daemon workspace-recreation fix). Run the full
reboot suite with:
```bash
sudo env PYTHONPATH=. ./venv/bin/python3 -m pytest \
    services/integration_tests/test_reboot_survival.py -v
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
`tests/adversarial/` for the existing suite (92 tests).

The fundamental principle: **The LLM is untrusted. The architecture is
trusted.** Any change to the safety architecture — even slightly — gets
a dedicated conversation before implementation. See `docs/safety.md` for
the full architecture and adversarial analysis.

## Documentation

When code and docs diverge, that is a bug. If you change behavior, update
the relevant doc:

- Hardware/model/inference config → `docs/operations.md`
- Project status → `docs/roadmap.md`
- Safety architecture → `docs/safety.md`
- Mission and philosophy → `docs/manifesto.md`

See `docs/README.md` for the full documentation index and conflict
resolution hierarchy.

## macOS App (surfaces/macos/)

The macOS app is built with Xcode on a Mac (not on the GPU server).
Source files live in the repo and are pulled to the Mac for building.

```bash
# On the Mac, from surfaces/macos/:
brew install xcodegen          # one-time setup
xcodegen generate              # generates HORI.xcodeproj from project.yml
open HORI.xcodeproj            # open in Xcode
# Cmd+R to build and run, Cmd+U to test

# Command-line build and test:
xcodebuild test -project HORI.xcodeproj -scheme HORI -destination 'platform=macOS'
```

The macOS app uses Swift Testing framework (built into Xcode 16+).
Tests live in `surfaces/macos/Tests/`.

## Public Repo Hygiene

This is a **public** repository. Before committing, ask: "Would I be
comfortable with this on the front page of Hacker News?"

### Never commit

- **Patent applications or patentability analyses** — public disclosure
  can destroy patent novelty. Keep these locally or in a private repo.
- **Secrets** — API keys, tokens, passwords, private keys, .env files.
  Use `/etc/hori/secrets.env` (root-owned, not in the repo).
- **Personal information** — real names (outside attribution), home
  paths, personal email addresses, real IP addresses, real hostnames,
  Tailscale machine names.
- **Internal infrastructure details** — network topology, real device
  IPs, internal service URLs with real hostnames.

### Use placeholders, not real values

- Tailscale URLs: `<your-tailnet>.ts.net` (not your real tailnet name)
- IPs: `100.64.0.0/10` (the Tailscale CGNAT range, not your real IP)
- Paths: `~/HORI/` or `~/.config/hori/` (not `/home/yourname/`)

### Pre-push hook

A pre-push hook (`.git/hooks/pre-push`) scans commits for PII patterns
and sensitive files. It will block pushes that contain:

- Known personal identifiers (names, emails, real IPs, tailnet name)
- Sensitive file patterns (`docs/patent*`, `.env`, `*.key`, `*.pem`)
- Internal-only docs in public paths (`docs/SLICE_LOG`, `docs/uncertainty`,
  `docs/ubiquitous_language`, `docs/prd/`, `core/intent/`)
- Blocked branches (`archive/*`, `dev`, `pre-rebrand`)

To bypass (DANGEROUS, not recommended): `git push --no-verify`

### When in doubt

Don't commit it. Keep it local. Ask later. The cost of accidentally
publishing sensitive information is much higher than the cost of
delaying a commit.

## Questions?

- Read [docs/README.md](docs/README.md) for the documentation index
- Read [docs/manifesto.md](docs/manifesto.md) for the project philosophy
- Read [docs/safety.md](docs/safety.md) for the safety architecture
