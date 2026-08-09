import Testing
import Foundation
@testable import HORI

/// Tests for the PresenceClient SSE parser and state mapping.
///
/// The SSE parsing logic is tested directly by feeding raw SSE byte
/// streams through the parser. The network connection itself is not
/// mocked here — it's covered by integration testing on the Mac.
@Suite("PresenceClient")
struct PresenceClientTests {

    // MARK: - SSE Frame Parsing

    @Test("Parses a state event with idle")
    func parsesIdleState() async throws {
        let states = PresenceStateCollector()
        let client = PresenceClient(
            baseURL: URL(string: "http://localhost")!,
            onStateChange: { states.add($0) },
            onConnectionChange: { _ in }
        )

        // Feed a complete SSE frame into the parser.
        let frame = "event: state\ndata: {\"state\": \"idle\"}\n\n"
        client.testFeedSSEData(frame.data(using: .utf8)!)

        // The callback is async (dispatched to main), so wait briefly.
        try await Task.sleep(for: .milliseconds(50))

        let collected = states.collect()
        #expect(collected == [.idle])
    }

    @Test("Parses a state event with thinking")
    func parsesThinkingState() async throws {
        let states = PresenceStateCollector()
        let client = PresenceClient(
            baseURL: URL(string: "http://localhost")!,
            onStateChange: { states.add($0) },
            onConnectionChange: { _ in }
        )

        let frame = "event: state\ndata: {\"state\": \"thinking\"}\n\n"
        client.testFeedSSEData(frame.data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))

        let collected = states.collect()
        #expect(collected == [.thinking])
    }

    @Test("Parses a state event with has_nudge")
    func parsesHasNudgeState() async throws {
        let states = PresenceStateCollector()
        let client = PresenceClient(
            baseURL: URL(string: "http://localhost")!,
            onStateChange: { states.add($0) },
            onConnectionChange: { _ in }
        )

        let frame = "event: state\ndata: {\"state\": \"has_nudge\"}\n\n"
        client.testFeedSSEData(frame.data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))

        let collected = states.collect()
        #expect(collected == [.hasNudge])
    }

    @Test("Ignores keepalive comments")
    func ignoresKeepalive() async throws {
        let states = PresenceStateCollector()
        let client = PresenceClient(
            baseURL: URL(string: "http://localhost")!,
            onStateChange: { states.add($0) },
            onConnectionChange: { _ in }
        )

        // A keepalive comment followed by a real state event.
        let data = ": keepalive\n\nevent: state\ndata: {\"state\": \"idle\"}\n\n"
        client.testFeedSSEData(data.data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))

        let collected = states.collect()
        #expect(collected == [.idle])
    }

    @Test("Handles partial frames delivered across multiple chunks")
    func handlesPartialFrames() async throws {
        let states = PresenceStateCollector()
        let client = PresenceClient(
            baseURL: URL(string: "http://localhost")!,
            onStateChange: { states.add($0) },
            onConnectionChange: { _ in }
        )

        // Send the frame in two chunks — the parser should buffer
        // the incomplete frame and only emit when it's complete.
        let part1 = "event: state\ndata: {\"state\": \"idl"
        let part2 = "e\"}\n\n"
        client.testFeedSSEData(part1.data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))
        #expect(states.collect().isEmpty)

        client.testFeedSSEData(part2.data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))
        #expect(states.collect() == [.idle])
    }

    @Test("Handles multiple frames in a single chunk")
    func handlesMultipleFrames() async throws {
        let states = PresenceStateCollector()
        let client = PresenceClient(
            baseURL: URL(string: "http://localhost")!,
            onStateChange: { states.add($0) },
            onConnectionChange: { _ in }
        )

        let data = """
        event: state\ndata: {"state": "idle"}\n\n\
        event: state\ndata: {"state": "thinking"}\n\n\
        event: state\ndata: {"state": "idle"}\n\n
        """
        client.testFeedSSEData(data.data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))

        let collected = states.collect()
        #expect(collected == [.idle, .thinking, .idle])
    }

    @Test("Ignores unknown event types")
    func ignoresUnknownEvents() async throws {
        let states = PresenceStateCollector()
        let client = PresenceClient(
            baseURL: URL(string: "http://localhost")!,
            onStateChange: { states.add($0) },
            onConnectionChange: { _ in }
        )

        let data = "event: ping\ndata: {}\n\nevent: state\ndata: {\"state\": \"idle\"}\n\n"
        client.testFeedSSEData(data.data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))

        let collected = states.collect()
        #expect(collected == [.idle])
    }

    @Test("Ignores malformed JSON in data")
    func ignoresMalformedJSON() async throws {
        let states = PresenceStateCollector()
        let client = PresenceClient(
            baseURL: URL(string: "http://localhost")!,
            onStateChange: { states.add($0) },
            onConnectionChange: { _ in }
        )

        let data = "event: state\ndata: not json\n\n"
        client.testFeedSSEData(data.data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))

        #expect(states.collect().isEmpty)
    }

    @Test("Ignores unknown state values")
    func ignoresUnknownStates() async throws {
        let states = PresenceStateCollector()
        let client = PresenceClient(
            baseURL: URL(string: "http://localhost")!,
            onStateChange: { states.add($0) },
            onConnectionChange: { _ in }
        )

        let data = "event: state\ndata: {\"state\": \"unknown_state\"}\n\n"
        client.testFeedSSEData(data.data(using: .utf8)!)
        try await Task.sleep(for: .milliseconds(50))

        #expect(states.collect().isEmpty)
    }

    // MARK: - URL Construction

    @Test("Connects to /v1/presence path")
    func urlConstruction() {
        let client = PresenceClient(
            baseURL: URL(string: "http://example.com:5680")!,
            onStateChange: { _ in },
            onConnectionChange: { _ in }
        )

        let url = client.presenceURL
        #expect(url.absoluteString == "http://example.com:5680/v1/presence")
    }
}

// MARK: - Test Helpers

/// Thread-safe collector for presence states received via callback.
private final class PresenceStateCollector {
    private var states: [PresenceState] = []
    private let lock = NSLock()

    func add(_ state: PresenceState) {
        lock.lock()
        defer { lock.unlock() }
        states.append(state)
    }

    func collect() -> [PresenceState] {
        lock.lock()
        defer { lock.unlock() }
        let copy = states
        states.removeAll()
        return copy
    }
}
