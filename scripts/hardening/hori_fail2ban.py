#!@@REPO_ROOT@@/venv/bin/python3
"""Narrow, sandboxed fail2ban-client status wrapper for the system-root-broker.

Reads a JSON request from stdin, validates the operation and optional jail
name, applies Landlock/seccomp, and execs only `fail2ban-client status [jail]`.

Installed to /usr/local/sbin/hori-fail2ban by
scripts/hardening/install_root_broker.sh.
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

os.environ["XDG_CONFIG_HOME"] = "/tmp/hori-root-broker"

from services.broker.sandbox import apply_broker_sandbox  # noqa: E402

JAIL_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


def _fail2ban_binary() -> str:
    for candidate in ("/usr/bin/fail2ban-client", "/bin/fail2ban-client"):
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("fail2ban-client binary not found")


def _validate(data: dict[str, Any]) -> str | None:
    operation = data.get("operation")
    if operation != "fail2ban_status":
        _fail("denied", f"operation '{operation}' is not allowed")

    jail = data.get("jail")
    if jail is not None:
        if not isinstance(jail, str) or not JAIL_PATTERN.match(jail):
            _fail("denied", f"invalid jail name: {jail!r}")
        return jail
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _fail("error", f"Invalid JSON on stdin: {exc}")

    jail = _validate(data)

    sandbox = apply_broker_sandbox("fail2ban_status")
    sandbox_profile = sandbox["sandbox_profile"]
    if sandbox_profile == "denied":
        reason = (
            sandbox["seccomp"].get("error")
            or sandbox["landlock"].get("error")
            or "sandbox could not be applied"
        )
        _fail("denied", f"sandbox application failed: {reason}", "denied")

    command = [_fail2ban_binary(), "status"]
    if jail:
        command.append(jail)

    try:
        proc = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError:
        _fail("error", "fail2ban-client binary not found", sandbox_profile)

    result: dict[str, Any] = {
        "status": "ok" if proc.returncode == 0 else "error",
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "sandbox_profile": sandbox_profile,
    }
    if proc.returncode != 0:
        result["error"] = proc.stderr or "fail2ban-client returned non-zero"
    print(json.dumps(result))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
