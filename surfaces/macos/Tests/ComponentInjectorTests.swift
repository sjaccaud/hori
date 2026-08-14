import Testing
@testable import HORI

/// Tests for ComponentInjector (Component Injection slice).
///
/// Verifies that ```component blocks are correctly extracted from
/// HORI's replies and parsed into Component structs with the right
/// target, position, HTML, and JavaScript.
@Suite("ComponentInjector")
struct ComponentInjectorTests {

    @Test("Extracts a basic component")
    func extractsBasicComponent() {
        let text = """
        Here's the save button:

        ```component
        target: #picker-container
        position: after
        <button id="save">Save Color</button>
        <script>
        document.getElementById("save").addEventListener("click", () => {
          localStorage.setItem("color", document.getElementById("picker").value);
        });
        </script>
        ```
        """
        let component = ComponentInjector.extractComponent(from: text)
        #expect(component != nil)
        #expect(component?.insertTarget == "#picker-container")
        #expect(component?.insertPosition == .after)
        #expect(component?.html.contains("<button") == true)
        #expect(component?.html.contains("Save Color") == true)
        #expect(component?.javascript?.contains("addEventListener") == true)
        #expect(component?.javascript?.contains("localStorage") == true)
    }

    @Test("Extracts component with default position")
    func extractsDefaultPosition() {
        let text = """
        ```component
        target: body
        <div class="header">Welcome</div>
        ```
        """
        let component = ComponentInjector.extractComponent(from: text)
        #expect(component != nil)
        #expect(component?.insertTarget == "body")
        #expect(component?.insertPosition == .appendChild)
        #expect(component?.javascript == nil)
        #expect(component?.html.contains("Welcome") == true)
    }

    @Test("Extracts component with no target (defaults to body)")
    func extractsNoTarget() {
        let text = """
        ```component
        <p>Hello!</p>
        ```
        """
        let component = ComponentInjector.extractComponent(from: text)
        #expect(component != nil)
        #expect(component?.insertTarget == nil)
        #expect(component?.insertPosition == .appendChild)
        #expect(component?.html == "<p>Hello!</p>")
    }

    @Test("Returns nil for no component block")
    func returnsNilForNoComponent() {
        let text = "I can help with that!"
        let component = ComponentInjector.extractComponent(from: text)
        #expect(component == nil)
    }

    @Test("Returns nil for HTML block (not component)")
    func returnsNilForHTMLBlock() {
        let text = """
        ```html
        <html><body>Hello</body></html>
        ```
        """
        let component = ComponentInjector.extractComponent(from: text)
        #expect(component == nil)
    }

    @Test("Parses all insert positions")
    func parsesAllPositions() {
        let positions: [(String, ComponentInjector.InsertPosition)] = [
            ("appendChild", .appendChild),
            ("prependChild", .prependChild),
            ("before", .before),
            ("after", .after),
            ("replace", .replace),
        ]

        for (posStr, expected) in positions {
            let text = """
            ```component
            position: \(posStr)
            <div>test</div>
            ```
            """
            let component = ComponentInjector.extractComponent(from: text)
            #expect(component?.insertPosition == expected, "Failed for position: \(posStr)")
        }
    }

    @Test("Extracts component with multiline HTML")
    func extractsMultilineHTML() {
        let text = """
        ```component
        target: #container
        position: appendChild
        <div class="panel">
          <h2>Settings</h2>
          <label>Theme: <select id="theme"><option>Dark</option><option>Light</option></select></label>
        </div>
        ```
        """
        let component = ComponentInjector.extractComponent(from: text)
        #expect(component != nil)
        #expect(component?.html.contains("<div class=\"panel\">") == true)
        #expect(component?.html.contains("<select") == true)
        #expect(component?.html.contains("</div>") == true)
    }

    @Test("Extracts component with complex JavaScript")
    func extractsComplexJS() {
        let text = """
        ```component
        target: #picker
        position: after
        <button id="save">Save</button>
        <script>
        const btn = document.getElementById("save");
        btn.addEventListener("click", function() {
          const color = document.getElementById("picker").value;
          const colors = JSON.parse(localStorage.getItem("colors") || "[]");
          colors.push(color);
          localStorage.setItem("colors", JSON.stringify(colors));
          btn.textContent = "Saved!";
          setTimeout(() => { btn.textContent = "Save"; }, 1000);
        });
        </script>
        ```
        """
        let component = ComponentInjector.extractComponent(from: text)
        #expect(component != nil)
        #expect(component?.javascript?.contains("addEventListener") == true)
        #expect(component?.javascript?.contains("localStorage") == true)
        #expect(component?.javascript?.contains("JSON.parse") == true)
        #expect(component?.javascript?.contains("setTimeout") == true)
    }

    @Test("Handles case-insensitive component tag")
    func handlesCaseInsensitive() {
        let text = """
        ```Component
        target: body
        <p>test</p>
        ```
        """
        let component = ComponentInjector.extractComponent(from: text)
        #expect(component != nil)
        #expect(component?.html == "<p>test</p>")
    }
}
