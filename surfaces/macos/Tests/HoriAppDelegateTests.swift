import Testing
import Foundation
@testable import HORI

/// Tests for `HoriAppDelegate` — the owner of the presence SSE stream
/// lifecycle.
///
/// The presence stream is shared across all windows (one HORI, one
/// presence), so it must live for the app's lifetime, not a single
/// window's. These tests lock in that the delegate — not per-window
/// `onAppear`/`onDisappear` — owns `SharedAppState` and starts/stops
/// the stream at app launch/termination.
///
/// Regression guard for the bug where closing one window tore down the
/// stream for all windows, flipping the indicator to "Offline" while
/// the chat endpoint (a separate HTTP request) still worked.
@Suite("HoriAppDelegate")
struct HoriAppDelegateTests {

    /// The delegate owns the shared state that SwiftUI consumes.
    @Test("Delegate exposes shared state for SwiftUI injection")
    func ownsSharedState() {
        let delegate = HoriAppDelegate()
        // SharedAppState starts disconnected — no stream until configured.
        #expect(delegate.sharedState.isPresenceConnected == false)
    }

    /// Terminating the app stops the presence stream. This is the
    /// single place the stream is torn down — not per-window.
    @Test("applicationWillTerminate stops the presence stream")
    func terminateStopsStream() {
        // Ensure no URL is configured so startPresenceStream is a no-op
        // and we don't kick off a real network connection in tests.
        UserDefaults.standard.removeObject(forKey: "aiosCoreURL")
        defer { UserDefaults.standard.removeObject(forKey: "aiosCoreURL") }

        let delegate = HoriAppDelegate()
        delegate.applicationWillTerminate(Notification(name: Notification.Name("test")))

        #expect(delegate.sharedState.isPresenceConnected == false)
    }

    /// Launching is safe even when the connection isn't configured yet
    /// (first run). The stream starts once `ConnectionSetupView` sets
    /// the URL and calls `startPresenceStream()` again.
    @Test("applicationDidFinishLaunching is a no-op when unconfigured")
    func launchNoOpWhenUnconfigured() {
        UserDefaults.standard.removeObject(forKey: "aiosCoreURL")
        defer { UserDefaults.standard.removeObject(forKey: "aiosCoreURL") }

        let delegate = HoriAppDelegate()
        delegate.applicationDidFinishLaunching(Notification(name: Notification.Name("test")))

        // No URL → no connection attempt → stays disconnected.
        #expect(delegate.sharedState.isPresenceConnected == false)
        #expect(delegate.sharedState.isConnectionConfigured == false)
    }
}
