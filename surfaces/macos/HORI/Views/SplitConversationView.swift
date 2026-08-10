import SwiftUI

/// Split view showing conversation and live HTML preview side by side.
///
/// When HORI's latest message contains HTML, the view splits:
/// - Left: conversation (messages + input)
/// - Right: live HTML preview (WKWebView)
///
/// A toggle button lets the user show/hide the preview pane. When hidden,
/// the conversation takes the full width.
///
/// Traces to: docs/roadmap.md MAC-4 (Live Preview).
struct SplitConversationView<Conversation: View, Preview: View>: View {

    /// The conversation content (messages + input).
    let conversation: Conversation

    /// The preview content (HTMLPreviewView or empty).
    let preview: Preview

    /// Whether the preview pane is visible.
    @Binding var previewVisible: Bool

    /// Whether there's HTML to preview (controls toggle button visibility).
    let hasPreviewContent: Bool

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HSplitView {
            conversation
                .frame(minWidth: 300)

            if previewVisible {
                previewPane
                    .frame(minWidth: 300, idealWidth: 400)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Preview Pane

    private var previewPane: some View {
        VStack(spacing: 0) {
            // Preview header with close button
            HStack {
                Text("Preview")
                    .font(HoriTypography.label)
                    .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                Spacer()
                Button {
                    withAnimation(HoriAnimations.snappy(reduceMotion: false)) {
                        previewVisible = false
                    }
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Close preview")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(HoriTheme.surface(for: colorScheme))

            Divider()

            // Preview content
            preview
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(HoriTheme.background(for: colorScheme))
        }
        .background(HoriTheme.surface(for: colorScheme))
    }
}
