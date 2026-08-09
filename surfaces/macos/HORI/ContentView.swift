import SwiftUI

/// The root content view for each HORI window.
///
/// In Phase 0, this showed only `EmptyStateView`.
/// In Phase 1, it shows:
/// - `EmptyStateView` when there are no messages (first impression)
/// - `ConversationView` when there are messages (the conversation)
/// - `MessageInputView` at the bottom (always, once configured)
/// - `ConnectionSetupView` as a sheet on first launch (no URL set)
/// In Phase 2, it also shows:
/// - `PresenceIndicator` in the top-right corner (live presence state)
/// In Phase 3, it adds:
/// - Voice mode toggle (text ↔ voice)
/// - `VoiceInputButton` for push-to-talk
/// - Streaming text + audio playback
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

    /// Whether voice mode is active (vs text mode).
    @State private var isVoiceMode: Bool = false

    /// The voice view model (created when voice mode is first activated).
    @State private var voiceViewModel: VoiceViewModel?

    /// Whether voice settings sheet is showing.
    @State private var showVoiceSettings: Bool = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
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

                // Input area — text or voice, depending on mode.
                if sharedState.isConnectionConfigured {
                    if isVoiceMode {
                        voiceInputArea
                    } else {
                        MessageInputView(
                            text: $inputText,
                            isSending: windowState.isSending,
                            onSend: sendMessage
                        )
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            // Presence indicator + mode toggle — top-right corner, overlay.
            if sharedState.isConnectionConfigured {
                VStack(alignment: .trailing, spacing: 8) {
                    HStack(spacing: 12) {
                        PresenceIndicator(
                            presence: sharedState.presence,
                            isConnected: sharedState.isPresenceConnected
                        )
                        modeToggleButton
                    }
                    if isVoiceMode {
                        Button {
                            showVoiceSettings = true
                        } label: {
                            Image(systemName: "gearshape")
                                .font(.system(size: 14, weight: .medium))
                                .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Voice settings")
                    }
                }
                .padding(.top, 12)
                .padding(.trailing, 16)
            }
        }
        .frame(minWidth: 600, minHeight: 400)
        .background(HoriTheme.background(for: colorScheme))
        .environment(windowState)
        .environment(\.horiUndoManager, undoManager)
        .sheet(isPresented: $showConnectionSetup) {
            ConnectionSetupView(isPresented: $showConnectionSetup)
        }
        .sheet(isPresented: $showVoiceSettings) {
            VoiceSettingsView()
        }
        .onAppear {
            if !sharedState.isConnectionConfigured {
                showConnectionSetup = true
            }
        }
        .onChange(of: isVoiceMode) { _, newValue in
            if newValue && voiceViewModel == nil {
                createVoiceViewModel()
            }
        }
    }

    // MARK: - Voice Input Area

    private var voiceInputArea: some View {
        HStack(spacing: 12) {
            if let vm = voiceViewModel {
                // Partial transcript (live feedback while listening)
                if !vm.voiceState.partialTranscript.isEmpty {
                    Text(vm.voiceState.partialTranscript)
                        .font(HoriTypography.body)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                        .lineLimit(2)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    Text("Hold the mic button to talk")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme).opacity(0.5))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                VoiceInputButton(
                    voiceState: vm.voiceState,
                    onToggle: {
                        if vm.voiceState.phase == .idle {
                            vm.startTalking()
                        } else if vm.voiceState.phase == .listening {
                            vm.stopTalking()
                        }
                    }
                )
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    // MARK: - Mode Toggle

    private var modeToggleButton: some View {
        Button {
            withAnimation(HoriAnimations.snappy(reduceMotion: reduceMotion)) {
                isVoiceMode.toggle()
            }
        } label: {
            Image(systemName: isVoiceMode ? "keyboard" : "mic.fill")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isVoiceMode ? "Switch to text input" : "Switch to voice input")
    }

    // MARK: - Voice ViewModel

    private func createVoiceViewModel() {
        guard let url = URL(string: sharedState.aiosCoreURL) else { return }
        let vm = VoiceViewModel(
            baseURL: url,
            voice: sharedState.ttsVoice,
            speed: Float(sharedState.ttsSpeed)
        )

        // Wire text chunks to update the conversation
        vm.onTextChunk = { chunk in
            // Accumulate streaming text into the last HORI message
            if let lastMsg = windowState.messages.last, lastMsg.role == .hori {
                // Update existing HORI message
                let updated = WindowState.Message(
                    role: .hori,
                    content: lastMsg.content + chunk
                )
                windowState.messages[windowState.messages.count - 1] = updated
            } else {
                // Start a new HORI message
                windowState.messages.append(WindowState.Message(role: .hori, content: chunk))
            }
        }

        vm.onDone = { fullText in
            // Replace the last HORI message with the full text
            if let lastIdx = windowState.messages.indices.last,
               windowState.messages[lastIdx].role == .hori {
                windowState.messages[lastIdx] = WindowState.Message(role: .hori, content: fullText)
            }
            windowState.isSending = false
        }

        vm.onError = { msg in
            errorMessage = msg
            windowState.isSending = false
        }

        voiceViewModel = vm
    }

    // MARK: - Send (Text Mode)

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
        let history = windowState.messages.dropLast()

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
