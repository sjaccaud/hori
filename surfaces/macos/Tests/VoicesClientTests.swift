import Testing
import Foundation
@testable import HORI

/// Tests for the VoicesClient — fetches available TTS voices from
/// GET /v1/audio/voices.
///
/// Uses the same MockURLProtocol pattern as HoriClientTests.
@Suite("VoicesClient", .serialized)
struct VoicesClientTests {

    @Test("Decodes a voice list response")
    func decodesVoiceList() async throws {
        VoicesMockURLProtocol.reset()
        VoicesMockURLProtocol.responseData = """
        {"voices": [{"name": "af_heart", "backend": "kokoro"}, {"name": "af_bella", "backend": "kokoro"}]}
        """.data(using: .utf8)!
        VoicesMockURLProtocol.statusCode = 200

        let client = VoicesClient(
            baseURL: URL(string: "https://example.com:5680")!,
            session: makeSession()
        )
        let voices = try await client.fetchVoices()
        #expect(voices.count == 2)
        #expect(voices[0].name == "af_bella")
        #expect(voices[0].backend == "kokoro")
        #expect(voices[1].name == "af_heart")
    }

    @Test("Handles empty voice list")
    func handlesEmptyList() async throws {
        VoicesMockURLProtocol.reset()
        VoicesMockURLProtocol.responseData = """
        {"voices": []}
        """.data(using: .utf8)!
        VoicesMockURLProtocol.statusCode = 200

        let client = VoicesClient(
            baseURL: URL(string: "https://example.com:5680")!,
            session: makeSession()
        )
        let voices = try await client.fetchVoices()
        #expect(voices.isEmpty)
    }

    @Test("Server error throws serverError")
    func serverError() async throws {
        VoicesMockURLProtocol.reset()
        VoicesMockURLProtocol.responseData = "Internal Server Error".data(using: .utf8)!
        VoicesMockURLProtocol.statusCode = 500

        let client = VoicesClient(
            baseURL: URL(string: "https://example.com:5680")!,
            session: makeSession()
        )
        await #expect(throws: HoriClientError.self) {
            _ = try await client.fetchVoices()
        }
    }

    @Test("Malformed JSON throws decodingFailed")
    func malformedJSON() async throws {
        VoicesMockURLProtocol.reset()
        VoicesMockURLProtocol.responseData = "not json".data(using: .utf8)!
        VoicesMockURLProtocol.statusCode = 200

        let client = VoicesClient(
            baseURL: URL(string: "https://example.com:5680")!,
            session: makeSession()
        )
        await #expect(throws: HoriClientError.self) {
            _ = try await client.fetchVoices()
        }
    }

    @Test("Network failure throws networkFailed")
    func networkFailure() async throws {
        VoicesMockURLProtocol.reset()
        VoicesMockURLProtocol.error = URLError(.notConnectedToInternet)

        let client = VoicesClient(
            baseURL: URL(string: "https://example.com:5680")!,
            session: makeSession()
        )
        await #expect(throws: HoriClientError.self) {
            _ = try await client.fetchVoices()
        }
    }

    @Test("Builds correct request URL")
    func buildsCorrectURL() {
        let client = VoicesClient(
            baseURL: URL(string: "https://example.com:5680")!,
            session: .shared
        )
        #expect(client.voicesURL.absoluteString == "https://example.com:5680/v1/audio/voices")
    }

    private func makeSession() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [VoicesMockURLProtocol.self]
        return URLSession(configuration: config)
    }
}

// MARK: - Mock URLProtocol (separate from HoriClientTests to avoid static state leaking)

final class VoicesMockURLProtocol: URLProtocol {

    static var responseData: Data = Data()
    static var statusCode: Int = 200
    static var error: Error? = nil
    static var lastRequest: URLRequest?

    static func reset() {
        responseData = Data()
        statusCode = 200
        error = nil
        lastRequest = nil
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        VoicesMockURLProtocol.lastRequest = request
        if let error = VoicesMockURLProtocol.error {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }
        let response = HTTPURLResponse(
            url: request.url!, statusCode: VoicesMockURLProtocol.statusCode,
            httpVersion: "HTTP/1.1", headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: VoicesMockURLProtocol.responseData)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
