# HORI Slice Log

> The single source of truth for "where are we right now."
> Any new session reads this first (see .devin/AGENTS.md → Crash Recovery Protocol).

## Current Slice

(None — ready to plan SLICE-MACOS-02)

## Slice Queue

1. SLICE-MACOS-02: HORI Feels Alive — Presence
2. SLICE-MACOS-03: Voice Conversation
3. SLICE-MACOS-04: Live Preview — First Taste of Emerging Software
4. SLICE-MACOS-05: The Workshop — Projects, Files, "Make It Yours"
5. SLICE-MACOS-06: The Emerging Canvas — Sims Builder Mode
6. SLICE-MACOS-07: The Koi, Menu Bar, Sound, Accessibility Audit
7. SLICE-MACOS-08: Install-Time Hardware Sensing
8. SLICE-MACOS-09: Phone Companion

## Completed Slices

### SLICE-MACOS-01: Native Text Conversation — COMPLETE
- Branch: `slice/macos-01-text-conversation` (merged to rebrand/hori pending)
- What was built: HoriClient (HTTP client for POST /v1/voice/chat —
  text only, audio ignored until Phase 3), ConversationView (scrollable
  message bubbles — user right/accent, HORI left/surface, spring
  animations, typing indicator), MessageInputView (multi-line input +
  send button bound to Cmd+Return), ConnectionSetupView (first-run
  sheet for aios-core URL with "Test Connection"), ContentView wired
  with send logic (user message → HORI reply → error banner on
  failure). 54 tests total (8 HoriClient + 12 ConversationView + 34
  existing).
- Surprises:
  1. `TestResult.success` collided with SwiftUI's `SensoryFeedback.success`
     (macOS 15) — fixed by making TestResult Equatable.
  2. `localizedDescription` returns String (non-optional) — optional
     chaining on it was invalid. Removed `?`.
  3. MockURLProtocol: URLProtocol instantiates a new instance per
     request, so instance properties can't be shared with the test.
     Moved all config to static vars. Also URLProtocol strips httpBody
     from POST requests — read it from httpBodyStream in startLoading().
  4. Swift Testing runs tests in parallel by default — shared static
     mock state leaked between tests. Added `.serialized` to the suite.
  5. EmptyStateView had `.frame(maxHeight: .infinity)` which greedily
     consumed all vertical space, pushing the input field off-screen.
     Removed `maxHeight: .infinity`.
  6. aios-core serves HTTP only (Tailscale Serve proxies HTTPS). The
     app uses HTTP over Tailscale — ATS is disabled via
     NSAllowsArbitraryLoads in Info.plist.
- Infrastructure: Set up SSH from GPU server to Mac via Tailscale
  (standard SSH key, not Tailscale SSH — the Mac GUI app doesn't
  support Tailscale SSH server). Devin can now build and test on the
  Mac directly via SSH over the Tailscale IP, eliminating the
  push/pull/paste loop. Visual demo still requires the product owner.
- Skipped: Streaming (uses /v1/voice/chat, not /stream — deferred).
  Audio (Phase 3). Connection settings accessible after first-run
  (needs a menu item or button — deferred to Phase 2+).
- Demo: Mac app → connection setup (enter aios-core Tailscale URL) →
  type "hello" + Cmd+Return → HORI replies in a bubble. Follow-up turn
  has context. 54 tests pass.

### SLICE-MACOS-00: First Impression + Foundation — COMPLETE
- Branch: `slice/macos-00-first-impression` (merged to rebrand/hori pending)
- What was built: The HORI macOS app skeleton — xcodegen project,
  design system (theme, typography, animations, shapes), five
  foundational decisions (accessibility, keyboard, localization,
  multi-window, undo), and the empty state view (koi + "What do you
  want to make today?"). 5 Swift test files (Theme, EmptyStateView,
  Accessibility, WindowState, UndoManager).
- Surprises: Six compile/test issues surfaced on first Mac build — all
  were API mismatches between the SDK the source was written against and
  macOS 15 Sequoia / Xcode 16:
  1. `Font.custom` has no overload taking both `weight:` and `relativeTo:`
     → chain `.weight()` as a modifier (app + test)
  2. `registerUndo(withTarget:handler:)` requires a class, not `Any`
     → shared no-op class instance as target
  3. `.toolbarVisibility` not a Scene modifier in this SDK → removed
     (`.windowStyle(.plain)` already gives transparent titlebar)
  4. `NSColorSpace.sRGBColorSpace` renamed to `.sRGB` → updated
  5. `UndoManager.groupsByEvent` defaults to true, causing all explicit
     undo groups to nest inside one event group → set to false so each
     register() is a top-level group (fixes chained undo)
  6. `.windowStyle(.plain)` dropped the traffic lights entirely on
     Sequoia (no close/minimize/zoom, no draggable titlebar) → removed,
     standard chrome restored. Transparent titlebar deferred to a later
     cosmetic refinement.
- Skipped: Transparent titlebar (needs AppKit window customization, not
  just .windowStyle(.plain) — deferred). DM Sans custom font not bundled
  yet (SF Pro fallback works).
- Demo: Mac build → 5 test files pass → Cmd+R → warm near-black window
  with koi + "What do you want to make today?", violet accent, standard
  window chrome, multi-window works (Cmd+N), VoiceOver navigable.
- Pre-push hook fix: Check 3 now uses --diff-filter=AM so deletions of
  sensitive files aren't flagged as additions (was blocking the push).

### SLICE-08: hori init setup wizard — COMPLETE
- Branch: slice/08-hori-init
- What was built: `hori/init.py` (setup wizard), `hori/test_init.py` (9 tests).
  `hori init` combines hardware detection + config creation: detects GPU/VRAM,
  recommends a model tier, writes `~/.config/hori/hori.yaml` with the recommended
  model and SQLite backend, creates `~/.local/share/hori/`, prints next steps.
  `--force` overwrites existing config, `--quiet` suppresses the report.
  Updated cli.py, README quickstart, CONTRIBUTING.md.
- Surprises: None. The detect module was already clean, so init was just
  orchestration + config writing + next-steps printing.
- Skipped: Nothing.
- Demo: Fresh clone → `pip install -e .` → `hori init` → working config
  with recommended model, data dir created, next steps printed.

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
