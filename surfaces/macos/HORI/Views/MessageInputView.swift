import SwiftUI

/// The message input field and send button.
///
/// Keyboard-first: Cmd+Return sends (via a keyboard shortcut on the
/// send button). The send button is also visible for discoverability
/// and for users who prefer the mouse. The field is a TextEditor
/// (multi-line) with a bounded height — grows to ~5 lines then
/// scrolls internally.
///
/// Accessibility:
/// - The field has an accessibility label "Message input"
/// - The send button has a label and hint
/// - When sending is in progress, both are disabled
struct MessageInputView: View {

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// The typed text (two-way binding owned by the parent).
    @Binding var text: String

    /// Whether a message is currently being sent.
    let isSending: Bool

    /// Called when the user sends a message (Cmd+Return or button).
    let onSend: () -> Void

    /// Focus state for the text editor.
    @FocusState private var isFocused: Bool

    var body: some View {
        HStack(spacing: 12) {
            // Input field — multi-line, bounded height.
            TextEditor(text: $text)
                .font(HoriTypography.body)
                .scrollIndicators(.hidden)
                .frame(minHeight: 36, maxHeight: 100)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(HoriTheme.surface(for: colorScheme))
                .clipShape(RoundedRectangle(cornerRadius: HoriShapes.small))
                .overlay(
                    RoundedRectangle(cornerRadius: HoriShapes.small)
                        .stroke(HoriTheme.border(for: colorScheme), lineWidth: 1)
                )
                .focused($isFocused)
                .accessibilityLabel("Message input")
                .accessibilityHint("Type your message to HORI. Press Command+Return to send.")

            // Send button — also bound to Cmd+Return for keyboard-first.
            Button(action: send) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 28, weight: .medium))
                    .foregroundStyle(canSend ? HoriTheme.accentFallback : HoriTheme.textSecondary(for: colorScheme).opacity(0.4))
                    .accessibilityLabel("Send message")
                    .accessibilityHint("Sends your message to HORI.")
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.return, modifiers: .command)
            .disabled(!canSend)
            .animation(HoriAnimations.snappy(reduceMotion: reduceMotion), value: canSend)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .background(HoriTheme.background(for: colorScheme))
    }

    /// Whether the send action is available (text is non-empty and not sending).
    private var canSend: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isSending
    }

    private func send() {
        guard canSend else { return }
        onSend()
    }
}
