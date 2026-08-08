# HORI Slice Log

> The single source of truth for "where are we right now."
> Any new session reads this first (see AGENTS.md → Crash Recovery Protocol).

## Current Slice

(none — SLICE-07 complete, repo is public-ready)

## Slice Queue

Proposed order (adjustable at any retro):

1. **SLICE-08: Setup wizard** — `hori init` command that creates config,
   detects hardware, and writes hori.yaml automatically. Demo: fresh
   clone → `hori init` → working config.

## Completed Slices

### SLICE-07: README + LICENSE + CONTRIBUTING — COMPLETE
- Branch: slice/07-readme-license
- What was built: Root `README.md` (project overview, quickstart,
  architecture diagram, project structure, testing commands),
  `LICENSE` (Apache-2.0), `CONTRIBUTING.md` (slice workflow, build/test,
  code style, safety-first testing, crash recovery). Updated
  `docs/README.md` to point at the root README.
- Surprises: None. Straightforward documentation slice.
- Skipped: Nothing.
- Demo: A stranger can clone the repo, read the README, and understand
  what HORI is, how to install it, and how to run it.

### SLICE-06: SQLite memory backend — COMPLETE
- Branch: slice/06-sqlite-memory
- What was built: `hori/sqlite_memory.py` (SQLite backend with cosine similarity),
  `hori/test_sqlite_memory.py` (24 tests). Refactored `services/aios_core/memory.py`
  to dispatch to either Qdrant or SQLite backend based on `memory.backend` config.
  Updated `intent_graph.py` to use `scroll_all()` instead of direct Qdrant client.
  Updated `_retrieve_memory_batch` in main.py to use the backend-agnostic API.
  Added `memory.backend` to `hori/config.py` and `config.reference.yaml`.
- Surprises: The memory.py refactor was straightforward — the interface was already
  clean. The main.py `_retrieve_memory_batch` was directly importing qdrant_client,
  which needed to be refactored to use the public `retrieve_memory` API instead.
  The intent_graph.py `build_from_qdrant` had nested loop indentation that needed
  careful untangling when switching from scroll() to scroll_all().
- Skipped: `memory_consolidation.py` still uses Qdrant directly — it's a standalone
  script, not part of the hot path. Will be updated when consolidation is refactored.
- Demo: Set `memory.backend: sqlite` in hori.yaml, chat with HORI — memories stored
  and retrieved from `~/.local/share/hori/memory.db` with no Qdrant running.

### SLICE-05: hori detect — COMPLETE
- Branch: slice/05-hori-detect
- What was built: `hori/detect.py` (hardware detection + model tier recommendations),
  `hori/cli.py` (CLI entry point), `hori/test_detect.py` (25 tests).
  Detects AMD (ROCm via rocm-smi or DRM sysfs fallback), NVIDIA (nvidia-smi),
  Apple Silicon (sysctl), and CPU-only. Recommends one of 5 model tiers
  (heavy/medium/light/micro/nano) based on VRAM, outputs a hori.yaml snippet.
- Surprises: rocm-smi's `--showproductname` doesn't include VRAM — needed a
  separate `--showmeminfo vram` call. The kfd sysfs path has no properties
  on this kernel, but DRM sysfs (`/sys/class/drm/cardN/device/mem_info_vram_total`)
  works perfectly. The GPU name key in rocm-smi JSON is "Card Series" (capital S).
- Skipped: Nothing.
- Demo: `python -m hori.detect` → detects Radeon AI PRO R9700 (31.9GB VRAM),
  recommends heavy tier (Qwen3.6-27B).

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
