"""Unit test: autolisten URL parameter handler in voice.html.

Tests that the ?autolisten=1 URL parameter (UX-1.1) correctly auto-starts
listening on page load when present, and does nothing when absent.

Traces to: UX-1.1 PRD (docs/prd/ux-1-1-ios-streaming-deep-link.md),
Manifesto Pillar IV (Seamless Voice & Remote Interaction).
"""
import re
from pathlib import Path


VOICE_HTML = Path(__file__).resolve().parent / "static" / "voice.html"


def _get_autolisten_region(html: str) -> str:
    """Extract the checkAutoListenParam function body from voice.html."""
    match = re.search(r'function\s+checkAutoListenParam\s*\(\)', html)
    if match:
        # Return a generous region around the function
        start = max(0, match.start() - 100)
        return html[start:start + 2000]
    # Fallback: return a region around the first 'autolisten' mention
    idx = html.lower().find('autolisten')
    if idx >= 0:
        return html[max(0, idx - 200):idx + 2000]
    return ""


class TestAutolistenParam:
    """The autolisten URL param handler in voice.html."""

    def test_voice_html_contains_autolisten_handler(self):
        """voice.html must contain code that checks for the autolisten URL param."""
        html = VOICE_HTML.read_text()
        assert 'autolisten' in html.lower(), (
            "voice.html must contain an autolisten handler that checks "
            "for the ?autolisten=1 URL parameter."
        )

    def test_autolisten_calls_unlock_audio(self):
        """The autolisten handler must call unlockAudio() to unlock the iOS AudioContext."""
        html = VOICE_HTML.read_text()
        region = _get_autolisten_region(html)
        assert region, "autolisten handler not found in voice.html"
        assert 'unlockAudio' in region, (
            "autolisten handler must call unlockAudio() — iOS requires "
            "AudioContext unlock on a user gesture, and the Shortcut launch "
            "counts as that gesture."
        )

    def test_autolisten_calls_toggle_mic(self):
        """The autolisten handler must call toggleMic() to start listening."""
        html = VOICE_HTML.read_text()
        region = _get_autolisten_region(html)
        assert region, "autolisten handler not found in voice.html"
        assert 'toggleMic' in region, (
            "autolisten handler must call toggleMic() to start listening."
        )

    def test_autolisten_checks_url_param(self):
        """The handler must check window.location.search or URLSearchParams for the param."""
        html = VOICE_HTML.read_text()
        region = _get_autolisten_region(html)
        assert region, "autolisten handler not found in voice.html"
        assert 'location.search' in region or 'URLSearchParams' in region, (
            "autolisten handler must read the URL parameter from "
            "window.location.search or URLSearchParams."
        )

    def test_autolisten_does_not_fire_when_param_absent(self):
        """The handler must only auto-listen when autolisten=1 is present, not by default."""
        html = VOICE_HTML.read_text()
        region = _get_autolisten_region(html)
        assert region, "autolisten handler not found in voice.html"
        # There must be a conditional check (if statement) around the autolisten logic
        assert 'if' in region, (
            "autolisten handler must be conditional — only auto-listen when "
            "the param is present, not on every page load."
        )
