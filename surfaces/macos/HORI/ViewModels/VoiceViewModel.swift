import Foundation
import SwiftUI

/// Coordinates the voice conversation flow.
///
/// Wires together SpeechRecognizer, VoiceChatStreamClient, AudioPlayer,
/// and VoiceState. The view calls `startTalking()` / `stopTalking()`
/// (PTT), and this class handles the full round-trip:
/// listen → transcribe → send → stream text + audio → play audio.
///
/// Traces to: docs/roadmap.md MAC-3 (Voice Conversation), Phase 3.
@Observable
final class VoiceViewModel {

    /// The voice state machine.
    let voiceState = VoiceState()

    /// The speech recognizer for STT.
    private let speechRecognizer: SpeechRecognizer

    /// The audio player for TTS playback.
    private let audioPlayer: AudioPlayer

    /// The streaming client for /v1/voice/chat/stream.
    private var streamClient: VoiceChatStreamClient?

    /// The server URL (from SharedAppState).
    private let baseURL: URL

    /// The selected TTS voice.
    private var voice: String

    /// The selected TTS speed.
    private var speed: Float

    /// Callback when text chunks arrive (for updating the conversation view).
    var onTextChunk: ((String) -> Void)?

    /// Callback when the stream is done (full text available).
    var onDone: ((String) -> Void)?

    /// Callback when an error occurs.
    var onError: ((String) -> Void)?

    /// Callback when the user's voice message is sent (transcribed text).
    var onSent: ((String) -> Void)?

    init(baseURL: URL, voice: String = "af_heart", speed: Float = 1.0) {
        self.baseURL = baseURL
        self.voice = voice
        self.speed = speed
        self.speechRecognizer = SpeechRecognizer()
        self.audioPlayer = AudioPlayer()

        // Wire speech recognizer
        speechRecognizer.onPartialTranscript = { [weak self] text in
            self?.voiceState.updatePartialTranscript(text)
        }
        speechRecognizer.onFinalTranscript = { [weak self] text in
            self?.handleFinalTranscript(text)
        }
        speechRecognizer.onError = { [weak self] msg in
            // "No speech detected" is not a real error — the user just
            // didn't say anything. Go back to idle without showing an error.
            if msg.lowercased().contains("no speech") {
                self?.voiceState.reset()
            } else {
                self?.handleError(msg)
            }
        }

        // Wire audio player
        audioPlayer.onPlaybackStarted = { [weak self] in
            self?.voiceState.audioPlaybackStarted()
        }
        audioPlayer.onPlaybackFinished = { [weak self] in
            self?.voiceState.audioPlaybackFinished()
        }
    }

    // MARK: - Push to Talk

    /// PTT pressed — start listening.
    func startTalking() {
        // Cancel any ongoing playback
        audioPlayer.reset()
        streamClient?.cancel()
        voiceState.reset()
        voiceState.startListening()
        speechRecognizer.startListening()
    }

    /// PTT released — stop listening and send.
    func stopTalking() {
        speechRecognizer.stopListening()
        // handleFinalTranscript will be called by the speech recognizer callback
    }

    // MARK: - Transcript Handling

    private func handleFinalTranscript(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            voiceState.reset()
            return
        }

        voiceState.stopListening(transcribedText: trimmed)
        sendToHori(text: trimmed)
    }

    // MARK: - Send to HORI

    private func sendToHori(text: String) {
        onSent?(text)
        streamClient = VoiceChatStreamClient(
            baseURL: baseURL,
            onText: { [weak self] chunk in
                self?.onTextChunk?(chunk)
            },
            onAudio: { [weak self] data, index in
                self?.audioPlayer.enqueue(data, index: index)
                if self?.audioPlayer.state == .queued {
                    self?.audioPlayer.startPlayback()
                }
            },
            onSearching: { _ in
                // Could show a "searching..." indicator
            },
            onCorrection: { original, replacement in
                // Could update the conversation view
            },
            onDone: { [weak self] _, fullText in
                self?.onDone?(fullText)
            },
            onError: { [weak self] msg in
                self?.handleError(msg)
            }
        )

        streamClient?.send(text: text, voice: voice, speed: speed)
    }

    // MARK: - Error Handling

    private func handleError(_ message: String) {
        voiceState.reset()
        audioPlayer.reset()
        onError?(message)
    }

    // MARK: - Cleanup

    func cancel() {
        speechRecognizer.cancel()
        streamClient?.cancel()
        audioPlayer.reset()
        voiceState.reset()
    }
}
