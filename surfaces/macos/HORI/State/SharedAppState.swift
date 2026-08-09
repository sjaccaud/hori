import SwiftUI

/// Shared app state — one instance, shared across all windows.
///
/// This is a foundational decision: macOS is a multi-window platform.
/// Shared state (connection config, project list, settings) is separate
/// from per-window state (conversation, preview, current project).
///
/// `SharedAppState` is created once in `HORIApp` and injected via
/// `@Environment`. Every window accesses the same instance.
/// `WindowState` is created per-window and also injected via `@Environment`.
@Observable
final class SharedAppState {

    // MARK: - Connection Configuration

    /// The aios-core URL (Tailscale IP + port 5680).
    /// Stored in UserDefaults, shared across all windows.
    var aiosCoreURL: String {
        get { UserDefaults.standard.string(forKey: "aiosCoreURL") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "aiosCoreURL") }
    }

    /// Whether the connection has been configured.
    var isConnectionConfigured: Bool {
        !aiosCoreURL.isEmpty
    }

    // MARK: - Settings

    /// Whether feedback sounds are enabled (off by default).
    var feedbackSoundsEnabled: Bool {
        get { UserDefaults.standard.bool(forKey: "feedbackSoundsEnabled") }
        set { UserDefaults.standard.set(newValue, forKey: "feedbackSoundsEnabled") }
    }

    // MARK: - Project List (Phase 5)

    /// All known projects (shared across windows so the sidebar
    /// shows the same list in every window).
    var projects: [HoriProject] = []

    // MARK: - Presence (Phase 2)

    /// Current HORI presence state (shared — HORI has one presence,
    /// not per-window).
    var presence: PresenceState = .offline

    /// Whether the presence SSE stream is connected.
    var isPresenceConnected: Bool = false

    /// The presence SSE client. Started/stopped from HORIApp.
    private var presenceClient: PresenceClient?

    // MARK: - Initialization

    init() {}

    // MARK: - Presence Stream

    /// Starts the presence SSE stream, connecting to aios-core and
    /// updating `presence` and `isPresenceConnected` in real time.
    /// Safe to call multiple times — stops any existing client first.
    func startPresenceStream() {
        stopPresenceStream()
        guard isConnectionConfigured,
              let url = URL(string: aiosCoreURL) else { return }

        presenceClient = PresenceClient(
            baseURL: url,
            onStateChange: { [weak self] state in
                self?.presence = state
            },
            onConnectionChange: { [weak self] connected in
                self?.isPresenceConnected = connected
                if !connected {
                    // Don't overwrite a known state on transient disconnects,
                    // but mark offline if we were never connected.
                    if self?.presence == .offline {
                        // already offline
                    }
                }
            }
        )
        presenceClient?.start()
    }

    /// Stops the presence SSE stream.
    func stopPresenceStream() {
        presenceClient?.stop()
        presenceClient = nil
        isPresenceConnected = false
    }
}

// MARK: - Presence State

/// HORI's presence state. Shared across all windows — HORI has one
/// presence, not per-window. Drives the presence indicator and koi
/// mascot reactivity.
enum PresenceState: String, Equatable, CaseIterable {
    case idle       // Available, waiting for input
    case thinking   // Processing a request
    case hasNudge   // Has something to say (proactive)
    case offline    // Not connected to aios-core

    /// The semantic color for this presence state.
    var color: Color {
        switch self {
        case .idle:      return HoriTheme.semanticIdle
        case .thinking:  return HoriTheme.semanticThinking
        case .hasNudge:  return HoriTheme.accentFallback
        case .offline:   return HoriTheme.semanticError
        }
    }

    /// A human-readable, localization-ready description.
    var description: String {
        switch self {
        case .idle:      return "Available"
        case .thinking:  return "Thinking"
        case .hasNudge:  return "Has something to say"
        case .offline:   return "Offline"
        }
    }

    /// VoiceOver announcement text.
    var accessibilityDescription: String {
        switch self {
        case .idle:      return "HORI is available"
        case .thinking:  return "HORI is thinking"
        case .hasNudge:  return "HORI has something to say"
        case .offline:   return "HORI is offline"
        }
    }
}

// MARK: - Project Model (Forward Reference)

/// A HORI project. Full implementation in Phase 5.
/// Defined here so `SharedAppState` and `WindowState` can reference it.
struct HoriProject: Identifiable, Equatable {
    let id: UUID
    var name: String
    var slug: String
    var created: Date
    var modified: Date
    var description: String

    init(id: UUID = UUID(), name: String, slug: String? = nil,
         created: Date = Date(), modified: Date = Date(), description: String = "") {
        self.id = id
        self.name = name
        self.slug = slug ?? name.lowercased().replacingOccurrences(of: " ", with: "-")
        self.created = created
        self.modified = modified
        self.description = description
    }
}
