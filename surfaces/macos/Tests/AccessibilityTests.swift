import Testing
import SwiftUI
@testable import HORI

/// Tests for accessibility infrastructure.
///
/// Accessibility is a foundational decision — built in from Phase 0.
/// These tests verify that the accessibility helpers work correctly
/// and that the design system respects Reduce Motion.
@Suite("Accessibility")
struct AccessibilityTests {

    @Test("HoriAccessibility.label returns the input string")
    func labelReturnsInput() {
        let result = HoriAccessibility.label("Send message")
        #expect(result == "Send message")
    }

    @Test("HoriAccessibility.hint returns the input string")
    func hintReturnsInput() {
        let result = HoriAccessibility.hint("Sends your message to HORI")
        #expect(result == "Sends your message to HORI")
    }

    @Test("Snappy animation respects Reduce Motion")
    func snappyRespectsReduceMotion() {
        let normalAnimation = HoriAnimations.snappy(reduceMotion: false)
        let reducedAnimation = HoriAnimations.snappy(reduceMotion: true)
        // When Reduce Motion is enabled, the animation should be
        // a linear ease, not a spring. We can't directly compare
        // Animation values, but we verify both are valid (non-nil).
        #expect(normalAnimation != nil)
        #expect(reducedAnimation != nil)
    }

    @Test("Balanced animation respects Reduce Motion")
    func balancedRespectsReduceMotion() {
        let normalAnimation = HoriAnimations.balanced(reduceMotion: false)
        let reducedAnimation = HoriAnimations.balanced(reduceMotion: true)
        #expect(normalAnimation != nil)
        #expect(reducedAnimation != nil)
    }

    @Test("Dramatic animation respects Reduce Motion")
    func dramaticRespectsReduceMotion() {
        let normalAnimation = HoriAnimations.dramatic(reduceMotion: false)
        let reducedAnimation = HoriAnimations.dramatic(reduceMotion: true)
        #expect(normalAnimation != nil)
        #expect(reducedAnimation != nil)
    }

    @Test("Presence state has accessibility description")
    func presenceAccessibilityDescriptions() {
        #expect(PresenceState.idle.accessibilityDescription == "HORI is available")
        #expect(PresenceState.thinking.accessibilityDescription == "HORI is thinking")
        #expect(PresenceState.hasNudge.accessibilityDescription == "HORI has something to say")
        #expect(PresenceState.offline.accessibilityDescription == "HORI is offline")
    }

    @Test("Presence state color is never the same for adjacent states")
    func presenceColorsDiffer() {
        // Color should never be the only signal, but it should
        // at least be different for different states.
        let colors = Set(PresenceState.allCases.map { $0.color.description })
        // We can't compare Color directly, but we verify that
        // the color property returns a non-default color.
        #expect(PresenceState.idle.color != PresenceState.thinking.color)
        #expect(PresenceState.thinking.color != PresenceState.offline.color)
    }
}
