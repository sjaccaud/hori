import Foundation

/// HTTP client for fetching available TTS voices from aios-core.
///
/// GET /v1/audio/voices returns a list of available voice models
/// (name + backend). Used by the voice settings UI to let the user
/// pick a TTS voice.
///
/// Traces to: docs/roadmap.md MAC-3 (Voice Conversation), Phase 3.
struct VoicesClient {

    let baseURL: URL
    var session: URLSession = .shared

    /// A TTS voice returned by /v1/audio/voices.
    struct Voice: Identifiable, Equatable {
        let name: String
        let backend: String

        var id: String { name }
    }

    /// The full URL of the voices endpoint.
    var voicesURL: URL {
        baseURL.appendingPathComponent("v1/audio/voices")
    }

    /// Fetches the list of available TTS voices.
    /// - Returns: Array of voices, sorted by name.
    /// - Throws: `HoriClientError` for network, decoding, or server errors.
    func fetchVoices() async throws -> [Voice] {
        var request = URLRequest(url: voicesURL)
        request.httpMethod = "GET"
        request.timeoutInterval = 10

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
            let decoded = try JSONDecoder().decode(VoicesResponse.self, from: data)
            return decoded.voices.map { Voice(name: $0.name, backend: $0.backend) }
                .sorted { $0.name < $1.name }
        } catch {
            throw HoriClientError.decodingFailed(error)
        }
    }

    private struct VoicesResponse: Decodable {
        let voices: [VoiceDTO]
    }

    private struct VoiceDTO: Decodable {
        let name: String
        let backend: String
    }
}
