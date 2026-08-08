"""
Socket configuration for the tool daemon.

The tool daemon listens on a Unix domain socket. This is NEVER exposed
over TCP or the Tailscale Funnel. aios-core connects to the socket
locally to issue tool calls.

Traces to: docs/roadmap.md Tier 2C, PoC 15.5.
Traces to: docs/tool_safety.md "Layer 2: The Cage".
"""
from hori.config import TOOL_SOCKET_PATH, SOCKET_DIR, AUDIT_LOG_PATH

# Re-exported for existing imports
__all__ = ["TOOL_SOCKET_PATH", "SOCKET_DIR", "AUDIT_LOG_PATH"]
