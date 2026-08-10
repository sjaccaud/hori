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
///
/// The presence SSE stream is owned by the app delegate, not per-window.
/// It starts at app launch and stops at app termination. Tying it to
/// `WindowGroup`'s `onAppear`/`onDisappear` caused the stream to be torn
/// down whenever any window disappeared (closed or reclaimed by SwiftUI),
/// which flipped the presence indicator to "Offline" for all windows
/// even though the chat endpoint (a separate one-shot HTTP request)
/// still worked. The stream is shared — one HORI, one presence — so its
/// lifetime must match the app, not a single window.
@main
struct HORIApp: App {

    /// Owns `SharedAppState` and the presence stream lifecycle.
    /// The delegate starts the stream at launch and stops it at
    /// termination; `SharedAppState` is injected into SwiftUI from here.
    @NSApplicationDelegateAdaptor(HoriAppDelegate.self)
    private var appDelegate

    var body: some Scene {

        // MARK: - Main Window Group

        WindowGroup {
            ContentView()
                .environment(appDelegate.sharedState)
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

// MARK: - App Delegate

/// Owns `SharedAppState` and manages the presence SSE stream lifetime.
///
/// The presence stream is shared across all windows (one HORI, one
/// presence), so it must live for the app's lifetime — not tied to any
/// single window's appear/disappear cycle. Starting it here at launch
/// and stopping it at termination keeps the indicator accurate even as
/// windows open and close.
///
/// `startPresenceStream()` is a no-op when the connection isn't
/// configured yet (first run); `ConnectionSetupView.save()` starts it
/// once the URL is set. Safe to call multiple times.
///
/// In Phase 7, the delegate also owns the MenuBarController (menu bar
/// presence with quick actions).
final class HoriAppDelegate: NSObject, NSApplicationDelegate {

    /// Shared state — one instance for the entire app.
    /// Connection config, project list, presence, settings.
    let sharedState = SharedAppState()

    /// Menu bar controller — HORI icon in the system menu bar.
    private var menuBarController: MenuBarController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        sharedState.startPresenceStream()

        // Start menu bar presence
        let mbc = MenuBarController(sharedState: sharedState)
        mbc.start()
        menuBarController = mbc
    }

    func applicationWillTerminate(_ notification: Notification) {
        sharedState.stopPresenceStream()
        menuBarController?.stop()
    }
}
