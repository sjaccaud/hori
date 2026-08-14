import Foundation

/// Compacts conversation history by stripping HTML code blocks.
///
/// When HORI generates HTML, the full block (often 2000-4000 tokens)
/// stays in `windowState.messages` and gets sent back as history on
/// every subsequent turn. This eats the 16K context window fast —
/// by turn 2-3, there's little room for new output.
///
/// HistoryCompactor strips ```html blocks from HORI's replies in the
/// history, replacing them with a compact reference. The Mac app's UI
/// still shows the full reply (with HTML) — only the history sent to
/// the backend is compacted.
///
/// The current canvas HTML is passed separately (as a context field,
/// not in conversation history) so HORI can still reference what she
/// built without it bloating every turn.
///
/// Traces to: Project Context slice — iterative building without bloat.
enum HistoryCompactor {

    /// The compact reference that replaces HTML blocks in history.
    /// This tells HORI she generated HTML without including the full
    /// HTML block (which would bloat the context window).
    static let compactReference = "[HTML page generated and shown in canvas]"

    /// Compacts a single message by stripping HTML code blocks.
    /// - Parameter message: The original message (may contain ```html blocks).
    /// - Returns: A new message with HTML blocks replaced by a compact reference.
    ///            Non-HTML content (prose, other code) is preserved.
    static func compact(_ message: WindowState.Message) -> WindowState.Message {
        let compacted = stripHTMLBlocks(from: message.content)
        return WindowState.Message(role: message.role, content: compacted)
    }

    /// Compacts an array of messages for sending as history.
    /// - Parameter messages: The full conversation history.
    /// - Returns: Compacted history with HTML blocks replaced.
    static func compactHistory(_ messages: [WindowState.Message]) -> [WindowState.Message] {
        messages.map { compact($0) }
    }

    /// Strips ```html blocks from text, replacing them with a compact reference.
    /// Preserves all non-HTML content (prose, other code blocks, etc.).
    /// - Parameter text: The text potentially containing HTML blocks.
    /// - Returns: Text with HTML blocks replaced by compact reference.
    static func stripHTMLBlocks(from text: String) -> String {
        // Use the same regex pattern as HTMLExtractor
        let pattern = #"(?s)```[hH][tT][mM][lL]\s*\n.*?```"#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: []) else {
            return text
        }

        let range = NSRange(text.startIndex..., in: text)
        let compacted = regex.stringByReplacingMatches(
            in: text,
            range: range,
            withTemplate: compactReference
        )

        return compacted
    }

    /// Extracts the current canvas HTML for passing as separate context.
    /// This is the most recent HTML block from the most recent HORI message.
    /// The HTML is compacted and capped to ~4000 chars so the 27B model
    /// can hold it in context and faithfully reproduce it with modifications.
    /// Larger pages will be truncated — the model sees the structure but
    /// may lose detail at the end.
    /// - Parameter messages: The full conversation history.
    /// - Returns: The most recent HTML block (compacted, capped), or nil.
    static func currentCanvasHTML(from messages: [WindowState.Message]) -> String? {
        // Find the most recent HORI message with HTML
        for message in messages.reversed() where message.role == .hori {
            if let html = HTMLExtractor.extractLastHTMLBlock(from: message.content) {
                return compactHTML(html)
            }
        }
        return nil
    }

    /// Compacts HTML by removing excess whitespace and capping size.
    /// Preserves all functional content up to the cap — the model sees
    /// the full structure of smaller pages, and the beginning of larger ones.
    static func compactHTML(_ html: String) -> String {
        // Remove leading/trailing whitespace on each line, drop empty lines
        let lines = html.split(separator: "\n", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        let compacted = lines.joined(separator: "\n")

        // Cap at ~4000 chars (~1000 tokens) so the model can hold it
        // and reproduce it faithfully. Larger pages get truncated with
        // a note so HORI knows there's more.
        let cap = 4000
        if compacted.count <= cap {
            return compacted
        }
        return String(compacted.prefix(cap)) + "\n<!-- ... truncated (page is \(compacted.count) chars total) ... -->"
    }
}
