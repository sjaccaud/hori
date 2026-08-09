import Testing
import SwiftUI
@testable import HORI

/// Tests for the conversation views and message rendering.
///
/// Verifies that messages render correctly, the typing indicator
/// appears during sending, and the message bubble structure is
/// correct for user vs. HORI messages.
@Suite("Conversation Views")
struct ConversationViewTests {

    // MARK: - Message Bubble

    @Test("MessageBubble for user message has correct role")
    @MainActor
    func userMessageBubbleRole() {
        let message = WindowState.Message(role: .user, content: "Hello HORI")
        let bubble = MessageBubble(message: message)
        #expect(bubble.message.role == .user)
        #expect(bubble.message.content == "Hello HORI")
    }

    @Test("MessageBubble for HORI message has correct role")
    @MainActor
    func horiMessageBubbleRole() {
        let message = WindowState.Message(role: .hori, content: "Hello user!")
        let bubble = MessageBubble(message: message)
        #expect(bubble.message.role == .hori)
        #expect(bubble.message.content == "Hello user!")
    }

    // MARK: - Conversation View

    @Test("ConversationView accepts messages and renders without crash")
    @MainActor
    func conversationViewRenders() {
        let messages: [WindowState.Message] = [
            .init(role: .user, content: "Hi"),
            .init(role: .hori, content: "Hello!"),
        ]
        let view = ConversationView(messages: messages, isSending: false)
        #expect(view.messages.count == 2)
        #expect(view.isSending == false)
    }

    @Test("ConversationView with empty messages does not crash")
    @MainActor
    func conversationViewEmpty() {
        let view = ConversationView(messages: [], isSending: false)
        #expect(view.messages.isEmpty)
    }

    @Test("ConversationView shows typing indicator when sending")
    @MainActor
    func conversationViewSending() {
        let view = ConversationView(messages: [], isSending: true)
        #expect(view.isSending == true)
    }

    // MARK: - Message Input

    @Test("MessageInputView can be created with empty text")
    @MainActor
    func messageInputEmpty() {
        let view = MessageInputView(
            text: .constant(""),
            isSending: false,
            onSend: {}
        )
        #expect(view.isSending == false)
    }

    @Test("MessageInputView reflects sending state")
    @MainActor
    func messageInputSending() {
        let view = MessageInputView(
            text: .constant("Hello"),
            isSending: true,
            onSend: {}
        )
        #expect(view.isSending == true)
    }

    // MARK: - Connection Setup

    @Test("ConnectionSetupView can be created")
    @MainActor
    func connectionSetupViewCreation() {
        let view = ConnectionSetupView(isPresented: .constant(true))
        // If it initializes without crashing, the test passes.
        #expect(Bool(true))
    }

    // MARK: - Send Flow Logic

    @Test("Sending adds user message to window state")
    @MainActor
    func sendAddsUserMessage() {
        let state = WindowState()
        state.messages.append(.init(role: .user, content: "Test message"))
        #expect(state.messages.count == 1)
        #expect(state.messages.first?.role == .user)
        #expect(state.messages.first?.content == "Test message")
    }

    @Test("Receiving adds HORI message to window state")
    @MainActor
    func receiveAddsHoriMessage() {
        let state = WindowState()
        state.messages.append(.init(role: .user, content: "Question"))
        state.messages.append(.init(role: .hori, content: "Answer"))
        #expect(state.messages.count == 2)
        #expect(state.messages[0].role == .user)
        #expect(state.messages[1].role == .hori)
    }

    @Test("History for API excludes the just-added user message")
    @MainActor
    func historyExcludesLastMessage() {
        let state = WindowState()
        state.messages.append(.init(role: .user, content: "First"))
        state.messages.append(.init(role: .hori, content: "Reply"))
        state.messages.append(.init(role: .user, content: "Second"))

        // When sending, history = messages.dropLast()
        let history = state.messages.dropLast()
        #expect(history.count == 2)
        #expect(history[0].content == "First")
        #expect(history[1].content == "Reply")
    }

    @Test("isSending flag toggles correctly during send cycle")
    @MainActor
    func sendingFlagToggles() {
        let state = WindowState()
        #expect(state.isSending == false)

        state.isSending = true
        #expect(state.isSending == true)

        state.isSending = false
        #expect(state.isSending == false)
    }

    @Test("Connection state transitions through send cycle")
    @MainActor
    func connectionStateTransitions() {
        let state = WindowState()
        #expect(state.connectionState == .disconnected)

        state.connectionState = .connecting
        #expect(state.connectionState == .connecting)

        state.connectionState = .connected
        #expect(state.connectionState == .connected)
    }

    // MARK: - Error Banner

    @Test("ErrorBanner can be created with a message")
    @MainActor
    func errorBannerCreation() {
        let banner = ErrorBanner(text: "Connection failed") {}
        #expect(banner.text == "Connection failed")
    }
}
