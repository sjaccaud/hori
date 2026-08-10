import SwiftUI

/// The full-screen canvas where emerging software renders.
///
/// This is the heart of the Emerging Canvas vision (MAC-6). The HTML
/// preview fills the entire window. When the conversation is focused,
/// the canvas is dimmed (greyed-out). When the user clicks on the canvas,
/// focus shifts to it and the conversation dims.
///
/// The canvas is always present when there's HTML to show. The
/// conversation floats *over* it, not beside it.
///
/// Traces to: docs/roadmap.md MAC-6 (The Emerging Canvas).
struct CanvasView: View {

    /// The HTML content to render.
    let html: String

    /// Whether the canvas is focused (interactive) or dimmed.
    let isFocused: Bool

    /// Called when the user clicks on the canvas to focus it.
    let onFocus: () -> Void

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            // Full-screen HTML preview
            HTMLPreviewView(html: html)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            // Dimming overlay when conversation is focused.
            // The canvas is "behind" the conversation — dimmed but visible.
            // Click to shift focus to the canvas (interact with what HORI built).
            Color.black
                .opacity(isFocused ? 0 : 0.45)
                .ignoresSafeArea()
                .onTapGesture {
                    if !isFocused {
                        onFocus()
                    }
                }
                .accessibilityLabel(isFocused ? "Canvas (interactive)" : "Canvas (click to interact)")
                .animation(HoriAnimations.snappy(reduceMotion: reduceMotion), value: isFocused)
        }
    }
}
