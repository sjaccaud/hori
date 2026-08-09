import Testing
import SwiftUI
@testable import HORI

/// Tests for the voice state machine.
///
/// The voice conversation has a strict state machine:
///   idle → listening → processing → speaking → idle
///
/// Illegal transitions must be rejected (e.g. can't go from idle
/// directly to speaking). This prevents race conditions when the
/// user releases PTT while a previous response is still playing.
@Suite("Voice State Machine")
struct VoiceStateTests {

    @Test("VoiceState starts idle")
    func startsIdle() {
        let state = VoiceState()
        #expect(state.phase == .idle)
    }

    @Test("PTT press: idle → listening")
    func startListening() {
        let state = VoiceState()
        state.startListening()
        #expect(state.phase == .listening)
    }

    @Test("PTT release with text: listening → processing")
    func stopListeningWithText() {
        let state = VoiceState()
        state.startListening()
        state.stopListening(transcribedText: "hello")
        #expect(state.phase == .processing)
    }

    @Test("PTT release with empty text: listening → idle (cancel)")
    func stopListeningEmptyText() {
        let state = VoiceState()
        state.startListening()
        state.stopListening(transcribedText: "")
        #expect(state.phase == .idle)
    }

    @Test("First audio arrives: processing → speaking")
    func firstAudioArrives() {
        let state = VoiceState()
        state.startListening()
        state.stopListening(transcribedText: "hello")
        state.audioPlaybackStarted()
        #expect(state.phase == .speaking)
    }

    @Test("All audio played: speaking → idle")
    func audioPlaybackFinished() {
        let state = VoiceState()
        state.startListening()
        state.stopListening(transcribedText: "hello")
        state.audioPlaybackStarted()
        state.audioPlaybackFinished()
        #expect(state.phase == .idle)
    }

    @Test("Error during processing: → idle")
    func errorDuringProcessing() {
        let state = VoiceState()
        state.startListening()
        state.stopListening(transcribedText: "hello")
        state.reset()
        #expect(state.phase == .idle)
    }

    @Test("Error during listening: → idle")
    func errorDuringListening() {
        let state = VoiceState()
        state.startListening()
        state.reset()
        #expect(state.phase == .idle)
    }

    @Test("Can't start listening while processing")
    func cantListenWhileProcessing() {
        let state = VoiceState()
        state.startListening()
        state.stopListening(transcribedText: "hello")
        state.startListening()  // should be rejected
        #expect(state.phase == .processing)
    }

    @Test("Can't start listening while speaking")
    func cantListenWhileSpeaking() {
        let state = VoiceState()
        state.startListening()
        state.stopListening(transcribedText: "hello")
        state.audioPlaybackStarted()
        state.startListening()  // should be rejected
        #expect(state.phase == .speaking)
    }

    @Test("Transcribed text is stored after PTT release")
    func transcribedTextStored() {
        let state = VoiceState()
        state.startListening()
        state.stopListening(transcribedText: "hello world")
        #expect(state.transcribedText == "hello world")
    }

    @Test("Transcribed text clears on reset")
    func transcribedTextClearsOnReset() {
        let state = VoiceState()
        state.startListening()
        state.stopListening(transcribedText: "hello")
        state.reset()
        #expect(state.transcribedText == "")
    }

    @Test("Partial transcript updates during listening")
    func partialTranscript() {
        let state = VoiceState()
        state.startListening()
        state.updatePartialTranscript("hello")
        #expect(state.partialTranscript == "hello")
        state.updatePartialTranscript("hello world")
        #expect(state.partialTranscript == "hello world")
    }

    @Test("Partial transcript clears on stop")
    func partialTranscriptClearsOnStop() {
        let state = VoiceState()
        state.startListening()
        state.updatePartialTranscript("hello")
        state.stopListening(transcribedText: "hello world")
        #expect(state.partialTranscript == "")
        #expect(state.transcribedText == "hello world")
    }

    @Test("isBusy is true for listening, processing, speaking")
    func isBusy() {
        let state = VoiceState()
        #expect(state.isBusy == false)

        state.startListening()
        #expect(state.isBusy == true)

        state.stopListening(transcribedText: "hi")
        #expect(state.isBusy == true)

        state.audioPlaybackStarted()
        #expect(state.isBusy == true)

        state.audioPlaybackFinished()
        #expect(state.isBusy == false)
    }
}
