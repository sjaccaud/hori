"""System root broker daemon.

A single-threaded Unix domain socket server that validates, audits, and
executes a narrow set of privileged operations.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .allowlist import Allowlist
from .audit import BrokerAuditEntry, BrokerAuditLogger
from .config import (
    BROKER_ALLOWLIST_PATH,
    BROKER_AUDIT_LOG_PATH,
    BROKER_SECRETS_PATH,
    BROKER_SOCKET_PATH,
    DEFAULT_TIMEOUT,
    MAX_REQUEST_SIZE,
)


class RootBroker:
    """The privileged-operations broker.

    Listens on a Unix socket, validates incoming requests against an
    allowlist, executes the requested operation, and logs the result.
    """

    def __init__(
        self,
        socket_path: str | Path = BROKER_SOCKET_PATH,
        allowlist_path: str | Path = BROKER_ALLOWLIST_PATH,
        audit_log_path: str | Path = BROKER_AUDIT_LOG_PATH,
        secrets_path: str | Path = BROKER_SECRETS_PATH,
        token: str | None = None,
        degraded_mode: bool = False,
    ):
        self.socket_path = Path(socket_path)
        self.allowlist = Allowlist(allowlist_path)
        self.audit_logger = BrokerAuditLogger(audit_log_path)
        self.secrets_path = Path(secrets_path)
        self._expected_token = token or self._load_token()
        self.degraded_mode = degraded_mode
        self._running = False
        self._server = None

    def _load_token(self) -> str | None:
        if not self.secrets_path.exists():
            return None
        for line in self.secrets_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("ROOT_BROKER_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
        return None

    def _read_request(self, conn: socket.socket) -> dict[str, Any] | None:
        """Read one line-delimited JSON request from the socket."""
        chunks: list[bytes] = []
        total = 0
        while total < MAX_REQUEST_SIZE:
            try:
                chunk = conn.recv(4096)
            except OSError:
                return None
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if b"\n" in chunk:
                break
        if not chunks:
            return None
        data = b"".join(chunks).split(b"\n", 1)[0]
        try:
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _send_response(self, conn: socket.socket, response: dict[str, Any]) -> None:
        conn.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")

    def _authenticate(self, token: str | None) -> bool:
        """Fail-closed if no token is configured or token does not match."""
        if self._expected_token is None:
            return False
        if token is None:
            return False
        # Constant-time comparison to avoid timing attacks.
        return len(token) == len(self._expected_token) and token == self._expected_token

    def _run_command(
        self,
        command: list[str],
        timeout: float,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Run a subprocess with a hard timeout.

        The broker process itself does not run as root; the install script
        sets up a narrow sudoers or PolicyKit rule so the `root-broker` user
        can run specific `systemctl` commands.
        """
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                shell=False,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            if exc.stdout is not None:
                return -1, exc.stdout.decode("utf-8", errors="replace"), "timeout"
            return -1, "", "timeout"
        except FileNotFoundError:
            return 127, "", f"command not found: {command[0]}"
        except OSError as exc:
            return 126, "", f"failed to execute {command[0]}: {exc}"

    def _default_for(self, operation: str, param: str) -> Any:
        """Return the default value for an optional parameter."""
        spec = self.allowlist._data.get(operation, {})
        entry = spec.get(param)
        if isinstance(entry, dict):
            return entry.get("default")
        return None

    def _execute(
        self,
        operation: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch to the operation handler."""
        handler = getattr(self, f"_op_{operation}", None)
        if handler is None:
            return {"status": "error", "error": f"Unknown operation: {operation}"}
        return handler(params)

    def _op_service_restart(self, params: dict[str, Any]) -> dict[str, Any]:
        service = str(params.get("service"))
        timeout = float(
            params.get(
                "timeout",
                self._default_for("service_restart", "timeout") or DEFAULT_TIMEOUT,
            )
        )
        command = ["sudo", "-n", "systemctl", "restart", service]
        exit_code, stdout, stderr = self._run_command(command, timeout)
        return {
            "status": "ok" if exit_code == 0 else "error",
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        }

    def _op_service_status(self, params: dict[str, Any]) -> dict[str, Any]:
        service = str(params.get("service"))
        timeout = float(
            params.get(
                "timeout",
                self._default_for("service_status", "timeout") or DEFAULT_TIMEOUT,
            )
        )
        command = ["sudo", "-n", "systemctl", "is-active", service]
        exit_code, stdout, stderr = self._run_command(command, timeout)
        return {
            "status": "ok" if exit_code == 0 else "error",
            "exit_code": exit_code,
            "stdout": stdout.strip(),
            "stderr": stderr,
        }

    def _op_service_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        service = str(params.get("service"))
        timeout = float(
            params.get(
                "timeout",
                self._default_for("service_stop", "timeout") or DEFAULT_TIMEOUT,
            )
        )
        command = ["sudo", "-n", "systemctl", "stop", service]
        exit_code, stdout, stderr = self._run_command(command, timeout)
        return {
            "status": "ok" if exit_code == 0 else "error",
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        }

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Process a single request and return the response.

        This is the core gate: authenticate -> allowlist -> execute -> audit.
        """
        request_id = str(request.get("request_id", uuid.uuid4()))
        token = request.get("token")
        operation = request.get("operation", "")
        params = request.get("params", {}) or {}

        if not self._authenticate(token):
            self._audit(
                request_id=request_id,
                operation=operation,
                params=params,
                allowed=False,
                confirmed_by=None,
                exit_code=None,
                stdout="",
                stderr="",
                error="authentication failed",
            )
            return {
                "request_id": request_id,
                "status": "denied",
                "error": "authentication failed",
            }

        if not self.allowlist.is_operation_allowed(operation):
            self._audit(
                request_id=request_id,
                operation=operation,
                params=params,
                allowed=False,
                confirmed_by=token,
                exit_code=None,
                stdout="",
                stderr="",
                error=f"operation '{operation}' is not allowed",
            )
            return {
                "request_id": request_id,
                "status": "denied",
                "error": f"operation '{operation}' is not allowed",
            }

        ok, reason = self.allowlist.validate_params(operation, params)
        if not ok:
            self._audit(
                request_id=request_id,
                operation=operation,
                params=params,
                allowed=False,
                confirmed_by=token,
                exit_code=None,
                stdout="",
                stderr="",
                error=reason,
            )
            return {"request_id": request_id, "status": "denied", "error": reason}

        result = self._execute(operation, params)

        self._audit(
            request_id=request_id,
            operation=operation,
            params=params,
            allowed=True,
            confirmed_by=token,
            exit_code=result.get("exit_code"),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            error=result.get("error"),
        )

        result["request_id"] = request_id
        return result

    def _audit(
        self,
        *,
        request_id: str,
        operation: str,
        params: dict[str, Any],
        allowed: bool,
        confirmed_by: str | None,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        error: str | None,
    ) -> None:
        entry = BrokerAuditEntry(
            timestamp=time.time(),
            request_id=request_id,
            caller_uid=os.getuid(),
            caller_exe=None,
            operation=operation,
            params=params,
            allowed=allowed,
            confirmed_by=confirmed_by,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            error=error,
            sandbox_profile="none",
            revert_id=None,
        )
        self.audit_logger.log(entry)

    def _handle_connection(self, conn: socket.socket, addr: Any) -> None:
        try:
            request = self._read_request(conn)
            if request is None:
                self._send_response(
                    conn, {"status": "error", "error": "invalid request"}
                )
                return
            response = self.handle_request(request)
            self._send_response(conn, response)
        except Exception as exc:
            self._send_response(conn, {"status": "error", "error": str(exc)})
        finally:
            conn.close()

    def start(self) -> None:
        """Start the broker server and block until shutdown."""
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.socket_path))
        self._server.listen(4)
        self._running = True

        # Restrict socket ownership so only the configured clients can connect.
        os.chmod(self.socket_path, 0o660)

        def _signal_handler(signum: int, frame: Any) -> None:
            self.shutdown()

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, _signal_handler)
            signal.signal(signal.SIGINT, _signal_handler)

        print(f"Root broker listening on {self.socket_path}", file=sys.stderr)
        while self._running:
            try:
                conn, _ = self._server.accept()
                self._handle_connection(conn, None)
            except OSError:
                break

    def shutdown(self) -> None:
        self._running = False
        if self._server:
            self._server.close()


def main() -> int:
    """CLI entry point for the system-root-broker service."""
    import argparse

    parser = argparse.ArgumentParser(description="System root broker")
    parser.add_argument("--token", default=None, help="Broker auth token")
    parser.add_argument("--socket", default=BROKER_SOCKET_PATH, help="Unix socket path")
    parser.add_argument(
        "--allowlist", default=BROKER_ALLOWLIST_PATH, help="Allowlist JSON path"
    )
    parser.add_argument(
        "--audit-log", default=BROKER_AUDIT_LOG_PATH, help="Audit log path"
    )
    parser.add_argument(
        "--secrets", default=BROKER_SECRETS_PATH, help="Secrets env path"
    )
    parser.add_argument("--degraded", action="store_true", help="Run in degraded mode")
    args = parser.parse_args()

    broker = RootBroker(
        socket_path=args.socket,
        allowlist_path=args.allowlist,
        audit_log_path=args.audit_log,
        secrets_path=args.secrets,
        token=args.token,
        degraded_mode=args.degraded,
    )
    if broker._expected_token is None:
        print("FATAL: no ROOT_BROKER_TOKEN configured", file=sys.stderr)
        return 1

    broker.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
