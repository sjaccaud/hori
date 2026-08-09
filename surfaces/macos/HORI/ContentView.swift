import SwiftUI

/// The root content view for each HORI window.
///
/// In Phase 0, this shows the `EmptyStateView` — the first impression.
/// In Phase 1, this will show `ConversationView` when a conversation
/// is active, and `EmptyStateView` when it's not.
///
/// Receives `WindowState` (per-window) and `SharedAppState` (shared)
/// via `@Environment`. The background is always `HoriTheme.background`
/// — warm dark, never default SwiftUI gray.
struct ContentView: View {

    @Environment(SharedAppState.self) private var sharedState
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Per-window state. Each window gets its own instance.
    /// This is a foundational decision: multi-window architecture
    /// with per-window state from Phase 0.
    @State private var windowState = WindowState()

    /// Per-window UndoManager. Each window gets its own undo stack.
    /// This is a foundational decision: Undo/Redo wired from Phase 0.
    @State private var undoManager = UndoManager()

    var body: some View {
        ZStack {
            // Warm background — never default SwiftUI gray.
            HoriTheme.background(for: colorScheme)
                .ignoresSafeArea()

            // Phase 0: just the empty state.
            // Phase 1 will add: if windowState.messages.isEmpty {
            //   EmptyStateView()
            // } else {
            //   ConversationView()
            // }
            EmptyStateView()
        }
        .frame(minWidth: 600, minHeight: 400)
        .environment(windowState)
        .environment(\.horiUndoManager, undoManager)
    }
}
