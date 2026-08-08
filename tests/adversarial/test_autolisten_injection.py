"""Adversarial test: autolisten deep-link cannot bypass the safety spine.

Tests that the ?autolisten=1 URL parameter (UX-1.1) is a purely client-side
UI hint and does not create a new trust path that bypasses the safety spine.
Specifically:
- The autolisten param must NOT be forwarded to aios-core in the
  /v1/voice/chat/stream request body (it is not a server-side mode flag).
- The isBusy gating must still be enforced (no second request while AIOS
  is responding).
- The autolisten handler must not disable or skip any existing safety
  checks in the client-side code.

Defends: UX-1.1 streaming deep-link safety boundary.
Traces to: Manifesto Pillar VII (Simplicity as Security), UX Gameplan §5.
"""
import re
from pathlib import Path


VOICE_HTML = Path(__file__).resolve().parents[2] / "services" / "aios_core" / "static" / "voice.html"


class TestAutolistenInjection:
    """The autolisten deep-link must not bypass the safety spine."""

    def test_autolisten_not_forwarded_to_backend(self):
        """The autolisten param must not appear in the request body sent to aios-core."""
        html = VOICE_HTML.read_text()
        # Find the sendToAIOS function and check the JSON.stringify body
        # The request body should contain text, voice, speed, history — NOT autolisten
        match = re.search(r'JSON\.stringify\s*\(\s*\{([^}]+)\}', html)
        assert match, "Could not find JSON.stringify request body in voice.html"
        body = match.group(1)
        assert 'autolisten' not in body.lower(), (
            "autolisten param must NOT be forwarded to the backend — "
            "it is a client-side UI hint only, not a server-side mode flag. "
            f"Found in request body: {body}"
        )

    def test_isbusy_gating_preserved(self):
        """The isBusy check must still be present in sendToAIOS (no bypass via autolisten)."""
        html = VOICE_HTML.read_text()
        # The sendToAIOS function must still check isBusy at the top
        assert 'if (isBusy)' in html, (
            "isBusy gating must be preserved — autolisten must not allow "
            "sending a second request while AIOS is responding."
        )

    def test_autolisten_handler_is_client_side_only(self):
        """The autolisten handler must only call existing client-side functions, not new backend calls."""
        html = VOICE_HTML.read_text()
        # If autolisten handler exists, it should call toggleMic/unlockAudio,
        # not fetch() or XMLHttpRequest to a new endpoint
        if 'autolisten' in html.lower():
            # Find the autolisten handler region (within ~500 chars of the mention)
            idx = html.lower().find('autolisten')
            region = html[max(0, idx - 200):idx + 500]
            # It should NOT contain a fetch() call to a new endpoint
            assert 'fetch(' not in region or 'voice/chat/stream' in region, (
                "autolisten handler must not introduce new backend calls — "
                "it should only call existing client-side functions (toggleMic, unlockAudio)."
            )
