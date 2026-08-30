#!@@REPO_ROOT@@/venv/bin/python3
"""Narrow, sandboxed UFW status wrapper for the system-root-broker.

Reads a JSON request from stdin, validates the operation, applies
Landlock/seccomp, and execs only `ufw status`.

Installed to /usr/local/sbin/hori-ufw by
scripts/hardening/install_root_broker.sh.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

REPO_ROOT = "@@REPO_ROOT@@"
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

os.environ["XDG_CONFIG_HOME"] = "/tmp/hori-root-broker"

from services.broker.sandbox import apply_broker_sandbox  # noqa: E402


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


def _ufw_binary() -> str:
    for candidate in ("/usr/sbin/ufw", "/bin/ufw"):
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("ufw binary not found")


def _validate(data: dict[str, Any]) -> None:
    operation = data.get("operation")
    if operation != "ufw_status":
        _fail("denied", f"operation '{operation}' is not allowed")


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _fail("error", f"Invalid JSON on stdin: {exc}")

    _validate(data)

    sandbox = apply_broker_sandbox("ufw_status")
    sandbox_profile = sandbox["sandbox_profile"]
    if sandbox_profile == "denied":
        reason = (
            sandbox["seccomp"].get("error")
            or sandbox["landlock"].get("error")
            or "sandbox could not be applied"
        )
        _fail("denied", f"sandbox application failed: {reason}", "denied")

    try:
        proc = subprocess.run([_ufw_binary(), "status"], capture_output=True, text=True)
    except FileNotFoundError:
        _fail("error", "ufw binary not found", sandbox_profile)

    result: dict[str, Any] = {
        "status": "ok" if proc.returncode == 0 else "error",
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "sandbox_profile": sandbox_profile,
    }
    if proc.returncode != 0:
        result["error"] = proc.stderr or "ufw status returned non-zero"
    print(json.dumps(result))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
