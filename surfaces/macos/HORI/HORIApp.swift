import SwiftUI
#if canImport(AppKit)
import AppKit
#endif

/// The HORI macOS app entry point.
///
/// This is a "2027 product" from the first window:
/// - Standard window chrome (traffic lights, draggable titlebar)
/// - Multi-window support (`WindowGroup`, not `Window`)
/// - Per-window state (`WindowState`) + shared state (`SharedAppState`)
/// - Per-window `UndoManager` injected via environment
///
/// The first thing you see is `ContentView`, which shows the
/// `EmptyStateView` — a warm dark window with a koi placeholder
/// and "What do you want to make today?"
@main
struct HORIApp: App {

    /// Shared state — one instance for the entire app.
    /// Connection config, project list, presence, settings.
    @State private var sharedState = SharedAppState()

    var body: some Scene {

        // MARK: - Main Window Group

        WindowGroup {
            ContentView()
                .environment(sharedState)
        }
        .commands {
            // Edit menu — Undo/Redo wired to the responder chain.
            // The UndoManager is injected via environment; SwiftUI
            // automatically connects menu items to the focused
            // window's UndoManager.
            CommandGroup(replacing: .undoRedo) {
                Button("Undo") {
                    NSApp.sendAction(#selector(UndoManager.undo), to: nil, from: nil)
                }
                .keyboardShortcut("z", modifiers: .command)

                Button("Redo") {
                    NSApp.sendAction(#selector(UndoManager.redo), to: nil, from: nil)
                }
                .keyboardShortcut("z", modifiers: [.command, .shift])
            }

            // File menu — New Window (system provides this by default
            // with WindowGroup, but we document it here).
            CommandGroup(after: .newItem) {
                // New project (Phase 5) and new window are different.
                // New window is handled by the system.
                // New project will be added here in Phase 5.
            }
        }
        .defaultSize(width: 900, height: 640)
        .windowResizability(.contentMinSize)
    }
}
