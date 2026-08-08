"""Unit test: chat typing surface (chat.html + /chat route).

Tests that the /chat web page (UX-1.2) is a keyboard-first, distraction-free
text chat surface that uses the existing streaming endpoint.

Traces to: UX-1.2 PRD (docs/prd/ux-1-2-laptop-typing-surface.md),
Manifesto Pillar IV (multimodal input/output).
"""
import re
from pathlib import Path


CHAT_HTML = Path(__file__).resolve().parent / "static" / "chat.html"
MAIN_PY = Path(__file__).resolve().parent / "main.py"


class TestChatSurface:
    """The /chat typing surface in chat.html + main.py."""

    def test_chat_html_exists(self):
        """chat.html must exist and be non-empty."""
        assert CHAT_HTML.exists(), "services/aios_core/static/chat.html must exist."
        content = CHAT_HTML.read_text()
        assert len(content) > 100, "chat.html must be a real page, not empty."

    def test_chat_html_has_autofocus(self):
        """The input must auto-focus on page load (zero mouse travel)."""
        html = CHAT_HTML.read_text()
        assert 'autofocus' in html or '.focus()' in html, (
            "chat.html must auto-focus the input on load — "
            "the surface is keyboard-first, zero mouse travel."
        )

    def test_chat_html_has_enter_to_send(self):
        """Enter must send, Shift+Enter for newline."""
        html = CHAT_HTML.read_text()
        # Must have a keydown listener that checks for Enter
        assert 'keydown' in html or 'onkeydown' in html, (
            "chat.html must have a keydown handler for Enter-to-send."
        )
        assert 'Enter' in html or 'enter' in html, (
            "chat.html must check for the Enter key in its keydown handler."
        )

    def test_chat_html_has_streaming(self):
        """The chat page must use streaming (SSE or fetch ReadableStream)."""
        html = CHAT_HTML.read_text()
        assert 'stream' in html.lower(), (
            "chat.html must use streaming (stream: true in the request) "
            "for low-latency response display."
        )
        # Must use either fetch with ReadableStream or EventSource
        assert 'fetch' in html or 'EventSource' in html, (
            "chat.html must use fetch() or EventSource for streaming."
        )

    def test_chat_html_has_conversation_history(self):
        """The chat page must maintain conversation history for context."""
        html = CHAT_HTML.read_text()
        assert 'conversation' in html.lower() or 'messages' in html.lower(), (
            "chat.html must maintain conversation history (messages array) "
            "for multi-turn context."
        )

    def test_chat_html_no_model_picker(self):
        """The chat page must NOT have a model picker (Pillar VII — minimal surface)."""
        html = CHAT_HTML.read_text()
        assert 'model-picker' not in html.lower(), (
            "chat.html must NOT have a model picker — it is intentionally minimal."
        )
        assert '<select' not in html or 'model' not in html.lower(), (
            "chat.html must NOT have a model selector dropdown."
        )

    def test_chat_html_no_sidebar(self):
        """The chat page must NOT have a sidebar (Pillar VII — minimal surface)."""
        html = CHAT_HTML.read_text()
        assert 'sidebar' not in html.lower(), (
            "chat.html must NOT have a sidebar — one input, one response, nothing else."
        )

    def test_chat_route_in_main(self):
        """main.py must have a /chat route serving chat.html."""
        main_src = MAIN_PY.read_text()
        assert '@app.get("/chat")' in main_src or "@app.get('//chat')" in main_src, (
            "main.py must have an @app.get('/chat') route handler."
        )
        assert 'chat.html' in main_src, (
            "main.py must serve chat.html from the /chat route."
        )
