import Foundation

/// SSE (Server-Sent Events) client for the /v1/presence endpoint.
///
/// macOS has no native EventSource equivalent, so this is a thin SSE
/// parser built on URLSessionDataDelegate. It connects to the server,
/// reads the streaming response, and parses SSE frames:
///
/// - `event: state\ndata: {"state": "idle"}\n\n` → state change
/// - `: keepalive\n\n` → comment (ignored)
///
/// On disconnect, it auto-reconnects after a delay. The current
/// presence state is published via a callback, not @Observable —
/// the caller (SharedAppState) bridges it to the observable world.
///
/// Traces to: UX-1.3 (ambient presence), Manifesto Pillar V (visible
/// autonomy) + IV (presence).
final class PresenceClient: NSObject {

    /// The base URL of the aios-core server.
    let baseURL: URL

    /// Called when a presence state is received from the stream.
    /// Always called on the main thread.
    let onStateChange: (PresenceState) -> Void

    /// Called when the connection state changes.
    /// Always called on the main thread.
    let onConnectionChange: (Bool) -> Void

    /// The URLSession used for the SSE connection.
    ///
    /// Created lazily on `start()` with this client as its delegate —
    /// `URLSessionDataDelegate` methods are how the client receives
    /// streamed data. The shared `URLSession` cannot have a custom
    /// delegate, so this client owns its own session.
    ///
    /// `nil` until `start()` is called, and set back to `nil` after
    /// `stop()` invalidates the session. This prevents the retain cycle
    /// where the session holds this client as its delegate.
    private var session: URLSession?

    /// The current data task (nil when not connected).
    private var task: URLSessionDataTask?

    /// Buffer for incomplete SSE frames.
    private var buffer = Data()

    /// Whether the client is intentionally stopped (don't reconnect).
    private var stopped = false

    /// Reconnect delay in seconds (doubles on consecutive failures).
    private let initialReconnectDelay: TimeInterval = 1.0
    private let maxReconnectDelay: TimeInterval = 30.0
    private var currentReconnectDelay: TimeInterval = 1.0

    init(baseURL: URL,
         session: URLSession? = nil,
         onStateChange: @escaping (PresenceState) -> Void,
         onConnectionChange: @escaping (Bool) -> Void) {
        self.baseURL = baseURL
        self.session = session
        self.onStateChange = onStateChange
        self.onConnectionChange = onConnectionChange
        super.init()
    }

    // MARK: - Connection

    /// Starts listening to the presence stream. Safe to call multiple times.
    ///
    /// Creates a delegate-backed URLSession if one doesn't exist, then
    /// opens the SSE data task. The shared `URLSession` cannot be used
    /// as a delegate (its delegate methods would never fire), which is
    /// why this client creates its own session on demand.
    func start() {
        stopped = false
        if session == nil {
            session = URLSession(configuration: .default, delegate: self, delegateQueue: .main)
        }
        connect()
    }

    /// Stops listening and prevents reconnection.
    ///
    /// Cancels the current data task and invalidates the URLSession,
    /// breaking the retain cycle where the session holds this client
    /// as its delegate.
    func stop() {
        stopped = true
        task?.cancel()
        session?.invalidateAndCancel()
        session = nil
        task = nil
        buffer = Data()
        DispatchQueue.main.async { self.onConnectionChange(false) }
    }

    /// Opens the SSE connection.
    private func connect() {
        guard let session = session else { return }

        let url = baseURL.appendingPathComponent("v1/presence")
        var request = URLRequest(url: url)
        request.timeoutInterval = .infinity  // SSE is a long-lived stream
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

        task = session.dataTask(with: request)
        task?.resume()
    }

    /// Reconnects after a delay, with exponential backoff.
    private func scheduleReconnect() {
        guard !stopped else { return }
        let delay = currentReconnectDelay
        currentReconnectDelay = min(currentReconnectDelay * 2, maxReconnectDelay)
        DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, !self.stopped else { return }
            self.connect()
        }
    }
}

// MARK: - URLSessionDataDelegate

extension PresenceClient: URLSessionDataDelegate {

    func urlSession(_ session: URLSession,
                    dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        if let httpResponse = response as? HTTPURLResponse,
           (200...299).contains(httpResponse.statusCode) {
            DispatchQueue.main.async { self.onConnectionChange(true) }
            currentReconnectDelay = initialReconnectDelay  // reset backoff on success
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        parseBuffer()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        DispatchQueue.main.async { self.onConnectionChange(false) }
        if error != nil {
            scheduleReconnect()
        }
    }
}

// MARK: - SSE Parsing

extension PresenceClient {

    /// The full URL of the presence endpoint (for testing/verification).
    var presenceURL: URL {
        baseURL.appendingPathComponent("v1/presence")
    }

    /// Feeds raw SSE data into the parser buffer and triggers parsing.
    /// Used by tests to feed data without a real network connection.
    func testFeedSSEData(_ data: Data) {
        buffer.append(data)
        parseBuffer()
    }

    /// Parses complete SSE frames from the buffer.
    /// A frame is delimited by `\n\n`. Incomplete frames stay in the buffer.
    private func parseBuffer() {
        while let frameEnd = buffer.range(of: Data("\n\n".utf8)) {
            let frameData = buffer.subdata(in: 0..<frameEnd.lowerBound)
            buffer.removeSubrange(0..<frameEnd.upperBound)
            parseFrame(frameData)
        }
    }

    /// Parses a single SSE frame and extracts the state if present.
    private func parseFrame(_ data: Data) {
        guard let text = String(data: data, encoding: .utf8) else { return }

        var eventType = ""
        var dataLines: [String] = []

        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix("event:") {
                eventType = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                dataLines.append(String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces))
            }
            // Lines starting with ":" are comments (keepalive) — ignored.
        }

        guard eventType == "state" else { return }
        let dataString = dataLines.joined(separator: "\n")
        guard let jsonData = dataString.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
              let stateString = json["state"] as? String else { return }

        if let state = PresenceState(rawValue: stateString) {
            DispatchQueue.main.async { self.onStateChange(state) }
        }
    }
}
