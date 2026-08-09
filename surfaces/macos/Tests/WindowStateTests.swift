import Testing
import SwiftUI
@testable import HORI

/// Tests for the multi-window state architecture.
///
/// Verifies that per-window state (WindowState) is isolated
/// and that shared state (SharedAppState) can be accessed.
/// This is a foundational decision: macOS is multi-window,
/// and state must be split correctly from Phase 0.
@Suite("Window State Architecture")
struct WindowStateTests {

    @Test("Each WindowState instance is independent")
    func windowStateIsolation() {
        let window1 = WindowState()
        let window2 = WindowState()

        window1.messages.append(.init(role: .user, content: "Hello from window 1"))
        window2.messages.append(.init(role: .user, content: "Hello from window 2"))

        #expect(window1.messages.count == 1)
        #expect(window2.messages.count == 1)
        #expect(window1.messages.first?.content == "Hello from window 1")
        #expect(window2.messages.first?.content == "Hello from window 2")
    }

    @Test("WindowState starts with empty messages")
    func startsEmpty() {
        let state = WindowState()
        #expect(state.messages.isEmpty)
        #expect(state.previewHTML == nil)
        #expect(state.previewVisible == false)
        #expect(state.currentProject == nil)
        #expect(state.connectionState == .disconnected)
    }

    @Test("WindowState canvas focus defaults to conversation")
    func canvasFocusDefaultsToConversation() {
        let state = WindowState()
        #expect(state.canvasFocus == .conversation)
    }

    @Test("SharedAppState stores aiosCoreURL in UserDefaults")
    @MainActor
    func sharedStateURLStorage() {
        let state = SharedAppState()
        // Clean up any existing value
        UserDefaults.standard.removeObject(forKey: "aiosCoreURL")
        #expect(state.aiosCoreURL == "")
        #expect(state.isConnectionConfigured == false)

        state.aiosCoreURL = "https://100.64.0.1:5680"
        #expect(state.aiosCoreURL == "https://100.64.0.1:5680")
        #expect(state.isConnectionConfigured == true)

        // Clean up
        UserDefaults.standard.removeObject(forKey: "aiosCoreURL")
    }

    @Test("isConnectionConfigured changes are observable (@Observable tracking)")
    @MainActor
    func isConnectionConfiguredIsObservable() {
        // @Observable only tracks stored properties. If aiosCoreURL is a
        // computed property backed by UserDefaults, @Observable can't
        // detect changes, and views won't re-render when the URL is set.
        // This test verifies that changing aiosCoreURL fires the
        // observation tracking for isConnectionConfigured.
        let state = SharedAppState()
        UserDefaults.standard.removeObject(forKey: "aiosCoreURL")

        var changeFired = false
        withObservationTracking {
            _ = state.isConnectionConfigured
        } onChange: {
            changeFired = true
        }

        state.aiosCoreURL = "https://100.64.0.1:5680"

        #expect(changeFired, "isConnectionConfigured must be observable — changing aiosCoreURL must notify observers")

        UserDefaults.standard.removeObject(forKey: "aiosCoreURL")
    }

    @Test("SharedAppState presence defaults to offline")
    func sharedStatePresenceDefaults() {
        let state = SharedAppState()
        #expect(state.presence == .offline)
    }

    @Test("Message model has correct roles")
    func messageRoles() {
        let userMsg = WindowState.Message(role: .user, content: "Hello")
        let horiMsg = WindowState.Message(role: .hori, content: "Hi there!")
        #expect(userMsg.role == .user)
        #expect(horiMsg.role == .hori)
        #expect(userMsg.content == "Hello")
        #expect(horiMsg.content == "Hi there!")
    }

    @Test("Message IDs are unique")
    func messageIDsUnique() {
        let msg1 = WindowState.Message(role: .user, content: "1")
        let msg2 = WindowState.Message(role: .user, content: "2")
        #expect(msg1.id != msg2.id)
    }

    @Test("HoriProject generates slug from name")
    func projectSlugGeneration() {
        let project = HoriProject(name: "My Calculator")
        #expect(project.slug == "my-calculator")
    }
}
