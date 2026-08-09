import Foundation

/// SSE client for the /v1/voice/chat/stream endpoint.
///
/// Sends a POST request with text + voice config, then parses the
/// streaming SSE response. Unlike PresenceClient (a persistent
/// auto-reconnecting stream), this is a one-shot request — one
/// conversation turn, then the stream ends with a `done` event.
///
/// SSE event types from the server:
/// - `text`      — text chunk (LLM streaming)
/// - `audio`     — base64 WAV audio chunk (TTS, per sentence)
/// - `searching` — reactive web search started
/// - `correction`— hallucination correction (original → replacement)
/// - `done`      — stream complete (conversation_id + full text)
/// - `error`     — error message
///
/// Traces to: docs/roadmap.md MAC-3 (Voice Conversation), Phase 3.
/// Server endpoint: services/aios_core/main.py → voice_chat_stream()
final class VoiceChatStreamClient: NSObject {

    let baseURL: URL

    // MARK: - Callbacks (all called on the main thread)

    let onText: (String) -> Void
    let onAudio: (Data, Int) -> Void
    let onSearching: (String) -> Void
    let onCorrection: (String, String) -> Void
    let onDone: (String, String) -> Void
    let onError: (String) -> Void

    // MARK: - Connection State

    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var buffer = Data()
    private var stopped = false

    init(baseURL: URL,
         onText: @escaping (String) -> Void,
         onAudio: @escaping (Data, Int) -> Void,
         onSearching: @escaping (String) -> Void,
         onCorrection: @escaping (String, String) -> Void,
         onDone: @escaping (String, String) -> Void,
         onError: @escaping (String) -> Void) {
        self.baseURL = baseURL
        self.onText = onText
        self.onAudio = onAudio
        self.onSearching = onSearching
        self.onCorrection = onCorrection
        self.onDone = onDone
        self.onError = onError
        super.init()
    }

    // MARK: - Stream URL

    var streamURL: URL {
        baseURL.appendingPathComponent("v1/voice/chat/stream")
    }

    // MARK: - Send

    /// Starts a streaming voice chat request.
    /// - Parameters:
    ///   - text: The transcribed user message
    ///   - voice: TTS voice name (e.g. "af_heart")
    ///   - speed: TTS speed (1.0 = normal)
    ///   - history: Previous conversation turns
    func send(text: String, voice: String = "af_heart", speed: Float = 1.0,
              history: [[String: String]] = []) {
        stopped = false
        buffer = Data()

        var request = URLRequest(url: streamURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.timeoutInterval = .infinity

        let body: [String: Any] = [
            "text": text,
            "voice": voice,
            "speed": speed,
            "history": history,
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)

        if session == nil {
            session = URLSession(configuration: .default, delegate: self, delegateQueue: .main)
        }
        task = session?.dataTask(with: request)
        task?.resume()
    }

    /// Cancels the current stream (if any).
    func cancel() {
        stopped = true
        task?.cancel()
        session?.invalidateAndCancel()
        session = nil
        task = nil
        buffer = Data()
    }

    // MARK: - Test Support

    /// Feeds raw SSE data into the parser buffer and triggers parsing.
    /// Used by tests to feed data without a real network connection.
    func testFeedSSEData(_ data: Data) {
        buffer.append(data)
        parseBuffer()
    }
}

// MARK: - URLSessionDataDelegate

extension VoiceChatStreamClient: URLSessionDataDelegate {

    func urlSession(_ session: URLSession,
                    dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        if let httpResponse = response as? HTTPURLResponse,
           !(200...299).contains(httpResponse.statusCode) {
            DispatchQueue.main.async {
                self.onError("Server error: \(httpResponse.statusCode)")
            }
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        parseBuffer()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        if let error, !stopped {
            DispatchQueue.main.async { self.onError(error.localizedDescription) }
        }
    }
}

// MARK: - SSE Parsing

extension VoiceChatStreamClient {

    /// Parses complete SSE frames from the buffer.
    private func parseBuffer() {
        while let frameEnd = buffer.range(of: Data("\n\n".utf8)) {
            let frameData = buffer.subdata(in: 0..<frameEnd.lowerBound)
            buffer.removeSubrange(0..<frameEnd.upperBound)
            parseFrame(frameData)
        }
    }

    /// Parses a single SSE frame and dispatches to the appropriate callback.
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

        guard !eventType.isEmpty else { return }
        let dataString = dataLines.joined(separator: "\n")
        guard let jsonData = dataString.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any] else {
            return
        }

        switch eventType {
        case "text":
            if let text = json["text"] as? String {
                DispatchQueue.main.async { self.onText(text) }
            }
        case "audio":
            if let audioB64 = json["audio"] as? String,
               let audioData = Data(base64Encoded: audioB64) {
                let index = json["index"] as? Int ?? 0
                DispatchQueue.main.async { self.onAudio(audioData, index) }
            }
        case "searching":
            if let query = json["query"] as? String {
                DispatchQueue.main.async { self.onSearching(query) }
            }
        case "correction":
            if let original = json["original"] as? String,
               let replacement = json["replacement"] as? String {
                DispatchQueue.main.async { self.onCorrection(original, replacement) }
            }
        case "done":
            if let convId = json["conversation_id"] as? String,
               let text = json["text"] as? String {
                DispatchQueue.main.async { self.onDone(convId, text) }
            }
        case "error":
            if let msg = json["error"] as? String {
                DispatchQueue.main.async { self.onError(msg) }
            }
        default:
            break  // unknown event type — ignore
        }
    }
}
