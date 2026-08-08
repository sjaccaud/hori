"""
PoC 15.5: Separate Tool Service (Unix Domain Socket Server)

The tool execution runs as a SEPARATE PROCESS from aios-core, as user
aios-worker. If aios-core is compromised via prompt injection, the tool
service still holds. aios-core proposes; the tool service validates and
executes. The LLM cannot influence validation logic.

IPC via Unix domain socket — never exposed over TCP or the Tailscale Funnel.

Traces to: docs/roadmap.md Tier 2C, PoC 15.5.
Traces to: docs/tool_safety.md "Layer 2: The Cage".

Protocol:
  aios-core sends a JSON request: {"tool": "count_files", "args": {...}}
  The tool daemon responds: {"result": {...}} or {"error": "..."}

  The daemon validates every call (PoC 15.4) before executing. If
  validation fails, the error is returned to aios-core, which can send
  it back to the LLM for a retry (max 3 retries).

Design:
  - The server uses a simple line-delimited JSON protocol over a Unix
    domain socket. Each request is one JSON object on one line; each
    response is one JSON object on one line.
  - The server is single-threaded (the spine doesn't need concurrency —
    the LLM makes one tool call at a time). Concurrency is AIOS 2.0.
  - The server logs every call to the audit log (PoC 15.9) before
    executing, so even a crash mid-execution leaves a forensic record.
  - The server checks the fail-closed gate (PoC 15.38) at startup.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .fail_closed import (
    SafetyCheckResult,
    check_safety_prerequisites,
    verify_landlock_available,
    verify_seccomp_available,
)
from .landlock import AllowedPath, apply_landlock_restrictions
from .registry import get_tool
from .seccomp_filter import apply_seccomp_filter
from .sherpa_interface import DEFAULT_CAPABILITY_FILE, SherpaCapabilityFile
from .socket_config import AUDIT_LOG_PATH, TOOL_SOCKET_PATH
from .tools import execute_tool
from .validation import validate_tool_call


class ToolDaemon:
    """The tool daemon server.

    Listens on a Unix domain socket, validates incoming tool calls,
    executes them, and logs every call to the audit log.

    In production, this runs as user aios-worker under systemd. In
    development, it runs as the current user with degraded mode.
    """

    def __init__(
        self,
        socket_path: str = TOOL_SOCKET_PATH,
        audit_log_path: str = AUDIT_LOG_PATH,
        degraded_mode: bool = False,
        sherpa_capability_path: str = DEFAULT_CAPABILITY_FILE,
    ):
        self.socket_path = socket_path
        self.audit_logger = AuditLogger(audit_log_path)
        self.degraded_mode = degraded_mode
        self._running = False
        self._lock = threading.Lock()
        # Sherpa capability interface. In degraded mode, we still create
        # the object but skip the check in handle_request (no Sherpa
        # running in dev/testing).
        self.sherpa = SherpaCapabilityFile(path=sherpa_capability_path)

    def check_safety(self) -> SafetyCheckResult:
        """Check safety prerequisites at startup. Fail-closed if missing."""
        landlock = verify_landlock_available()
        seccomp = verify_seccomp_available()
        return check_safety_prerequisites(
            landlock_available=landlock,
            seccomp_available=seccomp,
            degraded_mode_opt_in=self.degraded_mode,
        )

    def apply_kernel_restrictions(self) -> bool:
        """Apply seccomp-bpf and Landlock restrictions to this process.

        Called AFTER the socket is bound (so the socket file already
        exists and /run/aios doesn't need to be in the Landlock allow
        list) but BEFORE accepting connections.

        In degraded mode (development/testing), this is skipped.

        Returns True if restrictions were applied successfully, False if
        degraded mode (restrictions skipped).
        """
        if self.degraded_mode:
            print("DEGRADED MODE: skipping kernel restrictions (seccomp + Landlock)",
                  file=sys.stderr)
            return False

        # Apply seccomp first (blocks dangerous syscalls)
        seccomp_result = apply_seccomp_filter()
        if not seccomp_result.success:
            print(f"FATAL: seccomp filter failed: {seccomp_result.error}",
                  file=sys.stderr)
            sys.exit(1)
        print(f"seccomp: {seccomp_result.blocked_count} syscalls blocked "
              f"({seccomp_result.arch})", file=sys.stderr)

        # Apply Landlock (default-deny filesystem + deny all network)
        # Add the audit log path as a read-write allowed path.
        audit_abs = os.path.abspath(str(self.audit_logger.log_path))
        audit_dir = os.path.dirname(audit_abs)
        # Workspace and allowed read paths come from config.
        # The home directory and model directory are allowed read-only
        # so tools can read files the user asks about. The workspace
        # is read-write for tool operations.
        from hori.config import WORKSPACE_PATH
        home_dir = str(Path.home())
        allowed_paths = [
            AllowedPath(home_dir, read_only=True),
            AllowedPath(WORKSPACE_PATH, read_only=False),
            AllowedPath(audit_dir, read_only=False),
            # Sherpa capability file (PoC 15.50). The daemon reads this
            # before every tool call to check the current capability level.
            AllowedPath("/run/sherpa", read_only=True),
        ]
        landlock_result = apply_landlock_restrictions(
            allowed_paths=allowed_paths,
            deny_all_network=True,
        )
        if not landlock_result.success:
            print(f"FATAL: Landlock restriction failed: {landlock_result.error}",
                  file=sys.stderr)
            sys.exit(1)
        print(f"landlock: ABI {landlock_result.abi_version}, "
              f"{len(landlock_result.allowed_paths)} paths allowed, "
              f"network denied", file=sys.stderr)

        return True

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle a single tool call request.

        This is the core method: validate -> log -> execute -> log result.
        It's called by the socket server for each incoming request, and
        is also directly callable for testing (no socket needed).
        """
        tool_name = request.get("tool", "")
        args = request.get("args", {})
        conversation_id = request.get("conversation_id")
        turn_id = request.get("turn_id")
        llm_reasoning = request.get("llm_reasoning")

        # 0. Check Sherpa capability level (PoC 15.50)
        # The Sherpa is the behavioral guardian. If it has reduced
        # capabilities or died (stale timestamp → Level 4), we respect
        # that here. In degraded mode, skip this check.
        if not self.degraded_mode:
            level = self.sherpa.get_level()
            if not self.sherpa.is_tool_allowed(tool_name):
                self.audit_logger.log(
                    tool_name=tool_name,
                    args=args if isinstance(args, dict) else {},
                    result={"error": f"Sherpa capability level {level}: tool blocked"},
                    llm_reasoning=llm_reasoning,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
                return {
                    "error": f"Tool blocked by Sherpa (capability level {level}). "
                    f"Allowed tools: {self.sherpa.get_allowed_tools()}",
                    "sherpa_blocked": True,
                    "sherpa_level": level,
                }

        # 1. Validate the tool call against the registry and schema
        validation = validate_tool_call(tool_name, args)
        if not validation.valid:
            # Log the failed validation attempt
            self.audit_logger.log(
                tool_name=tool_name,
                args=args if isinstance(args, dict) else {},
                result={"error": validation.error},
                llm_reasoning=llm_reasoning,
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
            return {"error": validation.error, "validation_failed": True}

        # 2. Execute the tool (validated_args are canonicalized)
        result = execute_tool(tool_name, validation.validated_args or {})

        # 3. Determine if the result data is tainted (could contain
        #    prompt injection from file content)
        data_tainted = tool_name == "read_file" and "content" in result

        # 4. Log the execution
        self.audit_logger.log(
            tool_name=tool_name,
            args=validation.validated_args or {},
            result=result,
            llm_reasoning=llm_reasoning,
            data_tainted=data_tainted,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )

        return {"result": result}

    def start(self) -> None:
        """Start the Unix domain socket server.

        Checks safety prerequisites first (fail-closed). Then listens
        for connections and handles requests one at a time.
        """
        # Fail-closed check
        safety = self.check_safety()
        if not safety.can_start:
            print(f"REFUSING TO START: {safety.reason}", file=sys.stderr)
            sys.exit(1)
        if safety.degraded_mode:
            print(f"WARNING: {safety.reason}", file=sys.stderr)

        # Ensure socket directory exists
        socket_dir = os.path.dirname(self.socket_path)
        if socket_dir:
            os.makedirs(socket_dir, exist_ok=True)

        # Remove stale socket
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        # Create and bind the Unix domain socket
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_socket.bind(self.socket_path)

        # Set permissions: world can connect to the socket. The security
        # is in the daemon (validation, Sherpa, Landlock, seccomp), not in
        # the socket permissions. aios-core and the tool daemon run as
        # separate users — they share no group, so the socket must be
        # world-accessible for aios-core to reach it.
        os.chmod(self.socket_path, 0o666)

        # Apply kernel restrictions (seccomp + Landlock) AFTER binding
        # the socket (so /run/aios doesn't need Landlock access) but
        # BEFORE listening. This is the point of no return — after this,
        # the process cannot access anything outside the allowed paths.
        self.apply_kernel_restrictions()

        server_socket.listen(5)

        self._running = True
        print(f"Tool daemon listening on {self.socket_path}", file=sys.stderr)

        try:
            while self._running:
                conn, _ = server_socket.accept()
                try:
                    self._handle_connection(conn)
                except Exception as e:
                    print(f"Error handling connection: {e}", file=sys.stderr)
                finally:
                    conn.close()
        except KeyboardInterrupt:
            pass
        finally:
            server_socket.close()
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
            print("Tool daemon stopped.", file=sys.stderr)

    def _handle_connection(self, conn: socket.socket) -> None:
        """Handle a single client connection.

        Reads one line of JSON, processes it, and sends one line of JSON back.
        """
        # Read the request (line-delimited JSON)
        buffer = b""
        while b"\n" not in buffer:
            data = conn.recv(4096)
            if not data:
                return  # Connection closed before sending a complete request
            buffer += data
            if len(buffer) > 65536:  # 64KB max request size
                response = {"error": "Request too large"}
                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                return

        line = buffer.split(b"\n")[0].decode("utf-8").strip()
        if not line:
            return

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            response = {"error": f"Invalid JSON: {e}"}
            conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
            return

        with self._lock:
            response = self.handle_request(request)

        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))

    def stop(self) -> None:
        """Stop the server."""
        self._running = False


def main() -> None:
    """Entry point for the tool daemon.

    Run as: python3 -m services.tool_daemon.server
    In production: systemd service as user aios-worker.
    In development: --degraded flag to skip Landlock/seccomp check.

    The audit log path comes from hori.yaml (paths.audit_log).
    Env var HORI_AUDIT_LOG (or legacy AIOS_AUDIT_LOG) overrides.
    """
    degraded = "--degraded" in sys.argv
    audit_log = AUDIT_LOG_PATH
    daemon = ToolDaemon(
        degraded_mode=degraded,
        audit_log_path=audit_log,
    )
    daemon.start()


if __name__ == "__main__":
    main()
