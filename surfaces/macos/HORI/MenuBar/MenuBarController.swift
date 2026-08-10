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
/// Traces to: docs/roadmap.md MAC-7 (The Koi, Menu Bar, Sound).
final class MenuBarController: NSObject {

    /// The shared app state (for presence and settings).
    private let sharedState: SharedAppState

    /// The status item in the menu bar.
    private var statusItem: NSStatusItem?

    /// Whether the connection settings sheet should be shown.
    /// Bound to ContentView's showConnectionSetup.
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

        // Set up the menu
        let menu = buildMenu()
        statusItem?.menu = menu
        statusItem?.menu?.delegate = self

        updateIcon()

        // Observe presence changes
        withObservationTracking {
            _ = sharedState.presence
            _ = sharedState.isPresenceConnected
        } onChange: { [weak self] in
            DispatchQueue.main.async {
                self?.updateIcon()
            }
        }
    }

    func stop() {
        if let statusItem {
            NSStatusBar.system.removeStatusItem(statusItem)
        }
        statusItem = nil
    }

    // MARK: - Icon

    private func updateIcon() {
        let presence = sharedState.isPresenceConnected ? sharedState.presence : .offline
        let symbolName: String
        switch presence {
        case .idle:      symbolName = "fish.fill"
        case .thinking:  symbolName = "fish.fill"
        case .hasNudge:  symbolName = "fish.fill.circle"
        case .offline:   symbolName = "fish"
        }

        statusItem?.button?.image = NSImage(systemSymbolName: symbolName, accessibilityDescription: "HORI — \(presence.rawValue)")
        statusItem?.button?.image?.isTemplate = true

        // Re-observe for next change
        withObservationTracking {
            _ = sharedState.presence
            _ = sharedState.isPresenceConnected
        } onChange: { [weak self] in
            DispatchQueue.main.async {
                self?.updateIcon()
            }
        }
    }

    // MARK: - Menu

    func buildMenu() -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false

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

        return menu
    }

    // MARK: - Actions

    @objc private func newConversation() {
        onNewConversation?()
    }

    @objc private func showConnectionSettings() {
        onShowConnectionSettings?()
    }

    @objc private func toggleSoundFeedback() {
        sharedState.feedbackSoundsEnabled.toggle()
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }
}

// MARK: - NSMenuDelegate

extension MenuBarController: NSMenuDelegate {
    func menuNeedsUpdate(_ menu: NSMenu) {
        // Rebuild the menu each time it opens to reflect current state
        menu.removeAllItems()
        let newMenu = buildMenu()
        for item in newMenu.items {
            menu.addItem(item)
        }
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
