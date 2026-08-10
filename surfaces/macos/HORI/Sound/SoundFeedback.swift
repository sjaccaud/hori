import AppKit

/// Subtle sound feedback for HORI interactions.
///
/// Plays system sounds for key events:
/// - Message sent (subtle pop)
/// - Message received (soft chime)
/// - Presence change (very subtle tick)
///
/// All sounds are opt-in via `SharedAppState.feedbackSoundsEnabled`.
/// When disabled, no sounds are played.
///
/// Uses NSSound with system sounds — no audio files needed. The sounds
/// are subtle and short, designed to be background feedback, not alerts.
///
/// Traces to: docs/roadmap.md MAC-7 (The Koi, Menu Bar, Sound).
enum SoundFeedback {

    /// Plays a sound for the given event, if sound feedback is enabled.
    /// - Parameters:
    ///   - event: The event type (send, receive, presence change).
    ///   - enabled: Whether sound feedback is enabled.
    static func play(_ event: Event, enabled: Bool) {
        guard enabled else { return }
        guard let sound = event.sound else { return }
        sound.volume = event.volume
        sound.play()
    }

    // MARK: - Events

    enum Event {
        case messageSent
        case messageReceived
        case presenceChange

        /// The NSSound to play for this event.
        var sound: NSSound? {
            switch self {
            case .messageSent:
                // Subtle pop
                return NSSound(named: "Pop")
            case .messageReceived:
                // Soft chime
                return NSSound(named: "Glass")
            case .presenceChange:
                // Very subtle tick
                return NSSound(named: "Tink")
            }
        }

        /// Volume for this event (0.0 to 1.0).
        var volume: Float {
            switch self {
            case .messageSent:       return 0.3
            case .messageReceived:    return 0.4
            case .presenceChange:     return 0.15
            }
        }
    }
}
