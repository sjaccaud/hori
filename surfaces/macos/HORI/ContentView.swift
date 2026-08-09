import SwiftUI

/// The root content view for each HORI window.
///
/// In Phase 0, this showed only `EmptyStateView`.
/// In Phase 1, it shows:
/// - `EmptyStateView` when there are no messages (first impression)
/// - `ConversationView` when there are messages (the conversation)
/// - `MessageInputView` at the bottom (always, once configured)
/// - `ConnectionSetupView` as a sheet on first launch (no URL set)
///
/// Receives `WindowState` (per-window) and `SharedAppState` (shared)
/// via `@Environment`. The background is always `HoriTheme.background`
/// — warm dark, never default SwiftUI gray.
struct ContentView: View {

    @Environment(SharedAppState.self) private var sharedState
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Per-window state. Each window gets its own instance.
    @State private var windowState = WindowState()

    /// Per-window UndoManager.
    @State private var undoManager = UndoManager()

    /// The text currently in the input field.
    @State private var inputText: String = ""

    /// Whether the connection setup sheet is showing.
    @State private var showConnectionSetup: Bool = false

    /// Error message to show if a send fails (transient banner).
    @State private var errorMessage: String? = nil

    var body: some View {
        VStack(spacing: 0) {
            // Conversation area — empty state or message list.
            if windowState.messages.isEmpty {
                EmptyStateView()
            } else {
                ConversationView(
                    messages: windowState.messages,
                    isSending: windowState.isSending
                )
            }

            // Error banner (if any).
            if let error = errorMessage {
                ErrorBanner(text: error) {
                    withAnimation(HoriAnimations.snappy(reduceMotion: reduceMotion)) {
                        errorMessage = nil
                    }
                }
            }

            // Input field — always visible once configured.
            if sharedState.isConnectionConfigured {
                MessageInputView(
                    text: $inputText,
                    isSending: windowState.isSending,
                    onSend: sendMessage
                )
            }
        }
        .frame(minWidth: 600, minHeight: 400)
        .background(HoriTheme.background(for: colorScheme))
        .environment(windowState)
        .environment(\.horiUndoManager, undoManager)
        .sheet(isPresented: $showConnectionSetup) {
            ConnectionSetupView(isPresented: $showConnectionSetup)
        }
        .onAppear {
            if !sharedState.isConnectionConfigured {
                showConnectionSetup = true
            }
        }
    }

    // MARK: - Send

    /// Sends the current input text to HORI and handles the response.
    private func sendMessage() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !windowState.isSending else { return }

        // Add the user message immediately.
        let userMessage = WindowState.Message(role: .user, content: text)
        windowState.messages.append(userMessage)

        // Clear the input field.
        inputText = ""

        // Register undo: removes the user message.
        let prevMessages = windowState.messages
        HoriUndoManager.register(
            undoManager: undoManager,
            actionName: "Send message"
        ) { [weak windowState] in
            windowState?.messages = prevMessages
        }

        // Send to HORI.
        windowState.isSending = true
        windowState.connectionState = .connecting

        guard let url = URL(string: sharedState.aiosCoreURL) else {
            windowState.isSending = false
            windowState.connectionState = .error
            errorMessage = "Invalid server URL. Check your connection settings."
            return
        }

        let client = HoriClient(baseURL: url)
        let history = windowState.messages.dropLast() // exclude the message we just added

        Task {
            do {
                let reply = try await client.sendMessage(text, history: Array(history))
                await MainActor.run {
                    windowState.messages.append(WindowState.Message(role: .hori, content: reply))
                    windowState.isSending = false
                    windowState.connectionState = .connected
                }
            } catch {
                await MainActor.run {
                    windowState.isSending = false
                    windowState.connectionState = .error
                    errorMessage = error.localizedDescription
                }
            }
        }
    }
}

// MARK: - Error Banner

/// A transient error banner shown at the bottom of the conversation.
struct ErrorBanner: View {

    @Environment(\.colorScheme) private var colorScheme

    let text: String
    let onDismiss: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(HoriTheme.semanticError)
            Text(text)
                .font(HoriTypography.caption)
                .foregroundStyle(HoriTheme.text(for: colorScheme))
                .lineLimit(2)
            Spacer()
            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Dismiss error")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(HoriTheme.semanticError.opacity(0.12))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Error: \(text)")
    }
}
