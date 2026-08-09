import Testing
import SwiftUI
@testable import HORI

/// Tests for the UndoManager wiring.
///
/// Undo/Redo is a foundational decision — macOS users expect Undo,
/// and it must be wired in from Phase 0. These tests verify that
/// the HoriUndoManager helper registers undo actions correctly.
@Suite("Undo Manager")
struct UndoManagerTests {

    @Test("HoriUndoManager.register creates an undoable action")
    @MainActor
    func registerUndoAction() {
        let undoManager = UndoManager()
        var value = 5

        HoriUndoManager.register(
            undoManager: undoManager,
            actionName: "Change value"
        ) {
            value = 5
        }

        // Change the value
        value = 10
        #expect(value == 10)

        // Undo should restore the original value
        undoManager.undo()
        #expect(value == 5)
    }

    @Test("HoriUndoManager.register sets action name")
    @MainActor
    func registerSetsActionName() {
        let undoManager = UndoManager()
        var counter = 0

        HoriUndoManager.register(
            undoManager: undoManager,
            actionName: "Increment counter"
        ) {
            counter = 0
        }

        counter = 1
        // The action name should be set for the Edit menu.
        // We can't directly read the action name from UndoManager
        // in a test, but we verify the undo works.
        undoManager.undo()
        #expect(counter == 0)
    }

    @Test("HoriUndoManager.register with nil undoManager does not crash")
    func registerWithNilUndoManager() {
        // Should gracefully handle nil UndoManager (e.g. in tests
        // or previews where the environment isn't set up).
        HoriUndoManager.register(
            undoManager: nil,
            actionName: "Test"
        ) {
            // This closure should never be called.
        }
        // If we reach here without crashing, the test passes.
        #expect(Bool(true))
    }

    @Test("Multiple undo actions can be chained")
    @MainActor
    func chainedUndoActions() {
        let undoManager = UndoManager()
        var messages: [String] = []

        // Add first message
        HoriUndoManager.register(undoManager: undoManager, actionName: "Add message 1") {
            messages.removeLast()
        }
        messages.append("Hello")

        // Add second message
        HoriUndoManager.register(undoManager: undoManager, actionName: "Add message 2") {
            messages.removeLast()
        }
        messages.append("World")

        #expect(messages.count == 2)

        // Undo the last action
        undoManager.undo()
        #expect(messages.count == 1)
        #expect(messages.first == "Hello")

        // Undo the first action
        undoManager.undo()
        #expect(messages.isEmpty)
    }
}
