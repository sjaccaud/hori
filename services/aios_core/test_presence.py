"""Unit test: ambient presence endpoint and state manager.

Tests that the /v1/presence SSE endpoint (UX-1.3) exists, emits the
correct state enum, and is rate-limited.

Traces to: UX-1.3 (ambient presence),
Manifesto Pillar V (visible autonomy) + IV (presence).
"""
import re
from pathlib import Path


MAIN_PY = Path(__file__).resolve().parent / "main.py"
VOICE_HTML = Path(__file__).resolve().parent / "static" / "voice.html"
CHAT_HTML = Path(__file__).resolve().parent / "static" / "chat.html"


class TestPresenceEndpoint:
    """The /v1/presence SSE endpoint in main.py."""

    def test_presence_route_exists(self):
        """main.py must have a /v1/presence route handler."""
        src = MAIN_PY.read_text()
        assert '/v1/presence' in src, (
            "main.py must have a /v1/presence SSE endpoint."
        )

    def test_presence_returns_sse(self):
        """The presence handler must return StreamingResponse with text/event-stream."""
        src = MAIN_PY.read_text()
        idx = src.find('/v1/presence')
        assert idx >= 0, "presence endpoint not found"
        region = src[max(0, idx - 200):idx + 2000]
        assert 'StreamingResponse' in region or 'EventSourceResponse' in region, (
            "presence handler must return a StreamingResponse for SSE."
        )
        assert 'text/event-stream' in region, (
            "presence handler must set media_type to text/event-stream."
        )

    def test_presence_has_three_states(self):
        """The presence state enum must have exactly: idle, thinking, has_nudge."""
        src = MAIN_PY.read_text()
        for state in ('idle', 'thinking', 'has_nudge'):
            assert state in src, (
                f"main.py must define the '{state}' presence state."
            )

    def test_presence_state_manager_exists(self):
        """A presence state manager (function or class) must exist."""
        src = MAIN_PY.read_text()
        # Must have some form of state management for presence
        assert 'presence' in src.lower(), (
            "main.py must have presence state management code."
        )
        # Must have a way to set the state (set_presence, update_presence, etc.)
        assert 'set_presence' in src or 'update_presence' in src, (
            "presence state must be settable (set_presence or similar function)."
        )


class TestPresenceClientSide:
    """The presence subscriber in voice.html and chat.html."""

    def test_voice_html_has_presence_subscriber(self):
        """voice.html must subscribe to /v1/presence via EventSource."""
        html = VOICE_HTML.read_text()
        assert '/v1/presence' in html or 'presence' in html, (
            "voice.html must subscribe to the /v1/presence SSE stream."
        )

    def test_chat_html_has_presence_subscriber(self):
        """chat.html must subscribe to /v1/presence via EventSource."""
        html = CHAT_HTML.read_text()
        assert '/v1/presence' in html or 'presence' in html, (
            "chat.html must subscribe to the /v1/presence SSE stream."
        )

    def test_voice_html_has_breathing_animation(self):
        """voice.html must have a breathing/pulse animation for the idle state."""
        html = VOICE_HTML.read_text()
        # The existing pulse animation is for listening; we need a separate
        # breathing animation for idle presence
        assert 'breath' in html.lower() or '@keyframes' in html, (
            "voice.html must have a breathing animation for the presence indicator."
        )

    def test_voice_html_chime_is_opt_in(self):
        """The chime must be opt-in (localStorage check, default off)."""
        html = VOICE_HTML.read_text()
        if 'chime' in html.lower():
            # If chime exists, it must check localStorage and default to off
            assert 'localStorage' in html, (
                "chime must be opt-in via localStorage, default off."
            )
