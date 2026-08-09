import Testing
import Foundation
import AVFoundation
@testable import HORI

/// Tests for the AudioPlayer — a queue-based WAV player that plays
/// audio chunks in order as they arrive from the streaming endpoint.
///
/// The player maintains an ordered queue indexed by the `index` field
/// from the SSE audio event. Chunks may arrive out of order; the player
/// plays them in index order.
@Suite("AudioPlayer")
struct AudioPlayerTests {

    @Test("AudioPlayer starts idle")
    func startsIdle() {
        let player = AudioPlayer()
        #expect(player.state == .idle)
        #expect(player.isPlaying == false)
    }

    @Test("Enqueue sets state to queued if idle")
    func enqueueSetsQueued() {
        let player = AudioPlayer()
        player.enqueue(makeWavData(), index: 0)
        #expect(player.state == .queued)
    }

    @Test("Queue maintains order by index")
    func queueMaintainsOrder() {
        let player = AudioPlayer()
        // Enqueue out of order
        player.enqueue(makeWavData(), index: 2)
        player.enqueue(makeWavData(), index: 0)
        player.enqueue(makeWavData(), index: 1)
        #expect(player.queuedIndices == [0, 1, 2])
    }

    @Test("Clear empties the queue")
    func clearEmptiesQueue() {
        let player = AudioPlayer()
        player.enqueue(makeWavData(), index: 0)
        player.enqueue(makeWavData(), index: 1)
        player.clear()
        #expect(player.queuedIndices.isEmpty)
        #expect(player.state == .idle)
    }

    @Test("Has pending chunks returns true when queued")
    func hasPendingChunks() {
        let player = AudioPlayer()
        #expect(player.hasPendingChunks == false)
        player.enqueue(makeWavData(), index: 0)
        #expect(player.hasPendingChunks == true)
    }

    @Test("Next expected index starts at 0")
    func nextExpectedIndexStartsAtZero() {
        let player = AudioPlayer()
        #expect(player.nextExpectedIndex == 0)
    }

    @Test("Next expected index advances after enqueue")
    func nextExpectedIndexAdvances() {
        let player = AudioPlayer()
        player.enqueue(makeWavData(), index: 0)
        player.enqueue(makeWavData(), index: 1)
        #expect(player.nextExpectedIndex == 2)
    }

    @Test("Reset clears queue and state")
    func resetClearsAll() {
        let player = AudioPlayer()
        player.enqueue(makeWavData(), index: 0)
        player.reset()
        #expect(player.queuedIndices.isEmpty)
        #expect(player.state == .idle)
        #expect(player.nextExpectedIndex == 0)
    }

    // MARK: - Helpers

    /// Creates a minimal valid WAV file (44-byte header + 4 bytes of silence).
    private func makeWavData() -> Data {
        var data = Data()
        // RIFF header
        data.append(contentsOf: [0x52, 0x49, 0x46, 0x46])  // "RIFF"
        let fileSize: UInt32 = 36 + 4  // 4 bytes of audio data
        data.append(contentsOf: withUnsafeBytes(of: fileSize.littleEndian) { Array($0) })
        data.append(contentsOf: [0x57, 0x41, 0x56, 0x45])  // "WAVE"
        // fmt chunk
        data.append(contentsOf: [0x66, 0x6D, 0x74, 0x20])  // "fmt "
        let fmtSize: UInt32 = 16
        data.append(contentsOf: withUnsafeBytes(of: fmtSize.littleEndian) { Array($0) })
        let audioFormat: UInt16 = 1  // PCM
        data.append(contentsOf: withUnsafeBytes(of: audioFormat.littleEndian) { Array($0) })
        let numChannels: UInt16 = 1
        data.append(contentsOf: withUnsafeBytes(of: numChannels.littleEndian) { Array($0) })
        let sampleRate: UInt32 = 24000
        data.append(contentsOf: withUnsafeBytes(of: sampleRate.littleEndian) { Array($0) })
        let byteRate: UInt32 = 24000 * 2
        data.append(contentsOf: withUnsafeBytes(of: byteRate.littleEndian) { Array($0) })
        let blockAlign: UInt16 = 2
        data.append(contentsOf: withUnsafeBytes(of: blockAlign.littleEndian) { Array($0) })
        let bitsPerSample: UInt16 = 16
        data.append(contentsOf: withUnsafeBytes(of: bitsPerSample.littleEndian) { Array($0) })
        // data chunk
        data.append(contentsOf: [0x64, 0x61, 0x74, 0x61])  // "data"
        let dataSize: UInt32 = 4
        data.append(contentsOf: withUnsafeBytes(of: dataSize.littleEndian) { Array($0) })
        data.append(contentsOf: [0x00, 0x00, 0x00, 0x00])  // 4 bytes of silence
        return data
    }
}
