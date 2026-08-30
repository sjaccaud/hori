#!@@REPO_ROOT@@/venv/bin/python3
"""Narrow, sandboxed journalctl wrapper for the system-root-broker.

Reads a JSON request from stdin, validates the unit/line count/since
window, applies Landlock/seccomp, and execs only the allowlisted
`journalctl` command.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

REPO_ROOT = "@@REPO_ROOT@@"
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# The wrapper runs under a systemd service that may have ProtectHome=read-only,
# so point hori.config at a writable scratch directory before any service module
# is imported and tries to create /root/.config.
os.environ["XDG_CONFIG_HOME"] = "/tmp/hori-root-broker"

from services.broker.sandbox import apply_broker_sandbox

ALLOWED_UNITS = frozenset({
    "aios_core",
    "aios-sherpa",
    "aios-tool-daemon",
    "llamacpp",
})

MAX_LINES = 1000
SINCE_PATTERN = re.compile(r"^[-+][0-9]+[smhd]$")
CONFIRMED_BY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


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


def _journalctl_binary() -> str:
    for candidate in ("/usr/bin/journalctl", "/bin/journalctl"):
        if os.path.isfile(candidate):
            return candidate
    raise WrapperError("error", "journalctl binary not found")


def _validate(data: dict[str, Any]) -> tuple[str, int, str | None, str | None]:
    unit = data.get("unit")
    if unit not in ALLOWED_UNITS:
        raise WrapperError("denied", f"Unit '{unit}' is not in the broker allowlist")

    try:
        lines = int(data.get("lines", 50))
    except (TypeError, ValueError) as exc:
        raise WrapperError("denied", f"Invalid line count: {exc}")
    if not 1 <= lines <= MAX_LINES:
        raise WrapperError("denied", f"Line count {lines} is out of range 1-{MAX_LINES}")

    since = data.get("since")
    if since is not None and not SINCE_PATTERN.match(str(since)):
        raise WrapperError("denied", f"Invalid since window: {since!r}")

    confirmed_by = data.get("confirmed_by")
    if confirmed_by is not None and not CONFIRMED_BY_PATTERN.match(str(confirmed_by)):
        raise WrapperError("denied", "confirmed_by does not match the required pattern")

    return str(unit), lines, str(since) if since is not None else None, str(confirmed_by) if confirmed_by is not None else None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _fail("error", f"Invalid JSON on stdin: {exc}")

    try:
        unit, lines, since, _confirmed_by = _validate(data)
    except WrapperError as exc:
        _fail(exc.status, exc.message, exc.sandbox_profile)

    sandbox = apply_broker_sandbox("journalctl_unit")
    sandbox_profile = sandbox["sandbox_profile"]
    if sandbox_profile == "denied":
        reason = sandbox["seccomp"].get("error") or sandbox["landlock"].get("error") or "sandbox could not be applied"
        _fail("denied", f"sandbox application failed: {reason}", "denied")

    binary = _journalctl_binary()
    cmd = [binary, "-u", unit, "--no-pager", "-n", str(lines)]
    if since:
        cmd += ["--since", since]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        _fail("error", f"command not found: {cmd[0]}", sandbox_profile)

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
