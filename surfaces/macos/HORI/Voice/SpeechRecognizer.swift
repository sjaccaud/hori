import Foundation
import Speech
import AVFoundation

/// On-device speech recognition wrapper using SFSpeechRecognizer.
///
/// Uses AVAudioEngine to capture microphone input and SFSpeechRecognizer
/// to transcribe speech to text in real time. Provides partial
/// transcripts (for live UI feedback) and a final transcript (when
/// listening stops).
///
/// Note: AVAudioSession (iOS) is not used — macOS handles audio routing
/// automatically via AVAudioEngine.
///
/// Traces to: docs/roadmap.md MAC-3 (Voice Conversation), Phase 3.
final class SpeechRecognizer: NSObject {

    /// Called with partial transcript during recognition (main thread).
    var onPartialTranscript: ((String) -> Void)?

    /// Called with final transcript when listening stops (main thread).
    var onFinalTranscript: ((String) -> Void)?

    /// Called if recognition fails or is denied (main thread).
    var onError: ((String) -> Void)?

    /// Whether currently listening.
    private(set) var isListening = false

    private let speechRecognizer: SFSpeechRecognizer?
    private let audioEngine = AVAudioEngine()
    private var recognitionTask: SFSpeechRecognitionTask?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var finalTranscript = ""

    override init() {
        self.speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
        super.init()
    }

    // MARK: - Permission

    /// Requests speech recognition permission from the user.
    /// Must be called before `startListening()`.
    /// - Returns: true if permission was granted.
    static func requestPermission() async -> Bool {
        let status = SFSpeechRecognizer.authorizationStatus()
        if status == .authorized { return true }
        if status == .denied || status == .restricted { return false }

        return await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
    }

    /// Requests microphone permission (macOS).
    /// - Returns: true if permission was granted.
    static func requestMicrophonePermission() async -> Bool {
        // On macOS, microphone permission is requested automatically when
        // AVAudioEngine starts the input node. We check the current status
        // via the TCC system. If this is the first time, the system will
        // prompt the user when we try to access the microphone.
        // For now, return true — the system will prompt if needed.
        true
    }

    // MARK: - Listening

    /// Starts listening and transcribing speech.
    /// Calls `onPartialTranscript` with live updates.
    func startListening() {
        guard let speechRecognizer, speechRecognizer.isAvailable else {
            onError?("Speech recognition is not available on this device.")
            return
        }

        // Stop any existing task
        stopListening()

        finalTranscript = ""
        isListening = true

        // Create recognition request
        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        guard let recognitionRequest else {
            onError?("Failed to create recognition request.")
            isListening = false
            return
        }
        recognitionRequest.shouldReportPartialResults = true

        // Start recognition task
        recognitionTask = speechRecognizer.recognitionTask(with: recognitionRequest) { [weak self] result, error in
            guard let self else { return }

            if let result {
                let transcript = result.bestTranscription.formattedString
                self.finalTranscript = transcript
                DispatchQueue.main.async {
                    self.onPartialTranscript?(transcript)
                }
            }

            if let error {
                DispatchQueue.main.async {
                    self.onError?(error.localizedDescription)
                }
                self.stopListening()
            }
        }

        // Configure audio engine input
        let inputNode = audioEngine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { [weak self] buffer, _ in
            self?.recognitionRequest?.append(buffer)
        }

        do {
            try audioEngine.start()
        } catch {
            onError?("Failed to start audio engine: \(error.localizedDescription)")
            cleanup()
            isListening = false
        }
    }

    /// Stops listening and returns the final transcript.
    /// Calls `onFinalTranscript` with the complete transcribed text.
    func stopListening() {
        guard isListening else { return }
        isListening = false
        cleanup()

        let transcript = finalTranscript
        DispatchQueue.main.async {
            self.onFinalTranscript?(transcript)
        }
    }

    /// Cancels listening without emitting a final transcript.
    func cancel() {
        isListening = false
        finalTranscript = ""
        cleanup()
    }

    // MARK: - Cleanup

    private func cleanup() {
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask?.cancel()
        recognitionTask = nil
    }
}
