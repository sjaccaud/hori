#!@@REPO_ROOT@@/venv/bin/python3
"""Narrow, sandboxed Docker status wrapper for the system-root-broker.

Reads a JSON request from stdin, validates the query type, applies
Landlock/seccomp, and execs only the allowlisted `docker` subcommand.

Installed to /usr/local/sbin/hori-docker by
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
os.environ["HOME"] = "/tmp/hori-root-broker"

from services.broker.sandbox import apply_broker_sandbox  # noqa: E402

ALLOWED_QUERIES = frozenset({"ps", "system_df", "info"})


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


def _docker_binary() -> str:
    for candidate in ("/usr/bin/docker", "/bin/docker"):
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("docker binary not found")


def _docker_command(query: str) -> list[str]:
    binary = _docker_binary()
    if query == "ps":
        return [binary, "ps", "--format", "json"]
    if query == "system_df":
        return [binary, "system", "df", "--format", "json"]
    if query == "info":
        return [binary, "info", "--format", "{{json .}}"]
    raise ValueError(f"unknown query: {query}")


def _validate(data: dict[str, Any]) -> str:
    operation = data.get("operation")
    if operation != "docker_status":
        _fail("denied", f"operation '{operation}' is not allowed")

    query = data.get("query")
    if query not in ALLOWED_QUERIES:
        _fail("denied", f"docker query '{query}' is not in allowlist")

    return str(query)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _fail("error", f"Invalid JSON on stdin: {exc}")

    query = _validate(data)

    sandbox = apply_broker_sandbox("docker_status")
    sandbox_profile = sandbox["sandbox_profile"]
    if sandbox_profile == "denied":
        reason = (
            sandbox["seccomp"].get("error")
            or sandbox["landlock"].get("error")
            or "sandbox could not be applied"
        )
        _fail("denied", f"sandbox application failed: {reason}", "denied")

    command = _docker_command(query)
    try:
        proc = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError:
        _fail("error", f"command not found: {command[0]}", sandbox_profile)

    result: dict[str, Any] = {
        "status": "ok" if proc.returncode == 0 else "error",
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "sandbox_profile": sandbox_profile,
    }
    if proc.returncode != 0:
        result["error"] = proc.stderr or "docker command returned non-zero"
    print(json.dumps(result))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
