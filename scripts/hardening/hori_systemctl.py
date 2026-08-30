#!@@REPO_ROOT@@/venv/bin/python3
"""Narrow, sandboxed systemctl wrapper for the system-root-broker.

Reads a JSON request from stdin, validates the operation and service,
requires `confirmed_by` for destructive operations, applies
Landlock/seccomp, and execs only the allowlisted `systemctl` command.

Installed to /usr/local/sbin/hori-systemctl by
scripts/hardening/install_root_broker.sh.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = "@@REPO_ROOT@@"
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from services.broker.sandbox import apply_broker_sandbox

ALLOWED_SERVICES = frozenset({
    "aios_core",
    "aios-sherpa",
    "aios-tool-daemon",
    "llamacpp",
})

CONFIRMED_BY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
DESTRUCTIVE_OPERATIONS = frozenset({"service_restart", "service_stop"})


class WrapperError(Exception):
    """Validation or sandbox error."""

    def __init__(self, status: str, message: str, sandbox_profile: str = "denied"):
        self.status = status
        self.message = message
        self.sandbox_profile = sandbox_profile
        super().__init__(message)


def _fail(status: str, message: str, sandbox_profile: str = "denied") -> None:
    result = {
        "status": status,
        "exit_code": 1,
        "stdout": "",
        "stderr": message,
        "error": message,
        "sandbox_profile": sandbox_profile,
    }
    print(json.dumps(result))
    sys.exit(1)


def _systemctl_binary() -> str:
    for candidate in ("/usr/bin/systemctl", "/bin/systemctl"):
        if os.path.isfile(candidate):
            return candidate
    raise WrapperError("error", "systemctl binary not found")


def _validate(data: dict[str, Any]) -> tuple[str, str, str | None]:
    operation = data.get("operation")
    if operation not in ("service_status", "service_restart", "service_stop"):
        raise WrapperError("denied", f"operation '{operation}' is not allowed")

    service = data.get("service")
    if service not in ALLOWED_SERVICES:
        raise WrapperError("denied", f"service '{service}' is not in allowlist")

    confirmed_by: str | None = data.get("confirmed_by")
    if operation in DESTRUCTIVE_OPERATIONS:
        if not isinstance(confirmed_by, str) or not confirmed_by:
            raise WrapperError("denied", f"operation '{operation}' requires a 'confirmed_by' identity")
        if not CONFIRMED_BY_PATTERN.match(confirmed_by):
            raise WrapperError("denied", "confirmed_by does not match the required pattern")

    return operation, str(service), confirmed_by


def _systemctl_command(operation: str, service: str) -> list[str]:
    binary = _systemctl_binary()
    if operation == "service_status":
        return [binary, "is-active", service]
    if operation == "service_restart":
        return [binary, "restart", service]
    if operation == "service_stop":
        return [binary, "stop", service]
    raise WrapperError("denied", f"unknown operation: {operation}")


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _fail("error", f"Invalid JSON on stdin: {exc}")

    try:
        operation, service, _confirmed_by = _validate(data)
    except WrapperError as exc:
        _fail(exc.status, exc.message, exc.sandbox_profile)

    sandbox = apply_broker_sandbox(operation)
    sandbox_profile = sandbox["sandbox_profile"]
    if sandbox_profile == "denied":
        # Build a human-readable reason from the first failing layer.
        reason = sandbox["seccomp"].get("error") or sandbox["landlock"].get("error") or "sandbox could not be applied"
        _fail("denied", f"sandbox application failed: {reason}", "denied")

    command = _systemctl_command(operation, service)
    try:
        proc = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError:
        _fail("error", f"command not found: {command[0]}", sandbox_profile)

    result = {
        "status": "ok" if proc.returncode == 0 else "error",
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "sandbox_profile": sandbox_profile,
    }
    print(json.dumps(result))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
