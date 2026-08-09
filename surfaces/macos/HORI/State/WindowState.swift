import SwiftUI

/// Per-window state for the HORI app.
///
/// This is a foundational decision: macOS is a multi-window platform.
/// State is split into shared (connection config, project list) and
/// per-window (conversation, preview, current project, focus).
///
/// Each window gets its own `WindowState` instance, injected via
/// `@Environment`. Shared state lives in `SharedAppState`, which
/// is a single instance passed to all windows.
///
/// This means from Phase 1, you can have two conversations open in
/// two windows, each with its own state. Retrofitting per-window
/// state later would require a full state layer refactor.
@Observable
final class WindowState {

    // MARK: - Conversation

    /// Messages in the current conversation (per-window).
    var messages: [Message] = []

    /// Whether a message is currently being sent (per-window).
    var isSending: Bool = false

    // MARK: - Preview

    /// The HTML content currently shown in the preview pane (per-window).
    var previewHTML: String? = nil

    /// Whether the preview pane is visible (per-window).
    var previewVisible: Bool = false

    // MARK: - Canvas (Phase 6)

    /// Whether the conversation or canvas has focus (per-window).
    /// `.conversation` = conversation is focused, canvas is dimmed.
    /// `.canvas` = canvas is focused, conversation is dimmed.
    var canvasFocus: CanvasFocus = .conversation

    // MARK: - Project (Phase 5)

    /// The currently open project (per-window). nil if no project is open.
    var currentProject: HoriProject? = nil

    // MARK: - Connection

    /// Connection state for this window's conversation.
    var connectionState: ConnectionState = .disconnected

    // MARK: - Focus

    /// The current input focus target (per-window).
    var inputFocus: InputFocus = .composer

    // MARK: - Initialization

    init() {}

    // MARK: - Message Model

    /// A single message in the conversation.
    struct Message: Identifiable, Equatable {
        let id: UUID
        let role: Role
        var content: String
        let timestamp: Date

        enum Role: String, Equatable {
            case user
            case hori
        }

        init(id: UUID = UUID(), role: Role, content: String, timestamp: Date = Date()) {
            self.id = id
            self.role = role
            self.content = content
            self.timestamp = timestamp
        }
    }

    // MARK: - Enums

    /// Canvas focus state (used in Phase 6, defined here for forward compatibility).
    enum CanvasFocus: String {
        case conversation
        case canvas
    }

    /// Connection state for the current window.
    enum ConnectionState: String {
        case disconnected
        case connecting
        case connected
        case error
    }

    /// Input focus target.
    enum InputFocus: String {
        case composer
        case messageList
        case projectSidebar
        case preview
    }
}
