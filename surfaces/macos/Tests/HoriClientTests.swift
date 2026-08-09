import Testing
import Foundation
@testable import HORI

/// Tests for the HoriClient HTTP client.
///
/// Uses a mock URLProtocol to intercept requests without making real
/// network calls. Verifies request encoding, response decoding, and
/// error handling for the /v1/voice/chat endpoint.
@Suite("HoriClient")
struct HoriClientTests {

    // MARK: - Request Encoding

    @Test("Request body encodes text and history correctly")
    func requestEncoding() async throws {
        MockURLProtocol.reset()
        MockURLProtocol.responseData = """
        {"text": "Hello!", "audio": "", "audio_format": "wav", "conversation_id": "abc"}
        """.data(using: .utf8)!
        MockURLProtocol.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://example.com:5680")!,
            session: makeSession()
        )

        let history: [WindowState.Message] = [
            .init(role: .user, content: "Hi"),
            .init(role: .hori, content: "Hello there!"),
        ]

        _ = try await client.sendMessage("What's up?", history: history)

        // Verify the request was captured.
        let request = try #require(MockURLProtocol.lastRequest)
        #expect(request.url?.absoluteString == "https://example.com:5680/v1/voice/chat")
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")

        // Verify the body.
        let body = try #require(request.httpBody)
        let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        let dict = try #require(json)
        #expect(dict["text"] as? String == "What's up?")
        #expect(dict["voice"] as? String == "af_heart")
        #expect(dict["speed"] as? Double == 1.0)

        let historyArray = try #require(dict["history"] as? [[String: String]])
        #expect(historyArray.count == 2)
        #expect(historyArray[0]["role"] == "user")
        #expect(historyArray[0]["content"] == "Hi")
        #expect(historyArray[1]["role"] == "assistant")
        #expect(historyArray[1]["content"] == "Hello there!")
    }

    @Test("User messages map to 'user' role, HORI messages to 'assistant'")
    func roleMapping() async throws {
        MockURLProtocol.reset()
        MockURLProtocol.responseData = """
        {"text": "ok", "audio": null, "audio_format": null, "conversation_id": null}
        """.data(using: .utf8)!
        MockURLProtocol.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession()
        )

        let history: [WindowState.Message] = [
            .init(role: .user, content: "question"),
            .init(role: .hori, content: "answer"),
        ]

        _ = try await client.sendMessage("next", history: history)

        let body = try #require(MockURLProtocol.lastRequest?.httpBody)
        let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        let historyArray = try #require(json?["history"] as? [[String: String]])
        #expect(historyArray[0]["role"] == "user")
        #expect(historyArray[1]["role"] == "assistant")
    }

    // MARK: - Response Decoding

    @Test("Decodes a successful response and returns the text")
    func responseDecoding() async throws {
        MockURLProtocol.reset()
        MockURLProtocol.responseData = """
        {"text": "Hello from HORI!", "audio": "base64data", "audio_format": "wav", "conversation_id": "conv-123"}
        """.data(using: .utf8)!
        MockURLProtocol.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession()
        )

        let reply = try await client.sendMessage("Hi", history: [])
        #expect(reply == "Hello from HORI!")
    }

    @Test("Handles response with null optional fields")
    func responseWithNulls() async throws {
        MockURLProtocol.reset()
        MockURLProtocol.responseData = """
        {"text": "Reply", "audio": null, "audio_format": null, "conversation_id": null}
        """.data(using: .utf8)!
        MockURLProtocol.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession()
        )

        let reply = try await client.sendMessage("Hi", history: [])
        #expect(reply == "Reply")
    }

    // MARK: - Error Handling

    @Test("Server error (non-2xx) throws serverError")
    func serverError() async throws {
        MockURLProtocol.reset()
        MockURLProtocol.responseData = "Internal Server Error".data(using: .utf8)!
        MockURLProtocol.statusCode = 500

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession()
        )

        await #expect(throws: HoriClientError.self) {
            _ = try await client.sendMessage("Hi", history: [])
        }
    }

    @Test("Malformed JSON throws decodingFailed")
    func decodingError() async throws {
        MockURLProtocol.reset()
        MockURLProtocol.responseData = "not json at all".data(using: .utf8)!
        MockURLProtocol.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession()
        )

        await #expect(throws: HoriClientError.self) {
            _ = try await client.sendMessage("Hi", history: [])
        }
    }

    @Test("Network failure throws networkFailed")
    func networkError() async throws {
        MockURLProtocol.reset()
        MockURLProtocol.error = URLError(.notConnectedToInternet)

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession()
        )

        await #expect(throws: HoriClientError.self) {
            _ = try await client.sendMessage("Hi", history: [])
        }
    }

    @Test("Error descriptions are human-readable")
    func errorDescriptions() {
        let networkErr = HoriClientError.networkFailed(URLError(.timedOut))
        #expect(networkErr.localizedDescription.contains("Could not reach HORI"))

        let serverErr = HoriClientError.serverError(statusCode: 500, body: "error")
        #expect(serverErr.localizedDescription.contains("HTTP 500"))

        let decodeErr = HoriClientError.decodingFailed(DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "test")))
        #expect(decodeErr.localizedDescription.contains("Could not understand"))
    }

    // MARK: - URL Construction

    @Test("BaseURL + path constructs the correct endpoint URL")
    func urlConstruction() async throws {
        MockURLProtocol.reset()
        MockURLProtocol.responseData = """
        {"text": "ok", "audio": null, "audio_format": null, "conversation_id": null}
        """.data(using: .utf8)!
        MockURLProtocol.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://my-tailnet.ts.net:5680")!,
            session: makeSession()
        )

        _ = try await client.sendMessage("test", history: [])
        let url = try #require(MockURLProtocol.lastRequest?.url)
        #expect(url.absoluteString == "https://my-tailnet.ts.net:5680/v1/voice/chat")
    }

    // MARK: - Helpers

    /// Creates a URLSession that routes all requests through MockURLProtocol.
    private func makeSession() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        return URLSession(configuration: config)
    }
}

// MARK: - Mock URLProtocol

/// A mock URLProtocol that intercepts requests and returns canned responses.
/// Used for testing HoriClient without real network calls.
///
/// Uses static vars for response config because URLProtocol instantiates
/// a new instance per request — instance properties on the mock can't be
/// shared with the test. The test sets the static vars before calling
/// the client, and the protocol reads them during startLoading().
final class MockURLProtocol: URLProtocol {

    // Static response configuration — set by the test before the request.
    static var responseData: Data = Data()
    static var statusCode: Int = 200
    static var error: Error? = nil

    // Captured request for verification — set by startLoading().
    static var lastRequest: URLRequest?

    /// Reset all static state between tests.
    static func reset() {
        responseData = Data()
        statusCode = 200
        error = nil
        lastRequest = nil
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        // Capture the request for test verification.
        MockURLProtocol.lastRequest = request

        if let error = MockURLProtocol.error {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }

        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: MockURLProtocol.statusCode,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!

        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: MockURLProtocol.responseData)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
