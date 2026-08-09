import SwiftUI

/// First-run connection setup sheet.
///
/// Shown when the app launches and `SharedAppState.isConnectionConfigured`
/// is false. Lets the user enter the aios-core URL (their Tailscale
/// address + port 5680). The URL is saved to UserDefaults via
/// `SharedAppState.aiosCoreURL`.
///
/// Also accessible later via a "Connection Settings" menu item or
/// the settings button (Phase 2+).
struct ConnectionSetupView: View {

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(SharedAppState.self) private var sharedState

    /// Whether this sheet is presented.
    @Binding var isPresented: Bool

    /// The URL text being typed (local state until saved).
    @State private var urlText: String = ""

    /// Whether a test connection is in progress.
    @State private var isTesting = false

    /// The result of the connection test (nil = not tested yet).
    @State private var testResult: TestResult?

    enum TestResult: Equatable {
        case success
        case failure(String)
    }

    var body: some View {
        VStack(spacing: 24) {
            // Header
            VStack(spacing: 8) {
                Image(systemName: "link.badge.plus")
                    .font(.system(size: 36, weight: .light))
                    .foregroundStyle(HoriTheme.accentFallback)

                Text("Connect to HORI")
                    .font(HoriTypography.header)
                    .foregroundStyle(HoriTheme.text(for: colorScheme))

                Text("Enter the address of your HORI server.")
                    .font(HoriTypography.body)
                    .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
            }

            // URL input
            VStack(alignment: .leading, spacing: 6) {
                Text("Server URL")
                    .font(HoriTypography.label)
                    .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))

                TextField("https://your-tailnet.ts.net:5680", text: $urlText)
                    .font(HoriTypography.body)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .background(HoriTheme.surface(for: colorScheme))
                    .clipShape(RoundedRectangle(cornerRadius: HoriShapes.small))
                    .overlay(
                        RoundedRectangle(cornerRadius: HoriShapes.small)
                            .stroke(HoriTheme.border(for: colorScheme), lineWidth: 1)
                    )
                    .accessibilityLabel("Server URL")
                    .accessibilityHint("The HTTPS address of your HORI server, including port.")

                Text("This is your Tailscale address with port 5680.")
                    .font(HoriTypography.caption)
                    .foregroundStyle(HoriTheme.textSecondary(for: colorScheme).opacity(0.6))
            }

            // Test result feedback
            if let result = testResult {
                HStack(spacing: 8) {
                    Image(systemName: result == .success ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(result == .success ? HoriTheme.semanticIdle : HoriTheme.semanticError)
                    Text(result == .success ? "Connected successfully." : result.failureMessage)
                        .font(HoriTypography.caption)
                        .foregroundStyle(HoriTheme.textSecondary(for: colorScheme))
                }
                .transition(reduceMotion ? .opacity : .move(edge: .top).combined(with: .opacity))
            }

            Spacer()

            // Buttons
            HStack(spacing: 12) {
                Button("Test Connection") {
                    testConnection()
                }
                .disabled(urlText.trimmingCharacters(in: .whitespaces).isEmpty || isTesting)
                .buttonStyle(.bordered)

                Button("Connect") {
                    save()
                }
                .keyboardShortcut(.return)
                .buttonStyle(.borderedProminent)
                .disabled(urlText.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(32)
        .frame(width: 440, height: 380)
        .background(HoriTheme.background(for: colorScheme))
        .onAppear {
            urlText = sharedState.aiosCoreURL
        }
    }

    private func save() {
        let trimmed = urlText.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        sharedState.aiosCoreURL = trimmed
        isPresented = false
    }

    private func testConnection() {
        let trimmed = urlText.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, let url = URL(string: trimmed) else {
            withAnimation(HoriAnimations.snappy(reduceMotion: reduceMotion)) {
                testResult = .failure("Invalid URL.")
            }
            return
        }

        isTesting = true
        testResult = nil

        Task {
            let client = HoriClient(baseURL: url)
            do {
                _ = try await client.sendMessage("ping", history: [])
                await MainActor.run {
                    isTesting = false
                    withAnimation(HoriAnimations.snappy(reduceMotion: reduceMotion)) {
                        testResult = .success
                    }
                }
            } catch {
                await MainActor.run {
                    isTesting = false
                    withAnimation(HoriAnimations.snappy(reduceMotion: reduceMotion)) {
                        testResult = .failure(error.localizedDescription)
                    }
                }
            }
        }
    }
}

private extension ConnectionSetupView.TestResult {
    var failureMessage: String {
        switch self {
        case .success: return ""
        case .failure(let msg): return msg
        }
    }
}
