import Foundation

/// Extracts ```html code blocks from message text.
///
/// HORI's replies may contain fenced HTML code blocks. This extractor
/// pulls the HTML content (without the fences) for live preview rendering.
///
/// Handles streaming text (unclosed fences) so the preview can render
/// HTML as it arrives, before the closing ``` has been sent.
///
/// Traces to: docs/roadmap.md MAC-4 (Live Preview).
enum HTMLExtractor {

    /// Regex pattern for fenced html code blocks.
    /// Matches ```html ... ``` (case-insensitive), capturing the content.
    /// Also matches unclosed fences (for streaming).
    /// Uses (?s) for dot-matches-newline so multi-line HTML is captured.
    private static let pattern = #"(?s)```[hH][tT][mM][lL]\s*\n(.*?)(?:```|$)"#

    private static let regex: NSRegularExpression = {
        // swiftlint:disable:next force_try
        try! NSRegularExpression(pattern: pattern, options: [])
    }()

    /// Extracts all HTML code blocks from the given text.
    /// - Parameter text: The message text potentially containing ```html blocks.
    /// - Returns: Array of HTML content strings (without fences), in order of appearance.
    static func extractHTMLBlocks(from text: String) -> [String] {
        guard !text.isEmpty else { return [] }

        let range = NSRange(text.startIndex..., in: text)
        let matches = regex.matches(in: text, range: range)

        return matches.compactMap { match in
            // Group 1 is the captured HTML content
            let contentRange = match.range(at: 1)
            guard contentRange.location != NSNotFound,
                  let range = Range(contentRange, in: text) else {
                return nil
            }
            return String(text[range]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
    }

    /// Extracts the last HTML code block from the given text.
    /// Useful for live preview — the last block is the most recent version.
    /// - Parameter text: The message text potentially containing ```html blocks.
    /// - Returns: The last HTML content string, or nil if none found.
    static func extractLastHTMLBlock(from text: String) -> String? {
        let blocks = extractHTMLBlocks(from: text)
        return blocks.last
    }

    /// Checks whether the text contains any HTML code blocks.
    /// - Parameter text: The message text to check.
    /// - Returns: true if at least one ```html block is found (including unclosed/streaming).
    static func containsHTMLBlock(in text: String) -> Bool {
        !extractHTMLBlocks(from: text).isEmpty
    }
}
