import SwiftUI

/// The conversation floating over the canvas.
///
/// When the canvas is focused, the conversation dims and shrinks slightly.
/// When the conversation is focused, it's fully visible and interactive.
/// Clicking on the dimmed conversation brings it back to focus.
///
/// The conversation is positioned as a floating panel — not a sidebar,
/// not a split view, but a panel that floats over the emerging software.
/// It has rounded corners, a semi-transparent background, and a gap
/// from the window edge so it visually "floats."
///
/// Traces to: docs/roadmap.md MAC-6 (The Emerging Canvas).
struct CanvasConversationOverlay<Content: View>: View {

    /// The conversation content (messages + input).
    let conversation: Content

    /// Whether the conversation is focused (vs. canvas).
    @Binding var focus: WindowState.CanvasFocus

    /// Called when the user clicks the dimmed conversation to refocus it.
    let onRefocus: () -> Void

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { geo in
            let panelWidth: CGFloat = min(440, geo.size.width * 0.42)
            let isConvoFocused = focus == .conversation

            HStack(spacing: 0) {
                // Floating conversation panel — inset from edges, rounded
                conversationPanel
                    .frame(width: panelWidth)
                    .background(
                        HoriTheme.background(for: colorScheme)
                            .opacity(isConvoFocused ? 0.95 : 0.7)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                    .shadow(color: .black.opacity(0.4), radius: 12, x: 4, y: 0)
                    .padding(.leading, 12)
                    .padding(.top, 12)
                    .padding(.bottom, 12)
                    .opacity(isConvoFocused ? 1.0 : 0.4)
                    .scaleEffect(isConvoFocused ? 1.0 : 0.96, anchor: .leading)
                    .onTapGesture {
                        if !isConvoFocused {
                            onRefocus()
                        }
                    }
                    .accessibilityLabel(isConvoFocused ? "Conversation" : "Conversation (click to refocus)")

                Spacer()
            }
        }
    }

    // MARK: - Conversation Panel

    private var conversationPanel: some View {
        conversation
            .animation(HoriAnimations.snappy(reduceMotion: reduceMotion), value: focus == .conversation)
    }

    // MARK: - Computed

    private var isConvoFocused: Bool {
        focus == .conversation
    }
}
