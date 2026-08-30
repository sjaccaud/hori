#!/usr/bin/env python3
"""Narrow journalctl wrapper for the system-root-broker.

Reads a JSON request from stdin, validates the unit/line count/since
window, and runs `journalctl` without shell. This script is the *only*
root path for broker-initiated journal reads. It exists because
sudo-rs does not allow wildcards in command-line arguments, so a
fixed-path wrapper is more portable and audit-friendly.
"""

import json
import re
import subprocess
import sys

ALLOWED_UNITS = frozenset({
    "aios_core",
    "aios-sherpa",
    "aios-tool-daemon",
    "llamacpp",
})

MAX_LINES = 1000
SINCE_PATTERN = re.compile(r"^[-+][0-9]+[smhd]$")


def _fail(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _fail(f"Invalid JSON on stdin: {exc}")

    unit = data.get("unit")
    if unit not in ALLOWED_UNITS:
        _fail(f"Unit '{unit}' is not in the broker allowlist")

    try:
        lines = int(data.get("lines", 50))
    except (TypeError, ValueError) as exc:
        _fail(f"Invalid line count: {exc}")
    if not 1 <= lines <= MAX_LINES:
        _fail(f"Line count {lines} is out of range 1-{MAX_LINES}")

    since = data.get("since")
    if since is not None and not SINCE_PATTERN.match(str(since)):
        _fail(f"Invalid since window: {since!r}")

    cmd = ["journalctl", "-u", unit, "--no-pager", "-n", str(lines)]
    if since:
        cmd += ["--since", str(since)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
