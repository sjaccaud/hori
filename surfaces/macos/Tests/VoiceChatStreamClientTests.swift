import Testing
import Foundation
@testable import HORI

/// Tests for the VoiceChatStreamClient — SSE parser for
/// /v1/voice/chat/stream.
///
/// The streaming endpoint sends these SSE events:
/// - event: text      data: {"text": "..."}
/// - event: audio     data: {"audio": "<base64>", "index": N}
/// - event: searching data: {"query": "..."}
/// - event: correction data: {"original": "...", "replacement": "..."}
/// - event: done      data: {"conversation_id": "...", "text": "..."}
/// - event: error     data: {"error": "..."}
///
/// Tests feed raw SSE data via testFeedSSEData() (same pattern as
/// PresenceClientTests) — no real network needed. Callbacks are
/// dispatched to the main queue, so tests are async with a brief
/// sleep to let the queue drain.
@Suite("VoiceChatStreamClient", .serialized)
struct VoiceChatStreamClientTests {

    // MARK: - Text Events

    @Test("Parses text event")
    func parsesTextEvent() async throws {
        let collector = StringCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { collector.add($0) },
            onAudio: { _, _ in },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { _ in }
        )
        client.testFeedSSEData("event: text\ndata: {\"text\": \"Hello\"}\n\n".data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.collect() == ["Hello"])
    }

    @Test("Parses multiple text events in sequence")
    func parsesMultipleTextEvents() async throws {
        let collector = StringCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { collector.add($0) },
            onAudio: { _, _ in },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { _ in }
        )
        let sse = """
        event: text
        data: {"text": "Hello"}

        event: text
        data: {"text": " world"}

        """.data(using: .utf8)! + Data("\n".utf8)
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.collect() == ["Hello", " world"])
    }

    // MARK: - Audio Events

    @Test("Parses audio event with base64 and index")
    func parsesAudioEvent() async throws {
        let collector = AudioCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { _ in },
            onAudio: { collector.add(data: $0, index: $1) },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { _ in }
        )
        // "dGVzdA==" is base64 for "test"
        let sse = "event: audio\ndata: {\"audio\": \"dGVzdA==\", \"index\": 0}\n\n".data(using: .utf8)!
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        let chunks = collector.collect()
        #expect(chunks.count == 1)
        #expect(chunks[0].data == Data([0x74, 0x65, 0x73, 0x74]))  // "test"
        #expect(chunks[0].index == 0)
    }

    @Test("Parses multiple audio events with increasing index")
    func parsesMultipleAudioEvents() async throws {
        let collector = IndexCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { _ in },
            onAudio: { _, index in collector.add(index) },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { _ in }
        )
        let sse = """
        event: audio
        data: {"audio": "dGVzdA==", "index": 0}

        event: audio
        data: {"audio": "dGVzdA==", "index": 1}

        event: audio
        data: {"audio": "dGVzdA==", "index": 2}

        """.data(using: .utf8)! + Data("\n".utf8)
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.collect() == [0, 1, 2])
    }

    // MARK: - Done Event

    @Test("Parses done event with conversation_id and full text")
    func parsesDoneEvent() async throws {
        let collector = DoneCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { _ in },
            onAudio: { _, _ in },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { collector.add(convId: $0, text: $1) },
            onError: { _ in }
        )
        let sse = """
        event: done
        data: {"conversation_id": "abc-123", "text": "Hello world"}

        """.data(using: .utf8)! + Data("\n".utf8)
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        let result = collector.collect()
        #expect(result.count == 1)
        #expect(result[0].convId == "abc-123")
        #expect(result[0].text == "Hello world")
    }

    // MARK: - Error Event

    @Test("Parses error event")
    func parsesErrorEvent() async throws {
        let collector = StringCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { _ in },
            onAudio: { _, _ in },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { collector.add($0) }
        )
        let sse = "event: error\ndata: {\"error\": \"LLM timeout\"}\n\n".data(using: .utf8)!
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.collect() == ["LLM timeout"])
    }

    // MARK: - Searching Event

    @Test("Parses searching event")
    func parsesSearchingEvent() async throws {
        let collector = StringCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { _ in },
            onAudio: { _, _ in },
            onSearching: { collector.add($0) },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { _ in }
        )
        let sse = "event: searching\ndata: {\"query\": \"latest news\"}\n\n".data(using: .utf8)!
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.collect() == ["latest news"])
    }

    // MARK: - Correction Event

    @Test("Parses correction event")
    func parsesCorrectionEvent() async throws {
        let collector = CorrectionCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { _ in },
            onAudio: { _, _ in },
            onSearching: { _ in },
            onCorrection: { collector.add(original: $0, replacement: $1) },
            onDone: { _, _ in },
            onError: { _ in }
        )
        let sse = """
        event: correction
        data: {"original": "I found 847 files", "replacement": "I cannot perform that action."}

        """.data(using: .utf8)! + Data("\n".utf8)
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        let result = collector.collect()
        #expect(result.count == 1)
        #expect(result[0].original == "I found 847 files")
        #expect(result[0].replacement == "I cannot perform that action.")
    }

    // MARK: - Edge Cases

    @Test("Ignores keepalive comments")
    func ignoresKeepalive() async throws {
        let collector = StringCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { collector.add($0) },
            onAudio: { _, _ in },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { _ in }
        )
        let sse = """
        : keepalive

        event: text
        data: {"text": "hi"}

        """.data(using: .utf8)! + Data("\n".utf8)
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.collect() == ["hi"])
    }

    @Test("Handles partial frames delivered across multiple chunks")
    func handlesPartialFrames() async throws {
        let collector = StringCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { collector.add($0) },
            onAudio: { _, _ in },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { _ in }
        )
        // Split a frame across two feed calls
        client.testFeedSSEData("event: text\nda".data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.collect().isEmpty)  // not complete yet
        client.testFeedSSEData("ta: {\"text\": \"hi\"}\n\n".data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.collect() == ["hi"])
    }

    @Test("Ignores unknown event types")
    func ignoresUnknownEvents() async throws {
        let collector = CallbackCounter()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { _ in collector.increment() },
            onAudio: { _, _ in collector.increment() },
            onSearching: { _ in collector.increment() },
            onCorrection: { _, _ in collector.increment() },
            onDone: { _, _ in collector.increment() },
            onError: { _ in collector.increment() }
        )
        let sse = """
        event: unknown
        data: {"foo": "bar"}

        """.data(using: .utf8)! + Data("\n".utf8)
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.value == 0)
    }

    @Test("Ignores malformed JSON in data")
    func ignoresMalformedJSON() async throws {
        let collector = StringCollector()
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { collector.add($0) },
            onAudio: { _, _ in },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { _ in }
        )
        let sse = "event: text\ndata: {not valid json}\n\n".data(using: .utf8)!
        client.testFeedSSEData(sse)
        try await Task.sleep(for: .milliseconds(50))
        #expect(collector.collect().isEmpty)
    }

    // MARK: - Request Building

    @Test("Builds correct request URL")
    func buildsCorrectURL() {
        let client = VoiceChatStreamClient(
            baseURL: URL(string: "http://localhost:5680")!,
            onText: { _ in },
            onAudio: { _, _ in },
            onSearching: { _ in },
            onCorrection: { _, _ in },
            onDone: { _, _ in },
            onError: { _ in }
        )
        #expect(client.streamURL.absoluteString == "http://localhost:5680/v1/voice/chat/stream")
    }
}

// MARK: - Thread-Safe Collectors

private final class StringCollector {
    private let lock = NSLock()
    private var items: [String] = []

    func add(_ item: String) {
        lock.lock(); defer { lock.unlock() }
        items.append(item)
    }

    func collect() -> [String] {
        lock.lock(); defer { lock.unlock() }
        let copy = items
        items.removeAll()
        return copy
    }
}

private final class IndexCollector {
    private let lock = NSLock()
    private var items: [Int] = []

    func add(_ item: Int) {
        lock.lock(); defer { lock.unlock() }
        items.append(item)
    }

    func collect() -> [Int] {
        lock.lock(); defer { lock.unlock() }
        let copy = items
        items.removeAll()
        return copy
    }
}

private final class AudioCollector {
    private let lock = NSLock()
    private var items: [(data: Data, index: Int)] = []

    func add(data: Data, index: Int) {
        lock.lock(); defer { lock.unlock() }
        items.append((data, index))
    }

    func collect() -> [(data: Data, index: Int)] {
        lock.lock(); defer { lock.unlock() }
        let copy = items
        items.removeAll()
        return copy
    }
}

private final class DoneCollector {
    private let lock = NSLock()
    private var items: [(convId: String, text: String)] = []

    func add(convId: String, text: String) {
        lock.lock(); defer { lock.unlock() }
        items.append((convId, text))
    }

    func collect() -> [(convId: String, text: String)] {
        lock.lock(); defer { lock.unlock() }
        let copy = items
        items.removeAll()
        return copy
    }
}

private final class CorrectionCollector {
    private let lock = NSLock()
    private var items: [(original: String, replacement: String)] = []

    func add(original: String, replacement: String) {
        lock.lock(); defer { lock.unlock() }
        items.append((original, replacement))
    }

    func collect() -> [(original: String, replacement: String)] {
        lock.lock(); defer { lock.unlock() }
        let copy = items
        items.removeAll()
        return copy
    }
}

private final class CallbackCounter {
    private let lock = NSLock()
    private var count = 0

    func increment() {
        lock.lock(); defer { lock.unlock() }
        count += 1
    }

    var value: Int {
        lock.lock(); defer { lock.unlock() }
        return count
    }
}
