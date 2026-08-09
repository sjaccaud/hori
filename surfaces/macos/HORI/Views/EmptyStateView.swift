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
/// In Phase 2, the koi reacts to HORI's presence state:
/// - idle: gentle floating (subtle vertical bob)
/// - thinking: slight wiggle (rotation oscillation)
/// - hasNudge: gentle glow (pulsing opacity)
/// - offline: still, dimmed
///
/// Accessibility:
/// - VoiceOver announces "HORI. What do you want to make today?"
/// - The koi image has an accessibility label
/// - The entrance animation respects Reduce Motion
/// - Presence-reactive animations respect Reduce Motion
///
/// Localization:
/// - All strings use LocalizedStringKey (SwiftUI Text default)
struct EmptyStateView: View {

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(SharedAppState.self) private var sharedState

    /// Entrance animation state.
    @State private var appeared = false

    /// Koi floating animation (idle).
    @State private var floating = false

    /// Koi wiggle animation (thinking).
    @State private var wiggling = false

    /// Koi glow animation (has_nudge).
    @State private var glowing = false

    var body: some View {
        VStack(spacing: 0) {

            Spacer()

            // The koi — reacts to presence state.
            koiPlaceholder
                .opacity(koiOpacity)
                .offset(y: koiOffset)
                .rotationEffect(.degrees(koiRotation))
                .shadow(color: koiShadow, radius: koiShadowRadius)

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
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 48)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("HORI. What do you want to make today.")
        .accessibilityHint("Start typing to begin a conversation with HORI.")
        .onAppear {
            withAnimation(HoriAnimations.balanced(reduceMotion: reduceMotion)) {
                appeared = true
            }
            startKoiAnimation()
        }
        .onChange(of: sharedState.presence) { _, _ in
            startKoiAnimation()
        }
    }

    // MARK: - Koi Placeholder

    /// A simple koi placeholder using SF Symbols.
    /// In Phase 7, this is replaced with an animated Rive/Lottie mascot.
    /// For now, a fish symbol in the accent color, reacting to presence.
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
            .accessibilityHint(sharedState.presence.accessibilityDescription)
    }

    // MARK: - Koi Presence Animations

    /// The effective presence (offline if not connected).
    private var effectivePresence: PresenceState {
        sharedState.isPresenceConnected ? sharedState.presence : .offline
    }

    private var koiOpacity: Double {
        guard appeared else { return 0.0 }
        guard !reduceMotion else {
            return effectivePresence == .offline ? 0.4 : 1.0
        }
        switch effectivePresence {
        case .idle:      return 1.0
        case .thinking:  return 1.0
        case .hasNudge:  return glowing ? 1.0 : 0.6
        case .offline:   return 0.4
        }
    }

    private var koiOffset: CGFloat {
        guard appeared else { return 20 }
        guard !reduceMotion else { return 0 }
        switch effectivePresence {
        case .idle:      return floating ? -4 : 4
        case .thinking:  return 0
        case .hasNudge:  return 0
        case .offline:   return 0
        }
    }

    private var koiRotation: Double {
        guard appeared, !reduceMotion else { return 0 }
        switch effectivePresence {
        case .idle:      return 0
        case .thinking:  return wiggling ? 5 : -5
        case .hasNudge:  return 0
        case .offline:   return 0
        }
    }

    private var koiShadow: Color {
        guard appeared, !reduceMotion else { return .clear }
        switch effectivePresence {
        case .idle:      return .clear
        case .thinking:  return .clear
        case .hasNudge:  return HoriTheme.accentFallback
        case .offline:   return .clear
        }
    }

    private var koiShadowRadius: CGFloat {
        guard appeared, !reduceMotion else { return 0 }
        switch effectivePresence {
        case .idle:      return 0
        case .thinking:  return 0
        case .hasNudge:  return glowing ? 16 : 6
        case .offline:   return 0
        }
    }

    private func startKoiAnimation() {
        guard !reduceMotion else { return }
        // Reset all animation states
        floating = false
        wiggling = false
        glowing = false

        switch effectivePresence {
        case .idle:
            withAnimation(.easeInOut(duration: 2.5).repeatForever(autoreverses: true)) {
                floating = true
            }
        case .thinking:
            withAnimation(.easeInOut(duration: 0.4).repeatForever(autoreverses: true)) {
                wiggling = true
            }
        case .hasNudge:
            withAnimation(.easeInOut(duration: 1.8).repeatForever(autoreverses: true)) {
                glowing = true
            }
        case .offline:
            break  // no animation
        }
    }
}
