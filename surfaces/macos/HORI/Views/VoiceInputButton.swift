import SwiftUI

/// Microphone button for voice input.
///
/// Click to start listening, click again to stop and send. This is a
/// toggle, not push-to-talk — macOS mouse events don't reliably support
/// press-and-hold via SwiftUI gestures (a quick click fires press+release
/// almost instantly). Toggle is more standard on macOS and more reliable.
///
/// Visual states:
/// - idle: mic icon, subtle — click to start
/// - listening: pulsing red circle, "Listening..." — click to stop & send
/// - processing: spinner, "Thinking..." — disabled
/// - speaking: animated sound waves, "Speaking..." — disabled
///
/// Accessibility:
/// - VoiceOver label changes with state
/// - Reduce Motion: static states instead of animations
///
/// Traces to: docs/roadmap.md MAC-3 (Voice Conversation), Phase 3.
struct VoiceInputButton: View {

    let voiceState: VoiceState
    let onToggle: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        buttonContent
            .onTapGesture {
                if voiceState.phase == .idle || voiceState.phase == .listening {
                    onToggle()
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(accessibilityLabel)
            .accessibilityHint(accessibilityHint)
            .accessibilityAddTraits(.isButton)
    }

    // MARK: - Button Content

    @ViewBuilder
    private var buttonContent: some View {
        switch voiceState.phase {
        case .idle:
            micButton(icon: "mic.fill", color: HoriTheme.textSecondary(for: colorScheme))

        case .listening:
            micButton(icon: "mic.fill", color: HoriTheme.semanticError)
                .overlay(pulsingCircle)
                .overlay(
                    Text("Listening...  Click to send")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                        .offset(y: 36)
                )

        case .processing:
            micButton(icon: "mic.fill", color: HoriTheme.semanticThinking)
                .overlay(
                    ProgressView()
                        .controlSize(.small)
                        .offset(y: 36)
                )
                .overlay(
                    Text("Thinking...")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                        .offset(y: 52)
                )

        case .speaking:
            micButton(icon: "speaker.wave.2.fill", color: HoriTheme.semanticIdle)
                .overlay(
                    Text("Speaking...")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                        .offset(y: 36)
                )
        }
    }

    // MARK: - Mic Button Base

    private func micButton(icon: String, color: Color) -> some View {
        Image(systemName: icon)
            .font(.system(size: 20, weight: .medium))
            .foregroundStyle(color)
            .frame(width: 44, height: 44)
            .contentShape(Circle())
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
                    .scaleEffect(1.3)
                    .opacity(0.0)
                    .animation(
                        .easeInOut(duration: 1.2).repeatForever(autoreverses: true),
                        value: voiceState.phase == .listening
                    )
            }
        }
    }

    // MARK: - Accessibility

    private var accessibilityLabel: String {
        switch voiceState.phase {
        case .idle:       return "Talk to HORI. Click to start listening."
        case .listening:  return "Listening. Click to stop and send."
        case .processing: return "HORI is thinking"
        case .speaking:   return "HORI is speaking"
        }
    }

    private var accessibilityHint: String {
        switch voiceState.phase {
        case .idle:      return "Click to start recording"
        case .listening: return "Click to stop recording and send your message"
        default:         return ""
        }
    }
}
