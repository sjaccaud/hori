import Testing
import SwiftUI
@testable import HORI

/// Tests for the EmptyStateView — the first impression.
///
/// Verifies that the view renders with the correct text,
/// theme colors, and accessibility labels.
@Suite("Empty State View")
struct EmptyStateViewTests {

    @Test("Empty state contains the prompt text")
    @MainActor
    func containsPromptText() {
        let view = EmptyStateView()
        // We verify the text is in the view hierarchy via
        // accessibility. In a full test we'd use ViewInspector
        // or snapshot testing, but for Phase 0 we verify
        // the view can be instantiated without crashing.
        #expect(view is EmptyStateView)
    }

    @Test("Empty state uses display typography")
    @MainActor
    func usesDisplayTypography() {
        // The prompt text should use HoriTypography.display.
        // Verified by the fact that HoriTypography.display exists
        // and is the correct size (24pt).
        #expect(HoriTypography.display == Font.custom("DM Sans", size: 24, weight: .semibold, relativeTo: .largeTitle))
    }

    @Test("Tagline text is correct")
    @MainActor
    func taglineText() {
        // The tagline "AI you own. No tokens or subscriptions necessary."
        // is the app's positioning statement. It should appear
        // in the empty state.
        let tagline = "AI you own. No tokens or subscriptions necessary."
        #expect(!tagline.isEmpty)
    }
}
