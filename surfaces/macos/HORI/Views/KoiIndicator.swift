import SwiftUI

/// A small koi indicator that shows HORI's presence state during conversation.
///
/// Unlike the EmptyStateView koi (large, central, animated), this is a
/// compact indicator for the conversation header — a small fish that
/// reacts to presence state with subtle animation.
///
/// - idle: gentle floating
/// - thinking: wiggle
/// - hasNudge: glow
/// - offline: still, dimmed
///
/// Traces to: docs/roadmap.md MAC-7 (The Koi, Menu Bar, Sound).
struct KoiIndicator: View {

    @Environment(SharedAppState.self) private var sharedState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var floating = false
    @State private var wiggling = false
    @State private var glowing = false

    var body: some View {
        Image(systemName: "fish.fill")
            .font(.system(size: 16, weight: .light))
            .foregroundStyle(koiColor)
            .opacity(koiOpacity)
            .offset(y: koiOffset)
            .rotationEffect(.degrees(koiRotation))
            .shadow(color: koiShadow, radius: koiShadowRadius)
            .accessibilityLabel("HORI koi")
            .accessibilityValue(effectivePresence.accessibilityDescription)
            .onAppear { startAnimation() }
            .onChange(of: sharedState.presence) { _, _ in startAnimation() }
    }

    // MARK: - Presence

    private var effectivePresence: PresenceState {
        sharedState.isPresenceConnected ? sharedState.presence : .offline
    }

    // MARK: - Visuals

    private var koiColor: Color {
        HoriTheme.accentFallback.opacity(0.8)
    }

    private var koiOpacity: Double {
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
        guard !reduceMotion else { return 0 }
        switch effectivePresence {
        case .idle:      return floating ? -2 : 2
        case .thinking:  return 0
        case .hasNudge:  return 0
        case .offline:   return 0
        }
    }

    private var koiRotation: Double {
        guard !reduceMotion else { return 0 }
        switch effectivePresence {
        case .idle:      return 0
        case .thinking:  return wiggling ? 8 : -8
        case .hasNudge:  return 0
        case .offline:   return 0
        }
    }

    private var koiShadow: Color {
        guard !reduceMotion else { return .clear }
        switch effectivePresence {
        case .idle:      return .clear
        case .thinking:  return .clear
        case .hasNudge:  return HoriTheme.accentFallback
        case .offline:   return .clear
        }
    }

    private var koiShadowRadius: CGFloat {
        guard !reduceMotion else { return 0 }
        switch effectivePresence {
        case .idle:      return 0
        case .thinking:  return 0
        case .hasNudge:  return glowing ? 8 : 3
        case .offline:   return 0
        }
    }

    // MARK: - Animation

    private func startAnimation() {
        guard !reduceMotion else { return }
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
            break
        }
    }
}
