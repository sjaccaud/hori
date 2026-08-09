import SwiftUI

/// The presence indicator — a colored dot with a label that shows
/// HORI's current state.
///
/// - idle: green dot, gentle breathing animation, "Available"
/// - thinking: orange dot, pulsing animation, "Thinking"
/// - hasNudge: violet dot, glow animation, "Has something to say"
/// - offline: red dot, no animation, "Offline"
///
/// Color is never the only signal — each state has a distinct shape
/// (animation style) and text label, per the accessibility principle.
/// All animations respect Reduce Motion (replaced with static opacity).
///
/// Traces to: UX-1.3 (ambient presence), Manifesto Pillar V (visible
/// autonomy) + IV (presence).
struct PresenceIndicator: View {

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let presence: PresenceState
    let isConnected: Bool

    var body: some View {
        HStack(spacing: 8) {
            PresenceDot(presence: effectivePresence, reduceMotion: reduceMotion)
            Text(effectivePresence.description)
                .font(HoriTypography.caption)
                .foregroundStyle(HoriTheme.textSecondary(for: .dark))
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(effectivePresence.accessibilityDescription)
    }

    /// If not connected, show offline regardless of the presence state.
    private var effectivePresence: PresenceState {
        isConnected ? presence : .offline
    }
}

// MARK: - Presence Dot

/// The animated dot for a single presence state.
struct PresenceDot: View {

    let presence: PresenceState
    let reduceMotion: Bool

    // Breathing animation (idle)
    @State private var breathing = false

    // Pulse animation (thinking)
    @State private var pulsing = false

    // Glow animation (has_nudge)
    @State private var glowing = false

    var body: some View {
        Circle()
            .fill(presence.color)
            .frame(width: 8, height: 8)
            .scaleEffect(scale)
            .opacity(opacity)
            .shadow(color: shadowColor, radius: shadowRadius)
            .onAppear { startAnimation() }
            .onChange(of: presence) { _, _ in startAnimation() }
    }

    private var scale: CGFloat {
        guard !reduceMotion else { return 1.0 }
        switch presence {
        case .idle:      return breathing ? 1.15 : 0.85
        case .thinking:  return pulsing ? 1.3 : 0.9
        case .hasNudge:  return glowing ? 1.2 : 1.0
        case .offline:   return 1.0
        }
    }

    private var opacity: Double {
        guard !reduceMotion else {
            return presence == .offline ? 0.4 : 1.0
        }
        switch presence {
        case .idle:      return breathing ? 1.0 : 0.6
        case .thinking:  return pulsing ? 1.0 : 0.5
        case .hasNudge:  return glowing ? 1.0 : 0.7
        case .offline:   return 0.4
        }
    }

    private var shadowColor: Color {
        guard !reduceMotion else { return .clear }
        switch presence {
        case .idle:      return HoriTheme.semanticIdle
        case .thinking:  return HoriTheme.semanticThinking
        case .hasNudge:  return HoriTheme.accentFallback
        case .offline:   return .clear
        }
    }

    private var shadowRadius: CGFloat {
        guard !reduceMotion else { return 0 }
        switch presence {
        case .idle:      return breathing ? 6 : 2
        case .thinking:  return pulsing ? 8 : 2
        case .hasNudge:  return glowing ? 10 : 3
        case .offline:   return 0
        }
    }

    private func startAnimation() {
        guard !reduceMotion else { return }
        switch presence {
        case .idle:
            withAnimation(.easeInOut(duration: 2.0).repeatForever(autoreverses: true)) {
                breathing = true
            }
            pulsing = false
            glowing = false
        case .thinking:
            withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) {
                pulsing = true
            }
            breathing = false
            glowing = false
        case .hasNudge:
            withAnimation(.easeInOut(duration: 1.5).repeatForever(autoreverses: true)) {
                glowing = true
            }
            breathing = false
            pulsing = false
        case .offline:
            breathing = false
            pulsing = false
            glowing = false
        }
    }
}
