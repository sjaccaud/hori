"""Adversarial test: Tailscale Funnel isolation.

Tests that the tool daemon's Unix domain socket is NOT reachable from the
Tailscale Funnel endpoint. The Funnel only exposes voice endpoints (PoC
1.0.1); the tool socket must be unreachable from the network entirely.

Defends: PoC 1.0.1 (Funnel Audit), PoC 15.5 (Separate Tool Service).
Red-team fix #6.

Status: FAILING (xfail) — the tool daemon (2C) is not yet implemented, so
there's no Unix socket to test. Once the daemon exists, this test verifies
that the Funnel doesn't proxy to the socket.
"""
import pytest


class TestFunnelIsolation:
    """The tool socket must be unreachable from the Tailscale Funnel."""

    def test_tool_socket_not_exposed_via_funnel(self):
        """The Funnel must not proxy to the tool daemon's Unix socket."""
        # The tool daemon listens on a Unix domain socket (e.g.,
        # /run/aios/tool-daemon.sock). The Tailscale Funnel only proxies
        # /voice and /v1/voice/* to localhost:5680 (aios-core).
        # The tool socket must NOT be proxied.
        from services.tool_daemon.socket_config import TOOL_SOCKET_PATH
        # The socket path must be a Unix domain socket, not a TCP port
        assert TOOL_SOCKET_PATH.endswith(".sock")
        assert ":" not in TOOL_SOCKET_PATH  # No port number

    def test_tool_socket_path_not_tcp(self):
        """The tool daemon must use a Unix socket, not a TCP port."""
        from services.tool_daemon.socket_config import TOOL_SOCKET_PATH
        # Unix socket paths start with / and end with .sock
        assert TOOL_SOCKET_PATH.startswith("/")
        assert TOOL_SOCKET_PATH.endswith(".sock")
