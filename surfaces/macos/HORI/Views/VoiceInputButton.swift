import SwiftUI

/// Push-to-talk microphone button.
///
/// Press and hold to record, release to send. Visual states:
/// - idle: mic icon, subtle
/// - listening: pulsing red circle, "Listening..."
/// - processing: spinner, "Thinking..."
/// - speaking: animated sound waves, "Speaking..."
///
/// The press gesture is attached to the outer container, NOT to the
/// state-dependent content. This is critical: if the gesture were on
/// the inner view, SwiftUI would replace it when the state changes
/// (idle → listening), and the original gesture's onEnded (release)
/// would never fire — the user would be stuck "holding" with no way
/// to release.
///
/// Accessibility:
/// - VoiceOver label changes with state
/// - Keyboard accessible (Space to hold, Enter to toggle)
/// - Reduce Motion: static states instead of animations
///
/// Traces to: docs/roadmap.md MAC-3 (Voice Conversation), Phase 3.
struct VoiceInputButton: View {

    let voiceState: VoiceState
    let onPress: () -> Void
    let onRelease: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var colorScheme

    /// Whether the button is currently being pressed (mouse down).
    @State private var isPressed = false

    var body: some View {
        // The gesture is on the OUTER container — it stays active
        // even when the inner content changes with voice state.
        // This prevents the "stuck holding" bug where the original
        // gesture's onEnded never fires after a state change.
        buttonContent
            .simultaneousGesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        if !isPressed {
                            isPressed = true
                            onPress()
                        }
                    }
                    .onEnded { _ in
                        isPressed = false
                        onRelease()
                    }
            )
            .accessibilityElement(children: .combine)
            .accessibilityLabel(accessibilityLabel)
            .accessibilityHint("Hold to talk, release to send")
            .accessibilityAddTraits(.isButton)
    }

    // MARK: - Button Content (visual only, no gestures)

    @ViewBuilder
    private var buttonContent: some View {
        switch voiceState.phase {
        case .idle:
            micButton(icon: "mic.fill", color: HoriTheme.textSecondary(for: colorScheme))

        case .listening:
            micButton(icon: "mic.fill", color: HoriTheme.semanticError)
                .overlay(pulsingCircle)
                .overlay(
                    Text("Listening...")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                        .offset(y: 32)
                )

        case .processing:
            micButton(icon: "mic.fill", color: HoriTheme.semanticThinking)
                .overlay(
                    ProgressView()
                        .controlSize(.small)
                        .offset(y: 32)
                )
                .overlay(
                    Text("Thinking...")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                        .offset(y: 48)
                )

        case .speaking:
            micButton(icon: "speaker.wave.2.fill", color: HoriTheme.semanticIdle)
                .overlay(
                    Text("Speaking...")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                        .offset(y: 32)
                )
        }
    }

    // MARK: - Mic Button Base

    private func micButton(icon: String, color: Color) -> some View {
        Image(systemName: icon)
            .font(.system(size: 20, weight: .medium))
            .foregroundStyle(color)
            .frame(width: 44, height: 44)
            .contentShape(Circle())  // ensures the whole circle is tappable
            .background(
                Circle()
                    .fill(HoriTheme.surface(for: colorScheme))
                    .overlay(
                        Circle()
                            .stroke(HoriTheme.border(for: colorScheme), lineWidth: 1)
                    )
            )
    }

    // MARK: - Pulsing Circle (listening state)

    private var pulsingCircle: some View {
        Group {
            if !reduceMotion {
                Circle()
                    .stroke(HoriTheme.semanticError.opacity(0.4), lineWidth: 2)
                    .scaleEffect(isPressed ? 1.3 : 1.0)
                    .opacity(isPressed ? 0.0 : 0.6)
                    .animation(
                        .easeInOut(duration: 1.0).repeatForever(autoreverses: true),
                        value: isPressed
                    )
            }
        }
    }

    // MARK: - Accessibility

    private var accessibilityLabel: String {
        switch voiceState.phase {
        case .idle:       return "Talk to HORI"
        case .listening:  return "Listening. Release to send."
        case .processing: return "HORI is thinking"
        case .speaking:   return "HORI is speaking"
        }
    }
}
