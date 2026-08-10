import SwiftUI
import WebKit

/// A SwiftUI view that renders HTML in a WKWebView.
///
/// Used for live preview of HTML that HORI generates in conversation.
/// The HTML is loaded directly (no external resources) for security.
/// Updates live as the HTML content changes (streaming).
///
/// Traces to: docs/roadmap.md MAC-4 (Live Preview).
struct HTMLPreviewView: NSViewRepresentable {

    /// The HTML content to render.
    let html: String

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.preferences.javaScriptEnabled = true

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        // Transparent background so it blends with the app theme
        webView.underPageBackgroundColor = .clear
        // Allow inspection but prevent navigation away
        webView.allowsBackForwardNavigationGestures = false

        loadHTML(webView)
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        // Only reload if the HTML actually changed
        if context.coordinator.lastHTML != html {
            context.coordinator.lastHTML = html
            loadHTML(webView)
        }
    }

    private func loadHTML(_ webView: WKWebView) {
        webView.loadHTMLString(html, baseURL: nil)
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject, WKNavigationDelegate {

        /// Last loaded HTML — prevents unnecessary reloads.
        var lastHTML: String = ""

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            // Allow the initial load (loadHTMLString). Block everything else
            // (no external navigation, no links clicking away).
            if navigationAction.navigationType == .other {
                decisionHandler(.allow)
            } else {
                decisionHandler(.cancel)
            }
        }
    }
}
