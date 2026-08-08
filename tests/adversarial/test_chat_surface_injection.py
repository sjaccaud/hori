"""Adversarial test: chat typing surface cannot bypass the safety spine.

Tests that the /chat web page (UX-1.2) does not create a new trust path
that bypasses the safety spine. Specifically:
- The chat page must send requests to /v1/chat/completions (the existing
  gated endpoint), not to any raw LLM endpoint or direct llama-server.
- The chat page must not include tool-call schemas or tool-invocation
  logic — it is a text-only surface.
- The chat page must not expose admin API endpoints or the admin token.

Defends: UX-1.2 typing surface safety boundary.
Traces to: Manifesto Pillar VII (Simplicity as Security), UX Gameplan §5.
"""
import re
from pathlib import Path


CHAT_HTML = Path(__file__).resolve().parents[2] / "services" / "aios_core" / "static" / "chat.html"
MAIN_PY = Path(__file__).resolve().parents[2] / "services" / "aios_core" / "main.py"


class TestChatSurfaceInjection:
    """The /chat typing surface must not bypass the safety spine."""

    def test_chat_html_exists(self):
        """chat.html must exist (prerequisite for all other checks)."""
        assert CHAT_HTML.exists(), (
            "services/aios_core/static/chat.html must exist for UX-1.2."
        )

    def test_chat_uses_gated_endpoint(self):
        """The chat page must send requests to /v1/chat/completions, not a raw LLM endpoint."""
        if not CHAT_HTML.exists():
            pytest.skip("chat.html not yet created")
        html = CHAT_HTML.read_text()
        # Must reference /v1/chat/completions (the gated endpoint)
        assert '/v1/chat/completions' in html or 'chat/completions' in html, (
            "chat.html must send requests to /v1/chat/completions (the existing "
            "gated endpoint with red-team, memory, and response verification)."
        )
        # Must NOT reference raw llama-server endpoint (port 8080)
        assert ':8080' not in html, (
            "chat.html must NOT connect directly to llama-server — "
            "all requests must go through aios-core's gated /v1/chat/completions."
        )

    def test_chat_no_tool_call_logic(self):
        """The chat page must not include tool-call schemas or tool invocation."""
        if not CHAT_HTML.exists():
            pytest.skip("chat.html not yet created")
        html = CHAT_HTML.read_text()
        # Must not contain tool_call, tool_call_id, or function schema patterns
        assert 'tool_call' not in html.lower(), (
            "chat.html must not include tool-call logic — it is a text-only surface. "
            "Tool-augmented chat is handled by the voice app's existing pipeline."
        )
        assert '"type": "function"' not in html, (
            "chat.html must not include function/tool schemas."
        )

    def test_chat_no_admin_endpoints(self):
        """The chat page must not reference admin API endpoints or the admin token."""
        if not CHAT_HTML.exists():
            pytest.skip("chat.html not yet created")
        html = CHAT_HTML.read_text()
        assert '/admin/api' not in html, (
            "chat.html must not reference admin API endpoints."
        )
        assert 'AIOS_ADMIN_TOKEN' not in html, (
            "chat.html must not reference the admin token."
        )

    def test_chat_route_in_main_py(self):
        """main.py must have a /chat route that serves chat.html."""
        main_src = MAIN_PY.read_text()
        assert '/chat' in main_src, (
            "main.py must have a /chat route handler for the typing surface."
        )
        assert 'chat.html' in main_src, (
            "main.py must serve chat.html from the /chat route."
        )


# Import pytest at module level for skip
import pytest
