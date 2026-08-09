import Foundation
import AVFoundation

/// A queue-based WAV audio player for streaming TTS audio.
///
/// Audio chunks arrive from the /v1/voice/chat/stream SSE endpoint
/// with an index (sentence number). They may arrive out of order.
/// This player maintains an ordered queue and plays chunks in index
/// order, notifying the caller when playback starts and finishes.
///
/// Queueing and playback are separated: `enqueue` adds to the queue,
/// `startPlayback` begins playing. This makes the queue logic testable
/// without audio playback interfering.
///
/// Traces to: docs/roadmap.md MAC-3 (Voice Conversation), Phase 3.
final class AudioPlayer: NSObject {

    /// Playback state.
    enum State: Equatable {
        case idle       // No audio queued or playing
        case queued     // Audio in queue, not yet playing
        case playing    // Currently playing audio
    }

    /// Current state.
    private(set) var state: State = .idle

    /// Whether audio is currently playing.
    var isPlaying: Bool { state == .playing }

    /// Whether there are chunks waiting to be played.
    var hasPendingChunks: Bool { !queue.isEmpty }

    /// The indices currently in the queue, in order.
    var queuedIndices: [Int] { queue.map { $0.index } }

    /// The next expected index from the stream.
    private(set) var nextExpectedIndex: Int = 0

    /// The ordered audio queue.
    private var queue: [(index: Int, data: Data)] = []

    /// The current AVAudioPlayer instance.
    private var currentPlayer: AVAudioPlayer?

    /// Called when playback starts (first chunk begins playing).
    var onPlaybackStarted: (() -> Void)?

    /// Called when all queued audio has finished playing.
    var onPlaybackFinished: (() -> Void)?

    override init() {
        super.init()
    }

    // MARK: - Queue Management

    /// Enqueues an audio chunk at the given index.
    /// Chunks are sorted by index — they may arrive out of order.
    /// Does not start playback — call `startPlayback()` to begin.
    func enqueue(_ data: Data, index: Int) {
        queue.append((index, data))
        queue.sort { $0.index < $1.index }
        nextExpectedIndex = max(nextExpectedIndex, index + 1)
        if state == .idle {
            state = .queued
        }
    }

    /// Starts playing chunks from the queue. No-op if already playing
    /// or if the queue is empty.
    func startPlayback() {
        guard state != .playing else { return }
        playNext()
    }

    /// Clears the queue and stops any current playback.
    func clear() {
        queue.removeAll()
        currentPlayer?.stop()
        currentPlayer = nil
        state = .idle
    }

    /// Resets the player to its initial state.
    func reset() {
        clear()
        nextExpectedIndex = 0
    }

    // MARK: - Playback

    /// Plays the next chunk in the queue (if any).
    private func playNext() {
        guard !queue.isEmpty else {
            state = .idle
            onPlaybackFinished?()
            return
        }

        let chunk = queue.removeFirst()
        do {
            let player = try AVAudioPlayer(data: chunk.data)
            player.delegate = self
            currentPlayer = player
            state = .playing
            onPlaybackStarted?()
            player.play()
        } catch {
            // Skip this chunk and try the next
            playNext()
        }
    }
}

// MARK: - AVAudioPlayerDelegate

extension AudioPlayer: AVAudioPlayerDelegate {

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        currentPlayer = nil
        if queue.isEmpty {
            state = .idle
            onPlaybackFinished?()
        } else {
            playNext()
        }
    }
}
