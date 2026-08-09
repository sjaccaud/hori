import SwiftUI

/// The voice conversation state machine.
///
/// Enforces a strict state sequence:
///   idle → listening → processing → speaking → idle
///
/// Illegal transitions are silently rejected (the state doesn't change).
/// This prevents race conditions — e.g. pressing PTT while a previous
/// response is still playing, or audio arriving after the user cancelled.
///
/// Traces to: docs/roadmap.md MAC-3 (Voice Conversation), Phase 3.
@Observable
final class VoiceState {

    /// The current phase of the voice conversation.
    var phase: Phase = .idle

    /// Partial transcript while listening (updated live by SFSpeechRecognizer).
    var partialTranscript: String = ""

    /// The final transcribed text after PTT release.
    var transcribedText: String = ""

    /// True when in any active phase (listening, processing, or speaking).
    var isBusy: Bool {
        phase != .idle
    }

    init() {}

    // MARK: - Transitions

    /// PTT pressed — start listening.
    /// Only valid from idle. Silently rejected otherwise.
    func startListening() {
        guard phase == .idle else { return }
        partialTranscript = ""
        transcribedText = ""
        phase = .listening
    }

    /// Update the partial transcript during listening.
    /// Ignored if not listening.
    func updatePartialTranscript(_ text: String) {
        guard phase == .listening else { return }
        partialTranscript = text
    }

    /// PTT released — stop listening and transition to processing.
    /// If the transcribed text is empty, cancels back to idle.
    /// Only valid from listening. Silently rejected otherwise.
    func stopListening(transcribedText: String) {
        guard phase == .listening else { return }
        partialTranscript = ""
        let trimmed = transcribedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            phase = .idle
            return
        }
        self.transcribedText = trimmed
        phase = .processing
    }

    /// First audio chunk arrived — transition to speaking.
    /// Only valid from processing. Silently rejected otherwise.
    func audioPlaybackStarted() {
        guard phase == .processing else { return }
        phase = .speaking
    }

    /// All audio finished playing — transition back to idle.
    /// Only valid from speaking. Silently rejected otherwise.
    func audioPlaybackFinished() {
        guard phase == .speaking else { return }
        transcribedText = ""
        phase = .idle
    }

    /// Cancel/reset from any state back to idle.
    func reset() {
        partialTranscript = ""
        transcribedText = ""
        phase = .idle
    }

    // MARK: - Phase Enum

    enum Phase: Equatable {
        case idle         // Not in a voice conversation
        case listening    // PTT held, recording + transcribing
        case processing   // PTT released, waiting for LLM response
        case speaking     // Playing TTS audio
    }
}
