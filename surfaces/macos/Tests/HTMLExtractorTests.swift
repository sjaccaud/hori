import Testing
import Foundation
@testable import HORI

/// Tests for HTMLExtractor — extracts ```html code blocks from
/// message text and detects when a message contains HTML.
///
/// HORI's replies may contain ```html ... ``` blocks. We extract
/// the HTML content (without the fences) for live preview rendering.
@Suite("HTMLExtractor")
struct HTMLExtractorTests {

    // MARK: - Extraction

    @Test("Extracts a single html code block")
    func extractsSingleBlock() {
        let text = """
        Here's a landing page:

        ```html
        <!DOCTYPE html>
        <html><body><h1>Hello</h1></body></html>
        ```

        Let me know what you think!
        """
        let blocks = HTMLExtractor.extractHTMLBlocks(from: text)
        #expect(blocks.count == 1)
        #expect(blocks[0].contains("<!DOCTYPE html>"))
        #expect(blocks[0].contains("<h1>Hello</h1>"))
        #expect(!blocks[0].contains("```"))
    }

    @Test("Extracts multiple html code blocks")
    func extractsMultipleBlocks() {
        let text = """
        First page:

        ```html
        <h1>Page 1</h1>
        ```

        Second page:

        ```html
        <h1>Page 2</h1>
        ```
        """
        let blocks = HTMLExtractor.extractHTMLBlocks(from: text)
        #expect(blocks.count == 2)
        #expect(blocks[0].contains("Page 1"))
        #expect(blocks[1].contains("Page 2"))
    }

    @Test("Returns empty array when no html blocks")
    func noHTMLBlocks() {
        let text = "Just a regular message with no code blocks."
        let blocks = HTMLExtractor.extractHTMLBlocks(from: text)
        #expect(blocks.isEmpty)
    }

    @Test("Ignores non-html code blocks")
    func ignoresNonHTMLBlocks() {
        let text = """
        Here's some Python:

        ```python
        print("hello")
        ```

        And some JS:

        ```javascript
        console.log("hi");
        ```
        """
        let blocks = HTMLExtractor.extractHTMLBlocks(from: text)
        #expect(blocks.isEmpty)
    }

    @Test("Handles html block with no closing fence (streaming)")
    func handlesUnclosedBlock() {
        // During streaming, the closing ``` may not have arrived yet.
        // We should still extract what's there.
        let text = """
        Here's a page:

        ```html
        <h1>Streaming</h1>
        """
        let blocks = HTMLExtractor.extractHTMLBlocks(from: text)
        #expect(blocks.count == 1)
        #expect(blocks[0].contains("<h1>Streaming</h1>"))
    }

    @Test("Extracts the last html block (for live preview)")
    func extractsLastBlock() {
        let text = """
        ```html
        <h1>Old version</h1>
        ```

        Actually, let me improve that:

        ```html
        <h1>New version</h1>
        ```
        """
        let html = HTMLExtractor.extractLastHTMLBlock(from: text)
        #expect(html != nil)
        #expect(html?.contains("New version") == true)
    }

    @Test("Returns nil when no html block for lastBlock")
    func lastBlockReturnsNil() {
        let text = "No HTML here."
        let html = HTMLExtractor.extractLastHTMLBlock(from: text)
        #expect(html == nil)
    }

    // MARK: - Detection

    @Test("Detects message containing html block")
    func detectsHTMLBlock() {
        let text = """
        Sure! Here you go:

        ```html
        <h1>Hi</h1>
        ```
        """
        #expect(HTMLExtractor.containsHTMLBlock(in: text) == true)
    }

    @Test("Detects message without html block")
    func detectsNoHTMLBlock() {
        let text = "Just text, no code."
        #expect(HTMLExtractor.containsHTMLBlock(in: text) == false)
    }

    @Test("Detects streaming html block (no closing fence)")
    func detectsStreamingHTMLBlock() {
        let text = """
        ```html
        <h1>Partial
        """
        #expect(HTMLExtractor.containsHTMLBlock(in: text) == true)
    }

    // MARK: - Edge Cases

    @Test("Handles empty string")
    func handlesEmptyString() {
        #expect(HTMLExtractor.extractHTMLBlocks(from: "").isEmpty)
        #expect(HTMLExtractor.extractLastHTMLBlock(from: "") == nil)
        #expect(HTMLExtractor.containsHTMLBlock(in: "") == false)
    }

    @Test("Handles html block with extra whitespace in fence")
    func handlesWhitespaceInFence() {
        let text = """
        ```html
        <h1>Spaced</h1>
        ```
        """
        let blocks = HTMLExtractor.extractHTMLBlocks(from: text)
        #expect(blocks.count == 1)
        #expect(blocks[0].contains("<h1>Spaced</h1>"))
    }

    @Test("Handles case-insensitive html tag in fence")
    func handlesCaseInsensitiveFence() {
        let text = """
        ```HTML
        <h1>Upper</h1>
        ```
        """
        let blocks = HTMLExtractor.extractHTMLBlocks(from: text)
        #expect(blocks.count == 1)
        #expect(blocks[0].contains("<h1>Upper</h1>"))
    }
}
