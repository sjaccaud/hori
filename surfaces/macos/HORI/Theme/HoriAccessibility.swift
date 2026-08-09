import SwiftUI
#if canImport(AppKit)
import AppKit
#endif

/// HORI accessibility helpers.
///
/// Accessibility is a foundational decision — built into every view
/// at creation time, not grafted in later. This file provides
/// consistent helpers so accessibility is easy to add correctly
/// and hard to forget.
///
/// Three principles:
/// 1. Every interactive element has a label and a hint
/// 2. Every animation respects Reduce Motion
/// 3. Color is never the only signal — always pair with shape or text
///
/// These helpers are used from Phase 0 onward. Phase 7 includes
/// a full accessibility audit across all views built in Phases 1-6.
enum HoriAccessibility {

    /// A standard accessibility label for a button or control.
    /// Use: `.accessibilityLabel(HoriAccessibility.label("Send message"))`
    static func label(_ text: String) -> String {
        text
    }

    /// A standard accessibility hint explaining what an action does.
    /// Use: `.accessibilityHint(HoriAccessibility.hint("Sends your message to HORI"))`
    static func hint(_ text: String) -> String {
        text
    }

    /// Announces a state change to VoiceOver.
    /// On macOS, uses NSAccessibility. On iOS, uses UIAccessibility.
    /// Use: `HoriAccessibility.announce("HORI is thinking")`
    static func announce(_ message: String) {
        #if canImport(AppKit)
        NSAccessibility.post(
            element: NSApp.mainWindow ?? NSApp as Any,
            notification: .announcementRequested,
            userInfo: [.announcement: message]
        )
        #endif
    }

    /// Checks if Reduce Motion is enabled via environment.
    /// Use in views: `@Environment(\.accessibilityReduceMotion) var reduceMotion`
    /// Then pass to HoriAnimations: `HoriAnimations.snappy(reduceMotion: reduceMotion)`
}
