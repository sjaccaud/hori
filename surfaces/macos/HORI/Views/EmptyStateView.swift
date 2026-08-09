import SwiftUI

/// The first impression.
///
/// What you see when you open HORI for the first time: a warm dark
/// space, a koi in the corner, and "What do you want to make today?"
/// centered, inviting. No toolbar, no chrome, no clutter.
///
/// This is where the design system is born. Every color, font, and
/// animation here sets the bar for the rest of the app.
///
/// Accessibility:
/// - VoiceOver announces "HORI. What do you want to make today?"
/// - The koi image has an accessibility label
/// - The entrance animation respects Reduce Motion
///
/// Localization:
/// - All strings use LocalizedStringKey (SwiftUI Text default)
struct EmptyStateView: View {

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Entrance animation state.
    @State private var appeared = false

    var body: some View {
        VStack(spacing: 0) {

            Spacer()

            // The koi placeholder — static image for now.
            // Animated version comes in Phase 7.
            koiPlaceholder
                .opacity(appeared ? 1.0 : 0.0)
                .offset(y: appeared ? 0 : 20)

            // The prompt — warm, inviting, not screaming.
            Text("What do you want to make today?")
                .font(HoriTypography.display)
                .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                .padding(.top, 24)
                .opacity(appeared ? 1.0 : 0.0)
                .offset(y: appeared ? 0 : 20)

            Spacer()

            // Subtle footer — the tagline, very low key.
            Text("AI you own. No tokens or subscriptions necessary.")
                .font(HoriTypography.caption)
                .foregroundStyle(HoriTheme.textSecondary(for: colorScheme).opacity(0.5))
                .padding(.bottom, 24)
                .opacity(appeared ? 1.0 : 0.0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.horizontal, 48)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("HORI. What do you want to make today?")
        .accessibilityHint("Start typing to begin a conversation with HORI.")
        .onAppear {
            withAnimation(HoriAnimations.balanced(reduceMotion: reduceMotion)) {
                appeared = true
            }
        }
    }

    // MARK: - Koi Placeholder

    /// A simple koi placeholder using SF Symbols.
    /// In Phase 7, this is replaced with an animated Rive/Lottie mascot.
    /// For now, a fish symbol in the accent color, gently floating.
    private var koiPlaceholder: some View {
        Image(systemName: "fish.fill")
            .font(.system(size: 48, weight: .light))
            .foregroundStyle(
                LinearGradient(
                    colors: [
                        HoriTheme.accentFallback.opacity(0.7),
                        HoriTheme.accentFallback.opacity(0.4),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
            .accessibilityLabel("HORI koi mascot")
            .accessibilityHint("HORI is ready and waiting.")
    }
}
