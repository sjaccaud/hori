import Testing
import Foundation
@testable import HORI

/// Tests for GuidanceFlyout detection logic.
///
/// The detection is client-side heuristic — it scans HORI's response
/// text for COULDN'T/SHOULDN'T patterns. The full vision uses a
/// CapabilityChecker on the server side, but for this slice we detect
/// from the response text.
@Suite("GuidanceFlyout Detection")
struct GuidanceFlyoutTests {

    // MARK: - COULDN'T Detection

    @Test("Detects 'I can only read files' as couldn't")
    func detectsReadOnlyLimitation() {
        let text = "I'd love to help, but I can only read files on your machine — I can't create or save new ones for you."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result != nil)
        #expect(result?.0 == .couldnt)
    }

    @Test("Detects 'I can't create' as couldn't")
    func detectsCantCreate() {
        let text = "I can't create new files, but I can show you the code."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result != nil)
        #expect(result?.0 == .couldnt)
    }

    @Test("Detects 'I can't save' as couldn't")
    func detectsCantSave() {
        let text = "I can't save files directly to your disk."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result != nil)
        #expect(result?.0 == .couldnt)
    }

    @Test("Detects 'I don't have permission' as couldn't")
    func detectsNoPermission() {
        let text = "I don't have permission to modify that file."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result != nil)
        #expect(result?.0 == .couldnt)
    }

    // MARK: - SHOULDN'T Detection

    @Test("Detects 'I shouldn't' as shouldn't")
    func detectsShouldnt() {
        let text = "I shouldn't delete that file — it's important for the system."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result != nil)
        #expect(result?.0 == .shouldnt)
    }

    @Test("Detects 'that would be unsafe' as shouldn't")
    func detectsUnsafe() {
        let text = "That would be unsafe to execute."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result != nil)
        #expect(result?.0 == .shouldnt)
    }

    @Test("Detects 'I won't do that' as shouldn't")
    func detectsWontDo() {
        let text = "I won't do that — it could harm your system."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result != nil)
        #expect(result?.0 == .shouldnt)
    }

    // MARK: - No Detection

    @Test("Returns nil for normal response")
    func noDetectionForNormal() {
        let text = "Here's a landing page for Mr Scruffy's photo album."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result == nil)
    }

    @Test("Returns nil for empty string")
    func noDetectionForEmpty() {
        let result = GuidanceFlyout.detect(in: "")
        #expect(result == nil)
    }

    @Test("Returns nil for helpful response about limitations")
    func noDetectionForHelpfulResponse() {
        let text = "I can help you with that! Let me write the HTML for you."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result == nil)
    }

    // MARK: - Message Extraction

    @Test("Extracts relevant message from response")
    func extractsMessage() {
        let text = "I'd love to help, but I can only read files on your machine — I can't create or save new ones for you. If you want, I can write out the full HTML."
        let result = GuidanceFlyout.detect(in: text)
        #expect(result != nil)
        #expect(result?.1.isEmpty == false)
        // The message should contain some context
        #expect(result?.1.count ?? 0 > 10)
    }

    @Test("Truncates long messages")
    func truncatesLongMessages() {
        let longText = "I can't create new files. " + String(repeating: "This is a very long explanation. ", count: 20)
        let result = GuidanceFlyout.detect(in: longText)
        #expect(result != nil)
        #expect(result?.1.count ?? 0 <= 103) // 100 + "..."
    }
}
