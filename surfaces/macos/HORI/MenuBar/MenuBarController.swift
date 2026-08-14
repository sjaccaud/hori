import AppKit
import SwiftUI

/// Manages the HORI menu bar item (NSStatusItem).
///
/// Shows a HORI icon in the system menu bar that reflects the current
/// presence state. Clicking it opens a menu with quick actions:
/// - New Conversation (clears current conversation)
/// - Connection Settings (opens the connection setup sheet)
/// - Toggle Sound Feedback
/// - Quit HORI
///
/// The menu bar item persists for the app's lifetime — it's not tied
/// to any window. This means HORI is always accessible from the menu
/// bar even when no windows are open.
///
/// The menu is rebuilt every time it's about to show so the presence
/// status and sound toggle always reflect the current state.
///
/// Traces to: docs/roadmap.md MAC-7 (The Koi, Menu Bar, Sound).
final class MenuBarController: NSObject {

    /// The shared app state (for presence and settings).
    private let sharedState: SharedAppState

    /// The status item in the menu bar.
    private var statusItem: NSStatusItem?

    /// The menu shown when the status item is clicked.
    private let menu = NSMenu()

    /// Whether the connection settings sheet should be shown.
    var onShowConnectionSettings: (() -> Void)?

    /// Whether to start a new conversation.
    var onNewConversation: (() -> Void)?

    init(sharedState: SharedAppState) {
        self.sharedState = sharedState
        super.init()
    }

    // MARK: - Lifecycle

    func start() {
        guard statusItem == nil else { return }

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "fish.fill", accessibilityDescription: "HORI")
            button.image?.isTemplate = true
        }

        menu.showsStateColumn = true
        rebuildMenu()
        statusItem?.menu = menu
    }

    func stop() {
        if let statusItem {
            NSStatusBar.system.removeStatusItem(statusItem)
        }
        statusItem = nil
    }

    // MARK: - Menu Building

    /// Rebuilds the menu in place, keeping the same NSMenu object
    /// so the status item's reference stays valid.
    private func rebuildMenu() {
        menu.removeAllItems()

        // Presence status (disabled — just shows current state)
        let presence = sharedState.isPresenceConnected ? sharedState.presence : .offline
        let presenceTitle = "HORI — \(presence.displayName)"
        let presenceItem = NSMenuItem(title: presenceTitle, action: nil, keyEquivalent: "")
        presenceItem.isEnabled = false
        menu.addItem(presenceItem)

        menu.addItem(.separator())

        // New Conversation
        let newConvo = NSMenuItem(title: "New Conversation", action: #selector(newConversation), keyEquivalent: "n")
        newConvo.target = self
        newConvo.isEnabled = true
        menu.addItem(newConvo)

        // Connection Settings
        let settings = NSMenuItem(title: "Connection Settings…", action: #selector(showConnectionSettings), keyEquivalent: ",")
        settings.target = self
        settings.isEnabled = true
        menu.addItem(settings)

        menu.addItem(.separator())

        // Sound feedback toggle
        let sound = NSMenuItem(title: "Sound Feedback", action: #selector(toggleSoundFeedback), keyEquivalent: "")
        sound.target = self
        sound.state = sharedState.feedbackSoundsEnabled ? .on : .off
        sound.isEnabled = true
        menu.addItem(sound)

        menu.addItem(.separator())

        // Quit
        let quit = NSMenuItem(title: "Quit HORI", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        quit.isEnabled = true
        menu.addItem(quit)
    }

    // MARK: - Actions

    @objc private func newConversation() {
        rebuildMenu()
        onNewConversation?()
    }

    @objc private func showConnectionSettings() {
        rebuildMenu()
        onShowConnectionSettings?()
    }

    @objc private func toggleSoundFeedback() {
        sharedState.feedbackSoundsEnabled.toggle()
        rebuildMenu()
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }
}

// MARK: - PresenceState Display Name

extension PresenceState {
    var displayName: String {
        switch self {
        case .idle:      return "Available"
        case .thinking:  return "Thinking"
        case .hasNudge:  return "Has something to say"
        case .offline:   return "Offline"
        }
    }
}
