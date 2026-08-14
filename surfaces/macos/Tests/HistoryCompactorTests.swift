import Testing
@testable import HORI

/// Tests for HistoryCompactor (Project Context slice).
///
/// Verifies that HTML blocks are stripped from conversation history
/// and replaced with a compact reference, preventing context window
/// bloat during iterative building.
@Suite("HistoryCompactor")
struct HistoryCompactorTests {

    @Test("Strips HTML blocks from HORI messages")
    func stripsHTMLBlocks() {
        let message = WindowState.Message(
            role: .hori,
            content: "Here's your page:\n```html\n<!DOCTYPE html><html><body>Hello</body></html>\n```\nLet me know if you want changes."
        )
        let compacted = HistoryCompactor.compact(message)
        #expect(!compacted.content.contains("<!DOCTYPE html>"))
        #expect(!compacted.content.contains("<html>"))
        #expect(compacted.content.contains(HistoryCompactor.compactReference))
        #expect(compacted.content.contains("Here's your page:"))
        #expect(compacted.content.contains("Let me know if you want changes."))
    }

    @Test("Preserves user messages unchanged")
    func preservesUserMessages() {
        let message = WindowState.Message(role: .user, content: "Make me a color picker")
        let compacted = HistoryCompactor.compact(message)
        #expect(compacted.content == "Make me a color picker")
    }

    @Test("Preserves non-HTML code blocks")
    func preservesNonHTMLCode() {
        let message = WindowState.Message(
            role: .hori,
            content: "Here's some Python:\n```python\nprint('hello')\n```\nAnd here's HTML:\n```html\n<p>hi</p>\n```"
        )
        let compacted = HistoryCompactor.compact(message)
        #expect(compacted.content.contains("```python"))
        #expect(compacted.content.contains("print('hello')"))
        #expect(!compacted.content.contains("<p>hi</p>"))
        #expect(compacted.content.contains(HistoryCompactor.compactReference))
    }

    @Test("Handles messages with no HTML")
    func handlesNoHTML() {
        let message = WindowState.Message(role: .hori, content: "I can help with that!")
        let compacted = HistoryCompactor.compact(message)
        #expect(compacted.content == "I can help with that!")
    }

    @Test("Handles empty messages")
    func handlesEmpty() {
        let message = WindowState.Message(role: .hori, content: "")
        let compacted = HistoryCompactor.compact(message)
        #expect(compacted.content == "")
    }

    @Test("Compacts full history array")
    func compactsHistoryArray() {
        let history = [
            WindowState.Message(role: .user, content: "Make a page"),
            WindowState.Message(role: .hori, content: "Sure!\n```html\n<html></html>\n```"),
            WindowState.Message(role: .user, content: "Add a button"),
            WindowState.Message(role: .hori, content: "Done!\n```html\n<html><button>Click</button></html>\n```"),
        ]
        let compacted = HistoryCompactor.compactHistory(history)
        #expect(compacted.count == 4)
        #expect(compacted[0].content == "Make a page")
        #expect(compacted[1].content.contains(HistoryCompactor.compactReference))
        #expect(!compacted[1].content.contains("<html>"))
        #expect(compacted[2].content == "Add a button")
        #expect(compacted[3].content.contains(HistoryCompactor.compactReference))
        #expect(!compacted[3].content.contains("<button>"))
    }

    @Test("Extracts current canvas HTML from history")
    func extractsCanvasHTML() {
        let history = [
            WindowState.Message(role: .user, content: "Make a page"),
            WindowState.Message(role: .hori, content: "Sure!\n```html\n<html><p>first</p></html>\n```"),
            WindowState.Message(role: .user, content: "Add a button"),
            WindowState.Message(role: .hori, content: "Done!\n```html\n<html><button>Click</button></html>\n```"),
        ]
        let canvas = HistoryCompactor.currentCanvasHTML(from: history)
        #expect(canvas != nil)
        #expect(canvas?.contains("<button>") == true)
        #expect(canvas?.contains("Click") == true)
        // Should be the most recent, not the first
        #expect(canvas?.contains("first") == false)
    }

    @Test("Returns nil when no HTML in history")
    func noCanvasHTML() {
        let history = [
            WindowState.Message(role: .user, content: "Hello"),
            WindowState.Message(role: .hori, content: "Hi there!"),
        ]
        let canvas = HistoryCompactor.currentCanvasHTML(from: history)
        #expect(canvas == nil)
    }

    @Test("Handles multiple HTML blocks in one message")
    func multipleHTMLBlocks() {
        let message = WindowState.Message(
            role: .hori,
            content: "First version:\n```html\n<p>v1</p>\n```\nUpdated:\n```html\n<p>v2</p>\n```"
        )
        let compacted = HistoryCompactor.compact(message)
        // Both blocks should be replaced
        #expect(!compacted.content.contains("<p>v1</p>"))
        #expect(!compacted.content.contains("<p>v2</p>"))
        // Should have two compact references
        let count = compacted.content.components(separatedBy: HistoryCompactor.compactReference).count - 1
        #expect(count == 2)
    }
}
