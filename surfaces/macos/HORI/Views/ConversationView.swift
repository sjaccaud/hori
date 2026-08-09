import SwiftUI

/// The conversation view — a scrollable list of message bubbles.
///
/// User messages are right-aligned with the accent color background.
/// HORI messages are left-aligned with the surface color background.
/// New messages animate in with a spring transition.
///
/// When the conversation is empty, this view is not shown — the
/// `EmptyStateView` is shown instead (handled by `ContentView`).
///
/// Accessibility:
/// - Each message bubble is an accessibility element with a label
///   describing who said it and what they said
/// - The list is navigable with VoiceOver
struct ConversationView: View {

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// The messages to display.
    let messages: [WindowState.Message]

    /// Whether HORI is currently thinking (shows a typing indicator).
    let isSending: Bool

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 16) {
                    ForEach(messages) { message in
                        MessageBubble(message: message)
                            .id(message.id)
                            .transition(
                                reduceMotion
                                    ? .opacity
                                    : .asymmetric(
                                        insertion: .move(edge: .bottom).combined(with: .opacity),
                                        removal: .opacity
                                    )
                            )
                    }

                    // Typing indicator when HORI is thinking.
                    if isSending {
                        TypingIndicator()
                            .id("typing-indicator")
                    }
                }
                .padding(.horizontal, 24)
                .padding(.top, 20)
                .padding(.bottom, 20)
            }
            .background(HoriTheme.background(for: colorScheme))
            // Auto-scroll to the latest message or typing indicator.
            .onChange(of: messages.count) { _, _ in
                withAnimation(HoriAnimations.balanced(reduceMotion: reduceMotion)) {
                    scrollToBottom(proxy: proxy)
                }
            }
            .onChange(of: isSending) { _, _ in
                withAnimation(HoriAnimations.balanced(reduceMotion: reduceMotion)) {
                    scrollToBottom(proxy: proxy)
                }
            }
            .onAppear {
                scrollToBottom(proxy: proxy)
            }
        }
    }

    private func scrollToBottom(proxy: ScrollViewProxy) {
        if isSending {
            proxy.scrollTo("typing-indicator", anchor: .bottom)
        } else if let last = messages.last {
            proxy.scrollTo(last.id, anchor: .bottom)
        }
    }
}

// MARK: - Message Bubble

/// A single message bubble — user (right, accent) or HORI (left, surface).
struct MessageBubble: View {

    @Environment(\.colorScheme) private var colorScheme

    let message: WindowState.Message

    var body: some View {
        HStack {
            if message.role == .user {
                Spacer(minLength: 60)
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 4) {
                Text(message.content)
                    .font(HoriTypography.body)
                    .foregroundStyle(HoriTheme.text(for: colorScheme))
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(bubbleBackground)
                    .clipShape(RoundedRectangle(cornerRadius: HoriShapes.medium))
                    .accessibilityLabel("\(message.role == .user ? "You" : "HORI") says: \(message.content)")
            }

            if message.role == .hori {
                Spacer(minLength: 60)
            }
        }
    }

    private var bubbleBackground: Color {
        message.role == .user
            ? HoriTheme.accentFallback.opacity(0.18)
            : HoriTheme.surface(for: colorScheme)
    }
}

// MARK: - Typing Indicator

/// A subtle typing indicator shown while HORI is thinking.
/// Three dots that pulse — the universal "someone is typing" signal.
struct TypingIndicator: View {

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var animate = false

    var body: some View {
        HStack {
            HStack(spacing: 5) {
                ForEach(0..<3, id: \.self) { i in
                    Circle()
                        .fill(HoriTheme.textSecondary(for: colorScheme).opacity(0.5))
                        .frame(width: 7, height: 7)
                        .scaleEffect(animate ? 1.0 : 0.6)
                        .animation(
                            reduceMotion
                                ? nil
                                : .easeInOut(duration: 0.6)
                                    .repeatForever()
                                    .delay(Double(i) * 0.15),
                            value: animate
                        )
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(HoriTheme.surface(for: colorScheme))
            .clipShape(RoundedRectangle(cornerRadius: HoriShapes.medium))

            Spacer(minLength: 60)
        }
        .accessibilityLabel("HORI is thinking")
        .onAppear {
            guard !reduceMotion else { return }
            animate = true
        }
    }
}
