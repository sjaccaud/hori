import Foundation

/// HTTP client for talking to aios-core.
///
/// Phase 1 uses the `/v1/voice/chat` endpoint (text in → text + audio out).
/// We use only the text field of the response; audio is ignored until Phase 3.
/// History is sent with each request so HORI has conversation context.
///
/// The client is a thin wrapper around URLSession — no special retry,
/// no caching, no streaming. Those come in later phases if needed.
/// Errors are surfaced as `HoriClientError` so the UI can show
/// actionable messages rather than raw NSError strings.
struct HoriClient {

    /// The base URL of the aios-core server (e.g. "https://my-tailnet.ts.net:5680").
    let baseURL: URL

    /// The URLSession used for requests. Default is `.shared`.
    /// Injected in tests via a mock URLProtocol.
    var session: URLSession = .shared

    // MARK: - Request/Response Types

    /// Request body for POST /v1/voice/chat.
    /// Matches the `VoiceChatRequest` Pydantic model in aios_core/main.py.
    struct VoiceChatRequest: Encodable {
        let text: String
        let voice: String
        let speed: Double
        let history: [HistoryEntry]

        struct HistoryEntry: Encodable {
            let role: String
            let content: String
        }
    }

    /// Response body from POST /v1/voice/chat.
    /// Matches the dict returned by `voice_chat()` in aios_core/main.py.
    struct VoiceChatResponse: Decodable {
        let text: String
        let audio: String?
        let audioFormat: String?
        let conversationId: String?

        enum CodingKeys: String, CodingKey {
            case text
            case audio
            case audioFormat = "audio_format"
            case conversationId = "conversation_id"
        }
    }

    // MARK: - Send

    /// Sends a text message to HORI and returns the reply text.
    ///
    /// - Parameters:
    ///   - text: The user's message.
    ///   - history: Prior conversation turns (role + content), most recent last.
    ///   - voice: The TTS voice name (ignored in Phase 1 — we only use text).
    /// - Returns: HORI's reply text.
    /// - Throws: `HoriClientError` for network, decoding, or server errors.
    func sendMessage(
        _ text: String,
        history: [WindowState.Message],
        voice: String = "af_heart"
    ) async throws -> String {
        let url = baseURL.appendingPathComponent("v1/voice/chat")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 60

        let body = VoiceChatRequest(
            text: text,
            voice: voice,
            speed: 1.0,
            history: history.map { msg in
                .init(role: msg.role == .user ? "user" : "assistant",
                      content: msg.content)
            }
        )

        do {
            request.httpBody = try JSONEncoder().encode(body)
        } catch {
            throw HoriClientError.encodingFailed(error)
        }

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw HoriClientError.networkFailed(error)
        }

        guard let httpResponse = response as? HTTPURLResponse else {
            throw HoriClientError.invalidResponse
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(no body)"
            throw HoriClientError.serverError(statusCode: httpResponse.statusCode, body: body)
        }

        do {
            let decoded = try JSONDecoder().decode(VoiceChatResponse.self, from: data)
            return decoded.text
        } catch {
            throw HoriClientError.decodingFailed(error)
        }
    }
}

// MARK: - Errors

/// Errors that can occur when talking to aios-core.
///
/// Each case carries enough context for the UI to show an actionable
/// message. The associated values are not logged or displayed raw —
/// the UI maps each case to a user-facing string.
enum HoriClientError: LocalizedError {
    /// The request body could not be encoded to JSON.
    case encodingFailed(Error)
    /// The network request failed (no connection, timeout, DNS, etc.).
    case networkFailed(Error)
    /// The response was not an HTTPURLResponse (shouldn't happen with URLSession).
    case invalidResponse
    /// The server returned a non-2xx status code.
    case serverError(statusCode: Int, body: String)
    /// The response body could not be decoded as VoiceChatResponse.
    case decodingFailed(Error)

    var errorDescription: String? {
        switch self {
        case .encodingFailed:
            return "Could not prepare the message for sending."
        case .networkFailed:
            return "Could not reach HORI. Check your connection and the server URL."
        case .invalidResponse:
            return "HORI returned an unexpected response."
        case .serverError(let code, _):
            return "HORI returned an error (HTTP \(code))."
        case .decodingFailed:
            return "Could not understand HORI's reply."
        }
    }
}
