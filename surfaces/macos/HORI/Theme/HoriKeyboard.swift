import SwiftUI

/// HORI keyboard shortcut registry.
///
/// macOS is a keyboard-first platform. Every interaction has a
/// keyboard equivalent from Phase 1 onward. Shortcuts are defined
/// here centrally, not scattered across views, so they're easy
/// to discover, audit, and keep consistent.
///
/// This is a foundational decision — retrofitting keyboard support
/// later requires changing the view hierarchy and focus management.
enum HoriKeyboard {

    // MARK: - Conversation

    /// Cmd+Enter — send the current message.
    static let sendMessage = KeyboardShortcut("\r", modifiers: .command)

    // MARK: - Project

    /// Cmd+N — create a new project.
    static let newProject = KeyboardShortcut("n", modifiers: .command)

    /// Cmd+O — open an existing project.
    static let openProject = KeyboardShortcut("o", modifiers: .command)

    // MARK: - Window

    /// Cmd+W — close the current window.
    /// (Handled by the system by default, but documented here.)
    static let closeWindow = KeyboardShortcut("w", modifiers: .command)

    /// Cmd+Shift+H — open or focus the HORI window (global hotkey).
    /// Registered via KeyboardShortcuts library in Phase 7.
    static let focusHori = KeyboardShortcut("h", modifiers: [.command, .shift])

    // MARK: - Navigation

    /// Escape — dismiss the current flyout, popover, or sheet.
    static let dismiss = KeyboardShortcut(.escape, modifiers: [])

    /// Cmd+K — command palette (future, but the registry is ready).
    static let commandPalette = KeyboardShortcut("k", modifiers: .command)

    // MARK: - Preview

    /// Cmd+P — toggle preview pane visibility.
    static let togglePreview = KeyboardShortcut("p", modifiers: .command)

    /// Cmd+E — toggle between conversation and canvas focus.
    static let toggleFocus = KeyboardShortcut("e", modifiers: .command)
}
