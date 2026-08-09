import SwiftUI

/// Voice settings — pick TTS voice and speed.
///
/// Fetches available voices from /v1/audio/voices and lets the user
/// select one. Voice and speed are stored in UserDefaults via
/// SharedAppState.
///
/// Traces to: docs/roadmap.md MAC-3 (Voice Conversation), Phase 3.
struct VoiceSettingsView: View {

    @Environment(\.colorScheme) private var colorScheme
    @Environment(SharedAppState.self) private var sharedState

    /// Available voices from the server.
    @State private var voices: [VoicesClient.Voice] = []

    /// Whether voices are loading.
    @State private var isLoading = false

    /// Error message if voice loading failed.
    @State private var errorMessage: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Voice")
                .font(HoriTypography.header)
                .foregroundStyle(HoriTheme.text(for: colorScheme))

            if isLoading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Loading voices...")
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                }
            } else if let error = errorMessage {
                Text(error)
                    .font(HoriTypography.caption)
                    .foregroundStyle(HoriTheme.semanticError)
            } else if voices.isEmpty {
                Text("No voices available. Check your connection to HORI.")
                    .font(HoriTypography.caption)
                    .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
            } else {
                voicePicker
            }

            speedSlider

            Spacer()
        }
        .padding(20)
        .frame(width: 360, height: 320)
        .background(HoriTheme.background(for: colorScheme))
        .onAppear { loadVoices() }
    }

    // MARK: - Voice Picker

    private var voicePicker: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("TTS Voice")
                .font(HoriTypography.label)
                .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))

            Picker("Voice", selection: Binding(
                get: { sharedState.ttsVoice },
                set: { sharedState.ttsVoice = $0 }
            )) {
                ForEach(voices) { voice in
                    Text(voice.name)
                        .tag(voice.name)
                }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity)
            .background(HoriTheme.surface(for: colorScheme))
            .clipShape(RoundedRectangle(cornerRadius: HoriShapes.small))
            .overlay(
                RoundedRectangle(cornerRadius: HoriShapes.small)
                    .stroke(HoriTheme.border(for: colorScheme), lineWidth: 1)
            )
        }
    }

    // MARK: - Speed Slider

    private var speedSlider: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Speed: \(String(format: "%.1f", sharedState.ttsSpeed))x")
                .font(HoriTypography.label)
                .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))

            Slider(value: Binding(
                get: { sharedState.ttsSpeed },
                set: { sharedState.ttsSpeed = $0 }
            ), in: 0.5...2.0, step: 0.1)
        }
    }

    // MARK: - Voice Loading

    private func loadVoices() {
        isLoading = true
        errorMessage = nil

        guard let url = URL(string: sharedState.aiosCoreURL) else {
            isLoading = false
            errorMessage = "No server URL configured."
            return
        }

        Task {
            do {
                let client = VoicesClient(baseURL: url)
                let fetched = try await client.fetchVoices()
                await MainActor.run {
                    voices = fetched
                    isLoading = false
                }
            } catch {
                await MainActor.run {
                    errorMessage = error.localizedDescription
                    isLoading = false
                }
            }
        }
    }
}
