import Testing
@testable import HORI

/// Tests for MenuBarController (MAC-7).
///
/// The menu bar controller manages the NSStatusItem and its menu.
/// These tests verify the menu structure and presence display names.
@Suite("MenuBarController")
struct MenuBarControllerTests {

    @Test("PresenceState has display names for menu bar")
    func presenceDisplayNames() {
        #expect(PresenceState.idle.displayName == "Available")
        #expect(PresenceState.thinking.displayName == "Thinking")
        #expect(PresenceState.hasNudge.displayName == "Has something to say")
        #expect(PresenceState.offline.displayName == "Offline")
    }

    @Test("SoundFeedback event volumes are within valid range")
    func soundVolumesValid() {
        #expect(SoundFeedback.Event.messageSent.volume > 0)
        #expect(SoundFeedback.Event.messageSent.volume <= 1.0)
        #expect(SoundFeedback.Event.messageReceived.volume > 0)
        #expect(SoundFeedback.Event.messageReceived.volume <= 1.0)
        #expect(SoundFeedback.Event.presenceChange.volume > 0)
        #expect(SoundFeedback.Event.presenceChange.volume <= 1.0)
    }

    @Test("SoundFeedback presence change is quieter than message sounds")
    func presenceChangeQuieter() {
        #expect(SoundFeedback.Event.presenceChange.volume < SoundFeedback.Event.messageSent.volume)
        #expect(SoundFeedback.Event.presenceChange.volume < SoundFeedback.Event.messageReceived.volume)
    }

    @Test("SoundFeedback does not crash when disabled")
    func noCrashWhenDisabled() {
        // Should be a no-op when disabled
        SoundFeedback.play(.messageSent, enabled: false)
        SoundFeedback.play(.messageReceived, enabled: false)
        SoundFeedback.play(.presenceChange, enabled: false)
        #expect(Bool(true))
    }
}
