# HORI Slice Log

> The single source of truth for "where are we right now."
> Any new session reads this first (see AGENTS.md → Crash Recovery Protocol).

## Current Slice

(none — history squashed, ready for SLICE-05)

## Slice Queue

Proposed order (adjustable at any retro):

1. **SLICE-05: hori detect** — a command that detects hardware and recommends a model tier. Demo: run it, show output.
2. **SLICE-06: SQLite memory backend** — HORI works with zero external deps beyond Python. Demo: chat with HORI using SQLite instead of Qdrant.
3. **SLICE-07: README + LICENSE + CONTRIBUTING** — public repo ready. Demo: stranger can clone and understand the project.

## Completed Slices

### Pre-squash: De-personalization (SLICE-01 through SLICE-04)
- History was squashed to a single initial commit to remove personal data
  (conversation logs, IPs, hostnames, paths) from git history.
- All code, tests, scripts, and docs now use configurable paths via hori.yaml
  or auto-detect from the environment (Path.home(), script location, SUDO_USER).
- 290 unit tests, 121 adversarial tests, 141 integration tests pass.
- 2 integration failures are expected deployment drift (installed service files
  need reinstall after the path renames).

## Notes

- Branch: `rebrand/hori` (integration), slice branches to be created as `slice/NN-description`
- The soak period for the safety spine is considered complete (Tier 2+3 stable,
  92 adversarial tests, reboot survival fixed). The redistribution work is the
  new focus. The soak will be re-run on the redistributed codebase before HORI 2.0.
