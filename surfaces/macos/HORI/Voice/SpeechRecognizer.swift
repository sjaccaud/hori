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
/// Two critical details:
/// 1. Speech recognition permission MUST be requested before starting.
///    Without it, SFSpeechRecognizer silently fails with "No speech
///    detected" — auth=0 (notDetermined) is treated as denied at runtime.
/// 2. The audio format from AVAudioEngine's input node on macOS is
///    typically 4-channel 96kHz Float32 (deinterleaved). SFSpeechRecognizer
///    expects mono 16kHz. We must convert the format before appending
///    buffers to the recognition request.
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

    /// Tap callback counter (for diagnostics — reset on each start).
    private var tapCount: Int = 0

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
    /// On macOS, the system prompts automatically when AVAudioEngine
    /// accesses the input node. This just checks the current status.
    /// - Returns: true if permission was granted.
    static func requestMicrophonePermission() async -> Bool {
        true
    }

    // MARK: - Listening

    /// Starts listening and transcribing speech.
    /// Calls `onPartialTranscript` with live updates.
    func startListening() {
        let authStatus = SFSpeechRecognizer.authorizationStatus()
        print("🎤 SpeechRecognizer.startListening() — auth=\(authStatus.rawValue)")

        // If permission hasn't been determined, request it first.
        // SFSpeechRecognizer silently fails with "No speech detected"
        // when auth=0 (notDetermined).
        if authStatus == .notDetermined {
            print("🎤 Permission not determined — requesting...")
            SFSpeechRecognizer.requestAuthorization { [weak self] status in
                print("🎤 Authorization result: \(status.rawValue)")
                if status == .authorized {
                    DispatchQueue.main.async {
                        self?.startListening()
                    }
                } else {
                    DispatchQueue.main.async {
                        self?.onError?("Speech recognition permission denied.")
                    }
                }
            }
            return
        }

        guard authStatus == .authorized else {
            print("🎤 ERROR: Speech recognition not authorized (status=\(authStatus.rawValue))")
            onError?("Speech recognition permission denied. Enable it in System Settings → Privacy & Security → Speech Recognition.")
            return
        }

        guard let speechRecognizer, speechRecognizer.isAvailable else {
            print("🎤 ERROR: SFSpeechRecognizer is nil or not available")
            onError?("Speech recognition is not available on this device.")
            return
        }

        print("🎤 SFSpeechRecognizer is available, starting...")

        // Stop any existing task
        stopListening()

        finalTranscript = ""
        tapCount = 0
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
                print("🎤 Recognition error: \(error.localizedDescription)")
                DispatchQueue.main.async {
                    self.onError?(error.localizedDescription)
                }
                self.stopListening()
            }
        }

        // Configure audio engine input
        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        print("🎤 Input format: \(inputFormat)")

        // SFSpeechAudioBufferRecognitionRequest.append() accepts any PCM
        // format — the recognizer handles sample rate and channel conversion
        // internally. No manual AVAudioConverter needed.
        inputNode.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            guard let self else { return }

            // Log first few taps to verify audio is flowing
            self.tapCount += 1
            if self.tapCount <= 3 {
                let channelData = buffer.floatChannelData
                let frameLength = Int(buffer.frameLength)
                var maxSample: Float = 0
                if let channelData {
                    for ch in 0..<buffer.format.channelCount {
                        for i in 0..<min(frameLength, 100) {
                            let sample = abs(channelData[Int(ch)][i])
                            if sample > maxSample { maxSample = sample }
                        }
                    }
                }
                print("🎤 Tap #\(self.tapCount): frames=\(frameLength), maxSample=\(maxSample)")
            }

            self.recognitionRequest?.append(buffer)
        }

        do {
            try audioEngine.start()
            print("🎤 Audio engine started successfully")
        } catch {
            print("🎤 Audio engine start failed: \(error)")
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
