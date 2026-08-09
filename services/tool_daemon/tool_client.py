"""
PoC 16.1: Tool client for aios-core.

This is the client side of the tool daemon IPC. aios-core uses this to
send tool calls to the tool daemon over the Unix domain socket. The tool
daemon validates and executes the call; this client receives the result.

WHY IT EXISTS:
  This is the bridge between the LLM (which proposes tool calls) and the
  tool daemon (which validates and executes them). The LLM never touches
  the filesystem directly — it emits a JSON tool call, aios-core sends it
  via this client, the tool daemon validates and executes it, and the
  result comes back as structured data.

  The separation is critical: if the LLM is compromised via prompt
  injection, it can only emit tool calls that the tool daemon validates.
  It cannot bypass validation, access the filesystem directly, or modify
  the tool call mid-flight.

WHAT IT DEFENDS AGAINST:
  - Direct filesystem access by the LLM (impossible — LLM has no fs access)
  - Bypassing validation (impossible — tool daemon validates every call)
  - Network exfiltration (impossible — tool daemon has no network via Landlock)

TRACES TO:
  docs/roadmap.md Tier 3, PoC 16.1.
  docs/safety.md "Layer 2: The Cage".
"""
from __future__ import annotations

import json
import logging
import socket
from typing import Any

from .socket_config import TOOL_SOCKET_PATH

logger = logging.getLogger(__name__)


class ToolClient:
    """Client for the tool daemon's Unix domain socket.

    Sends tool call requests and receives results. Each request is a
    single-line JSON object; each response is a single-line JSON object.

    In production, the tool daemon runs as aios-worker with Landlock +
    seccomp. This client runs as the hori-core user and connects
    to the socket at /run/hori/tool-daemon.sock.
    """

    def __init__(self, socket_path: str = TOOL_SOCKET_PATH, timeout: float = 10.0):
        self.socket_path = socket_path
        self.timeout = timeout

    def call_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        conversation_id: str | None = None,
        turn_id: str | None = None,
        llm_reasoning: str | None = None,
    ) -> dict[str, Any]:
        """Send a tool call to the tool daemon and return the result.

        Args:
            tool_name: The name of the tool to call (e.g., "count_files").
            args: The tool arguments (e.g., {"path": "the home directory", "pattern": "*.py"}).
            conversation_id: Optional conversation ID for audit logging.
            turn_id: Optional turn ID for audit logging.
            llm_reasoning: Optional LLM reasoning for audit logging.

        Returns:
            The tool daemon's response dict. On success: {"result": {...}}.
            On validation failure: {"error": "...", "validation_failed": True}.
            On Sherpa block: {"error": "...", "sherpa_blocked": True}.
        """
        request = {
            "tool": tool_name,
            "args": args,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "llm_reasoning": llm_reasoning,
        }

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(self.socket_path)
                s.sendall((json.dumps(request) + "\n").encode("utf-8"))

                # Read the response (single line)
                buffer = b""
                while b"\n" not in buffer:
                    data = s.recv(4096)
                    if not data:
                        break
                    buffer += data

                response_line = buffer.decode("utf-8").strip()
                if not response_line:
                    return {"error": "Tool daemon returned empty response"}

                return json.loads(response_line)

        except (ConnectionRefusedError, FileNotFoundError) as e:
            logger.warning(f"Tool daemon not available: {e}")
            return {"error": f"Tool daemon not available: {e}", "daemon_unavailable": True}
        except socket.timeout:
            logger.warning("Tool daemon timed out")
            return {"error": "Tool daemon timed out", "daemon_timeout": True}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Tool daemon communication error: {e}")
            return {"error": f"Tool daemon communication error: {e}"}

    def is_available(self) -> bool:
        """Check if the tool daemon is running and reachable.

        Returns True if the socket exists and a connection can be established.
        Does NOT send a tool call — just checks connectivity.
        """
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect(self.socket_path)
            return True
        except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
            return False


# Module-level singleton for convenience
_client: ToolClient | None = None


def get_tool_client() -> ToolClient:
    """Get the shared tool client instance."""
    global _client
    if _client is None:
        _client = ToolClient()
    return _client
