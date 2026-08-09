# HORI macOS Surface

The native macOS app for HORI — a workshop where you make personal software through conversation.

**AI you own. No tokens or subscriptions necessary.**

## Build Requirements

- macOS 15.0+ (Sequoia)
- Xcode 16+
- [xcodegen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`)
- A running aios-core instance (on a GPU server via Tailscale, or local)

## Setup

```bash
# From the surfaces/macos/ directory:
xcodegen generate    # generates HORI.xcodeproj
open HORI.xcodeproj  # opens in Xcode
```

In Xcode: Cmd+R to build and run.

## Test

```bash
# From the surfaces/macos/ directory:
xcodegen generate
xcodebuild test -project HORI.xcodeproj -scheme HORI -destination 'platform=macOS'
```

Or run tests from Xcode: Cmd+U.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full architecture overview, design system, and foundational decisions.

## Project Structure

```
surfaces/macos/
├── project.yml              # xcodegen project specification
├── HORI/
│   ├── HORIApp.swift        # @main entry point, WindowGroup, commands
│   ├── ContentView.swift    # Root view per window
│   ├── Info.plist           # App metadata, permissions
│   ├── Localizable.xcstrings # Localization string catalog
│   ├── Assets.xcassets/     # Color sets, app icon
│   ├── Theme/
│   │   ├── HoriTheme.swift         # Color palette (warm dark/light)
│   │   ├── HoriTypography.swift    # Custom typeface (DM Sans)
│   │   ├── HoriAnimations.swift    # Spring curves + Reduce Motion
│   │   ├── HoriShapes.swift        # Corner radii (8/12/18px)
│   │   ├── HoriAccessibility.swift # VoiceOver/Reduce Motion helpers
│   │   └── HoriKeyboard.swift      # Keyboard shortcut registry
│   ├── State/
│   │   ├── SharedAppState.swift    # Shared across windows (config, projects, presence)
│   │   ├── WindowState.swift       # Per-window (conversation, preview, focus)
│   │   └── HoriUndoManager.swift   # Undo/Redo infrastructure
│   └── Views/
│       └── EmptyStateView.swift    # The first impression
└── Tests/
    ├── ThemeTests.swift            # Color palette correctness
    ├── EmptyStateViewTests.swift   # First impression view
    ├── AccessibilityTests.swift    # VoiceOver, Reduce Motion
    ├── WindowStateTests.swift      # Multi-window state isolation
    └── UndoManagerTests.swift      # Undo/Redo registration
```

## Connecting to HORI

The app connects to aios-core over Tailscale. In Phase 0, there's no
connection UI yet (that comes in Phase 1). The app shows the empty state
with the koi and "What do you want to make today?"

## Design System

- **Colors:** Warm dark (#0A0A0A background, not pure black) / warm off-white (#F5F5F7)
- **Accent:** Violet (#7C9EFF)
- **Typeface:** DM Sans (warm, modern, geometric-humanist)
- **Animations:** Spring curves (0.3/0.5/0.8s response, 0.8/0.825/0.9 damping)
- **Shapes:** 8px (small), 12px (medium), 18px (large) corner radii
- **Quality bar:** "2027 Product, Not Windows 95" — no default SwiftUI gray, no system font as default

## Foundational Decisions

These are architectural decisions that can't be grafted in later:

1. **Accessibility** — built into every view at creation time
2. **Keyboard-first** — every interaction has a keyboard equivalent
3. **Localization-ready** — all strings use LocalizedStringKey
4. **Multi-window** — per-window state (WindowState) + shared state (SharedAppState)
5. **Undo/Redo** — UndoManager wired in from Phase 0
