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
        let mock = MockURLProtocol()
        mock.responseData = """
        {"text": "Hello!", "audio": "", "audio_format": "wav", "conversation_id": "abc"}
        """.data(using: .utf8)!
        mock.statusCode = 200

        let session = makeSession(with: mock)
        let client = HoriClient(
            baseURL: URL(string: "https://example.com:5680")!,
            session: session
        )

        let history: [WindowState.Message] = [
            .init(role: .user, content: "Hi"),
            .init(role: .hori, content: "Hello there!"),
        ]

        _ = try await client.sendMessage("What's up?", history: history)

        // Verify the request was captured.
        let request = try #require(mock.lastRequest)
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
        let mock = MockURLProtocol()
        mock.responseData = """
        {"text": "ok", "audio": null, "audio_format": null, "conversation_id": null}
        """.data(using: .utf8)!
        mock.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession(with: mock)
        )

        let history: [WindowState.Message] = [
            .init(role: .user, content: "question"),
            .init(role: .hori, content: "answer"),
        ]

        _ = try await client.sendMessage("next", history: history)

        let body = try #require(mock.lastRequest?.httpBody)
        let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        let historyArray = try #require(json?["history"] as? [[String: String]])
        #expect(historyArray[0]["role"] == "user")
        #expect(historyArray[1]["role"] == "assistant")
    }

    // MARK: - Response Decoding

    @Test("Decodes a successful response and returns the text")
    func responseDecoding() async throws {
        let mock = MockURLProtocol()
        mock.responseData = """
        {"text": "Hello from HORI!", "audio": "base64data", "audio_format": "wav", "conversation_id": "conv-123"}
        """.data(using: .utf8)!
        mock.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession(with: mock)
        )

        let reply = try await client.sendMessage("Hi", history: [])
        #expect(reply == "Hello from HORI!")
    }

    @Test("Handles response with null optional fields")
    func responseWithNulls() async throws {
        let mock = MockURLProtocol()
        mock.responseData = """
        {"text": "Reply", "audio": null, "audio_format": null, "conversation_id": null}
        """.data(using: .utf8)!
        mock.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession(with: mock)
        )

        let reply = try await client.sendMessage("Hi", history: [])
        #expect(reply == "Reply")
    }

    // MARK: - Error Handling

    @Test("Server error (non-2xx) throws serverError")
    func serverError() async throws {
        let mock = MockURLProtocol()
        mock.responseData = "Internal Server Error".data(using: .utf8)!
        mock.statusCode = 500

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession(with: mock)
        )

        await #expect(throws: HoriClientError.self) {
            _ = try await client.sendMessage("Hi", history: [])
        }
    }

    @Test("Malformed JSON throws decodingFailed")
    func decodingError() async throws {
        let mock = MockURLProtocol()
        mock.responseData = "not json at all".data(using: .utf8)!
        mock.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession(with: mock)
        )

        await #expect(throws: HoriClientError.self) {
            _ = try await client.sendMessage("Hi", history: [])
        }
    }

    @Test("Network failure throws networkFailed")
    func networkError() async throws {
        let mock = MockURLProtocol()
        mock.error = URLError(.notConnectedToInternet)

        let client = HoriClient(
            baseURL: URL(string: "https://example.com")!,
            session: makeSession(with: mock)
        )

        await #expect(throws: HoriClientError.self) {
            _ = try await client.sendMessage("Hi", history: [])
        }
    }

    @Test("Error descriptions are human-readable")
    func errorDescriptions() {
        let networkErr = HoriClientError.networkFailed(URLError(.timedOut))
        #expect(networkErr.localizedDescription?.contains("Could not reach HORI") == true)

        let serverErr = HoriClientError.serverError(statusCode: 500, body: "error")
        #expect(serverErr.localizedDescription?.contains("HTTP 500") == true)

        let decodeErr = HoriClientError.decodingFailed(DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "test")))
        #expect(decodeErr.localizedDescription?.contains("Could not understand") == true)
    }

    // MARK: - URL Construction

    @Test("BaseURL + path constructs the correct endpoint URL")
    func urlConstruction() async throws {
        let mock = MockURLProtocol()
        mock.responseData = """
        {"text": "ok", "audio": null, "audio_format": null, "conversation_id": null}
        """.data(using: .utf8)!
        mock.statusCode = 200

        let client = HoriClient(
            baseURL: URL(string: "https://my-tailnet.ts.net:5680")!,
            session: makeSession(with: mock)
        )

        _ = try await client.sendMessage("test", history: [])
        let url = try #require(mock.lastRequest?.url)
        #expect(url.absoluteString == "https://my-tailnet.ts.net:5680/v1/voice/chat")
    }

    // MARK: - Helpers

    /// Creates a URLSession that routes all requests through the mock URLProtocol.
    private func makeSession(with mock: MockURLProtocol) -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        // Store the mock instance on the shared singleton so the protocol
        // handler can find it. This is a test-only pattern.
        MockURLProtocol.shared = mock
        return URLSession(configuration: config)
    }
}

// MARK: - Mock URLProtocol

/// A mock URLProtocol that intercepts requests and returns canned responses.
/// Used for testing HoriClient without real network calls.
final class MockURLProtocol: URLProtocol {

    /// The shared mock instance that the protocol handler uses.
    static var shared: MockURLProtocol?

    // Configuration for the next response.
    var responseData: Data = Data()
    var statusCode: Int = 200
    var error: Error? = nil

    // Captured request for verification.
    var lastRequest: URLRequest?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let mock = MockURLProtocol.shared else {
            client?.urlProtocol(self, didFailWithError: URLError(.unknown))
            return
        }

        mock.lastRequest = request

        if let error = mock.error {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }

        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: mock.statusCode,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!

        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: mock.responseData)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
