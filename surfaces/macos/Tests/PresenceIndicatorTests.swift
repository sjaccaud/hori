import Testing
import SwiftUI
@testable import HORI

/// Tests for PresenceIndicator and PresenceDot.
///
/// Verifies color mapping, accessibility, and that the indicator
/// renders without crashing for all presence states.
@Suite("PresenceIndicator")
struct PresenceIndicatorTests {

    // MARK: - Rendering

    @Test("PresenceIndicator renders for all states without crash")
    func rendersAllStates() {
        for state in PresenceState.allCases {
            let indicator = PresenceIndicator(
                presence: state,
                isConnected: true
            )
            _ = indicator.body
        }
    }

    @Test("PresenceIndicator renders when disconnected")
    func rendersDisconnected() {
        let indicator = PresenceIndicator(
            presence: .idle,
            isConnected: false
        )
        _ = indicator.body
    }

    @Test("PresenceDot renders for all states without crash")
    func dotRendersAllStates() {
        for state in PresenceState.allCases {
            let dot = PresenceDot(presence: state, reduceMotion: false)
            _ = dot.body
            let dotReduced = PresenceDot(presence: state, reduceMotion: true)
            _ = dotReduced.body
        }
    }

    // MARK: - Presence State

    @Test("PresenceState has exactly four cases")
    func hasFourStates() {
        #expect(PresenceState.allCases.count == 4)
        #expect(PresenceState.allCases.contains(.idle))
        #expect(PresenceState.allCases.contains(.thinking))
        #expect(PresenceState.allCases.contains(.hasNudge))
        #expect(PresenceState.allCases.contains(.offline))
    }

    @Test("PresenceState maps from server strings")
    func mapsFromServerStrings() {
        #expect(PresenceState(rawValue: "idle") == .idle)
        #expect(PresenceState(rawValue: "thinking") == .thinking)
        #expect(PresenceState(rawValue: "has_nudge") == .hasNudge)
        #expect(PresenceState(rawValue: "offline") == .offline)
        #expect(PresenceState(rawValue: "unknown") == nil)
    }

    @Test("Each state has a distinct color")
    func distinctColors() {
        let colors = PresenceState.allCases.map { $0.color }
        // All colors should be distinct (no two states share a color)
        for i in 0..<colors.count {
            for j in (i+1)..<colors.count {
                #expect(colors[i] != colors[j],
                        "\(PresenceState.allCases[i]) and \(PresenceState.allCases[j]) share the same color")
            }
        }
    }

    @Test("Each state has a description")
    func hasDescription() {
        for state in PresenceState.allCases {
            #expect(!state.description.isEmpty,
                    "\(state) has no description")
        }
    }

    @Test("Each state has an accessibility description")
    func hasAccessibilityDescription() {
        for state in PresenceState.allCases {
            #expect(!state.accessibilityDescription.isEmpty,
                    "\(state) has no accessibility description")
        }
    }
}
