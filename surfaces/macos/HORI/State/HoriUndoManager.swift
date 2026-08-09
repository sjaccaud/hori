import SwiftUI

/// HORI UndoManager setup.
///
/// Undo/Redo is a foundational decision — macOS users expect Undo,
/// and retrofitting it requires every state mutation to register an
/// undo action. You can't go back and add undo to 100 mutations
/// after the fact.
///
/// The `UndoManager` is created per-window and injected via
/// `@Environment`. Every state mutation in a view model registers
/// an undo action. The Edit menu is wired to the responder chain.
///
/// From Phase 1: undo sending a message, undo deleting a message.
/// From Phase 5: undo file changes, undo project creation.
extension EnvironmentValues {
    /// Per-window UndoManager, injected by the window group.
    @Entry var horiUndoManager: UndoManager? = nil
}

/// A helper for registering undo actions in view models.
/// Usage:
/// ```
/// func sendMessage(_ text: String) {
///     let previousMessages = messages
///     messages.append(.init(role: .user, content: text))
///     HoriUndoManager.register(undoManager: undoManager) { [weak self] in
///         self?.messages = previousMessages
///     }
/// }
/// ```
enum HoriUndoManager {

    /// Registers an undo action with a descriptive label.
    /// The label appears in the Edit menu as "Undo [label]".
    static func register(undoManager: UndoManager?,
                         actionName: String,
                         undo: @escaping () -> Void) {
        guard let undoManager else { return }
        undoManager.beginUndoGrouping()
        undoManager.registerUndo(withTarget: undo as Any) { _ in
            undo()
        }
        undoManager.setActionName(actionName)
        undoManager.endUndoGrouping()
    }
}
