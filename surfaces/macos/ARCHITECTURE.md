# HORI macOS Surface — Architecture

## Overview

The HORI macOS app is a native SwiftUI application that serves as the
"creation surface" for HORI — a workshop where you make personal software
through conversation, with the software emerging live behind the conversation.

The app is a thin client that connects to the existing aios-core backend
(running on a GPU server via Tailscale). The Mac handles all file I/O and
UI; HORI (aios-core) handles the intelligence, memory, and safety.

## Architecture Principles

### Lean UX — UX Leads, Architecture Follows

Each phase is defined by a UX moment (what you see and feel). Architecture
is built just-in-time to make the UX moment real. No upfront infrastructure
that isn't needed for the current UX moment.

### Quality Bar — "2027 Product, Not Windows 95"

The design system is established in Phase 0 and applied from the first pixel.
No default SwiftUI gray, no system font as default, spring animations from
day one, rounded corners everywhere.

### Foundational Decisions — Can't Be Grafted In Later

1. Accessibility (VoiceOver, Reduce Motion, keyboard nav)
2. Keyboard-first interaction (shortcut registry)
3. Localization-ready strings (LocalizedStringKey)
4. Multi-window architecture (shared vs. per-window state)
5. Undo/Redo (UndoManager wired from Phase 0)

## Design System

### Colors

| Role | Dark Mode | Light Mode |
|---|---|---|
| Background | #0A0A0A (warm near-black) | #F5F5F7 (warm off-white) |
| Surface | #171717 (elevated) | #FFFFFF |
| Text | #FAFAFA | #1D1D1F |
| Text Secondary | #A3A3A3 | #86868B |
| Border | white @ 10% | black @ 8% |
| Accent | #7C9EFF (violet) | #7C9EFF |
| Idle | #34C759 (green) | |
| Thinking | #FF9800 (orange) | |
| Error | #FF3B30 (red) | |

Colors are defined in `Assets.xcassets` as color sets with dark/light
variants, with programmatic fallbacks in `HoriTheme.swift` for tests
and previews.

### Typography

- **Typeface:** DM Sans (custom, warm, geometric-humanist)
- **Fallback:** SF Pro (system default)
- **Monospace:** SF Mono (for code display, later phases)
- **Weights:** regular (body), semibold (headers/labels), bold (display)

| Style | Size | Weight | Usage |
|---|---|---|---|
| display | 24pt | semibold | Empty state prompt, large titles |
| header | 17pt | semibold | Section headers, project names |
| body | 15pt | regular | Message content, general text |
| label | 13pt | semibold | Buttons, field labels |
| caption | 12pt | regular | Timestamps, hints, tagline |

### Animations

All animations use spring curves. No linear animations.

| Curve | Response | Damping | Usage |
|---|---|---|---|
| snappy | 0.3s | 0.8 | Buttons, hovers, small feedback |
| balanced | 0.5s | 0.825 | Transitions, message bubbles |
| dramatic | 0.8s | 0.9 | Modals, canvas focus switches |

When Reduce Motion is enabled, all springs are replaced with linear
ease-in-out fades (0.2-0.3s).

### Shapes

| Size | Radius | Usage |
|---|---|---|
| small | 8px | Buttons, input fields, tags |
| medium | 12px | Cards, message bubbles, panels |
| large | 18px | Windows, sheets, containers |

## State Architecture

### SharedAppState (one instance, all windows)

- `aiosCoreURL` — connection config (stored in UserDefaults)
- `projects` — project list (Phase 5)
- `presence` — HORI's presence state (Phase 2)
- `feedbackSoundsEnabled` — settings

### WindowState (one instance per window)

- `messages` — conversation history
- `isSending` — sending state
- `previewHTML` — current preview content (Phase 4)
- `previewVisible` — preview pane visibility
- `canvasFocus` — conversation vs. canvas focus (Phase 6)
- `currentProject` — the open project (Phase 5)
- `connectionState` — per-window connection state
- `inputFocus` — current keyboard focus target

### UndoManager (one instance per window)

Injected via `@Environment(\.horiUndoManager)`. Every state mutation
in a view model registers an undo action via `HoriUndoManager.register()`.

## Window Architecture

The app uses `WindowGroup` (not `Window`) to support multiple windows.
Each window gets its own `WindowState` (created as `@State` in
`ContentView`). `SharedAppState` is created once in `HORIApp` and
injected via `.environment()`.

Window chrome: `.windowStyle(.plain)` + `.toolbarVisibility(.hidden)`
for a transparent titlebar with content extending to edges. Traffic
light buttons (close/minimize/maximize) remain as macOS convention.

## Keyboard Shortcuts

Defined centrally in `HoriKeyboard.swift`:

| Shortcut | Action |
|---|---|
| Cmd+Enter | Send message |
| Cmd+N | New project (Phase 5) |
| Cmd+O | Open project (Phase 5) |
| Cmd+W | Close window (system) |
| Cmd+Shift+H | Focus HORI window (Phase 7, global) |
| Escape | Dismiss flyout/popover/sheet |
| Cmd+K | Command palette (future) |
| Cmd+P | Toggle preview (Phase 4) |
| Cmd+E | Toggle conversation/canvas focus (Phase 6) |

## Accessibility

- Every interactive element has `.accessibilityLabel()` and `.accessibilityHint()`
- Every animation checks `@Environment(\.accessibilityReduceMotion)` and provides linear fallbacks
- Color is never the only signal — presence states have shapes and text, not just colors
- VoiceOver can navigate the entire app without a mouse
- Keyboard navigation works for every interaction (Tab, arrows, Enter, Escape)
- Dynamic Type and High Contrast are supported

## Backend Connection

The app connects to aios-core via HTTP over Tailscale. No backend changes
are needed for Phases 0-8 (the app uses existing endpoints):

| Endpoint | Method | Phase | Usage |
|---|---|---|---|
| /v1/voice/chat | POST | 1 | Text conversation |
| /v1/presence | SSE | 2 | Presence monitoring |
| /v1/voice/chat | POST | 3 | Voice (text + audio) |
| /v1/voice/chat/stream | POST | 3+ | Streaming (if needed) |
| /v1/audio/voices | GET | 3 | Available voices |

Phase 9 (phone companion) adds new endpoints: /v1/summary, /v1/capture,
/v1/approvals.

## File Ownership

Files live on the Mac at `~/HORI/projects/{slug}/`. The Mac app handles
all file I/O. HORI (aios-core) generates code in conversation; the Mac
app extracts and saves it. This aligns with "make it yours" and avoids
dependency on HORI 2.0 write tools (gated on the 2-week soak).

## Capability Topology

A living map of what HORI SHOULD, COULD, COULDN'T, SHOULDN'T, and WON'T
do. Lives at `surfaces/macos/capability_topology.yaml` (created in
Phase 5). Used by:
- The crafter (human-readable generated Markdown)
- HORI (AI-readable, included in conversation context)
- Us (keeps building focused on actual capabilities)

The topology drives guidance flyouts in the canvas (Phase 6) — the
"No Wrong Notes" principle from GarageBand, made visible.

## Baked-In Rigor

HORI bakes in the same engineering discipline used to build HORI itself:
- Generated tests (every project includes test.js)
- Documented code (comments on every file, README in every project)
- Safety properties surfaced (dry-run mode for risky operations)
- Converse first, build second (HORI asks questions before generating)
- Structure visible (StructureView shows the shape of the project)
- Project log (hori.json records what was built, when, why)
- Uncertainty tracked (uncertainties field in hori.json)
