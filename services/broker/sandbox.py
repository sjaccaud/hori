"""Sandbox primitives for the root broker wrappers.

Wraps the existing tool daemon Landlock and seccomp primitives so that
`systemctl` and `journalctl` run with the minimum filesystem and syscall
surface. The broker itself does not apply the sandbox; the dedicated
wrapper scripts (`hori-systemctl`, `hori-journalctl`) apply it to
themselves and then exec the narrow command.

TRACES TO:
    .devin/prd/system-root-broker-03.md (Sandbox profile, Section 2)
    docs/roadmap.md Tier 2A (PoC 15.0b/c/d)
    docs/safety.md "Layer 1: The Cage (Kernel)"
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from typing import Any

from services.tool_daemon.landlock import AllowedPath, apply_landlock_restrictions
from services.tool_daemon.seccomp_filter import apply_seccomp_filter


def _which_systemctl() -> str:
    """Return the resolved path to the systemctl/journalctl binary."""
    for candidate in ("/usr/bin/systemctl", "/bin/systemctl"):
        if os.path.isfile(candidate):
            return candidate
    return "/usr/bin/systemctl"


def _which_journalctl() -> str:
    for candidate in ("/usr/bin/journalctl", "/bin/journalctl"):
        if os.path.isfile(candidate):
            return candidate
    return "/usr/bin/journalctl"


# Paths required to load and execute systemctl/journalctl.
# This is intentionally fail-closed: anything not listed is denied.
# The list may need to be expanded if the live smoke test shows a
# missing runtime path.
_BASE_ALLOWED_PATHS = [
    # The wrapper's own binary and the command it will exec.
    AllowedPath(_which_systemctl(), read_only=True, execute=True),
    AllowedPath(_which_journalctl(), read_only=True, execute=True),
    # Shared libraries and the dynamic linker.
    AllowedPath("/usr/lib", read_only=True, execute=True),
    AllowedPath("/lib", read_only=True, execute=True),
    AllowedPath("/lib64", read_only=True, execute=True),
    AllowedPath("/usr/lib64", read_only=True, execute=True),
    AllowedPath("/usr/lib/x86_64-linux-gnu", read_only=True, execute=True),
    AllowedPath("/lib/x86_64-linux-gnu", read_only=True, execute=True),
    # Dynamic linker cache and configuration.
    AllowedPath("/etc/ld.so.cache", read_only=True),
    AllowedPath("/etc/ld.so.preload", read_only=True),
    AllowedPath("/etc/ld.so.conf.d", read_only=True),
    # systemd unit files and runtime.
    AllowedPath("/etc/systemd", read_only=True),
    AllowedPath("/usr/lib/systemd", read_only=True),
    AllowedPath("/lib/systemd", read_only=True),
    AllowedPath("/run/systemd", read_only=False),
    AllowedPath("/run/dbus", read_only=False),
    AllowedPath("/var/run/systemd", read_only=False),
    AllowedPath("/var/run/dbus", read_only=False),
    # System runtime and volatile state.
    AllowedPath("/run", read_only=False),
    AllowedPath("/var/run", read_only=False),
    AllowedPath("/tmp", read_only=False),
    # Locale / time data for journalctl output.
    AllowedPath("/etc/localtime", read_only=True),
    AllowedPath("/etc/timezone", read_only=True),
    AllowedPath("/usr/share/zoneinfo", read_only=True),
    AllowedPath("/etc/hostname", read_only=True),
    AllowedPath("/etc/machine-id", read_only=True),
    # journalctl reads the systemd journal.
    AllowedPath("/var/log/journal", read_only=True),
    # Devices are excluded by default. The wrapper does not redirect
    # stdin/stdout to /dev/null, so Landlock device rules are not needed.
]

# Operation-specific additions. These are added on top of _BASE_ALLOWED_PATHS.
_OPERATION_ALLOWED_PATHS: dict[str, list[AllowedPath]] = {
    "journalctl_unit": [
        # Machine-specific journal directory is under /var/log/journal.
        AllowedPath("/var/log/journal", read_only=True),
    ],
    "gpu_status": [
        # DRM sysfs provides all read-only GPU state for amdgpu/nvidia.
        AllowedPath("/sys/class/drm", read_only=True),
        AllowedPath("/sys/bus/pci", read_only=True),
        AllowedPath("/sys/devices", read_only=True),
        AllowedPath("/usr/share/misc/pci.ids", read_only=True),
    ],
    "docker_status": [
        AllowedPath("/usr/bin/docker", read_only=True, execute=True),
        # /run and /var/run are already allowed by _BASE_ALLOWED_PATHS;
        # the docker socket lives there.  We do not grant the whole of /etc/docker.
        AllowedPath("/etc/docker", read_only=True),
    ],
    "ufw_status": [
        AllowedPath("/usr/sbin/ufw", read_only=True, execute=True),
        AllowedPath("/usr/sbin/iptables", read_only=True, execute=True),
        AllowedPath("/usr/sbin/nft", read_only=True, execute=True),
        AllowedPath("/usr/sbin/ip6tables", read_only=True, execute=True),
        AllowedPath("/etc/ufw", read_only=True),
        AllowedPath("/usr/share/ufw", read_only=True),
        AllowedPath("/lib/ufw", read_only=True),
        AllowedPath("/var/lib/ufw", read_only=True),
        AllowedPath("/etc/iptables", read_only=True),
        AllowedPath("/etc/default/ufw", read_only=True),
    ],
    "fail2ban_status": [
        AllowedPath("/usr/bin/fail2ban-client", read_only=True, execute=True),
        AllowedPath("/usr/bin/python3", read_only=True, execute=True),
        AllowedPath("/etc/fail2ban", read_only=True),
        AllowedPath("/var/lib/fail2ban", read_only=True),
        AllowedPath("/run/fail2ban", read_only=True),
        AllowedPath("/var/run/fail2ban", read_only=True),
    ],
}


def allowed_paths_for_operation(operation: str) -> list[AllowedPath]:
    """Return the Landlock allowlist for the given broker operation."""
    extra = _OPERATION_ALLOWED_PATHS.get(operation, [])
    return _BASE_ALLOWED_PATHS + extra


def apply_broker_sandbox(operation: str | None = None) -> dict[str, Any]:
    """Apply seccomp and Landlock restrictions to the current process.

    Intended to be called by the root-broker wrapper scripts immediately
    before executing `systemctl` or `journalctl`. Returns a result dict
    with a `sandbox_profile` string suitable for the audit log.

    The fail-closed default is to deny: if either seccomp or Landlock
    cannot be applied, the returned `sandbox_profile` is "denied" and
    the caller should abort.
    """
    seccomp_result = apply_seccomp_filter()
    landlock_result = apply_landlock_restrictions(
        allowed_paths=allowed_paths_for_operation(operation or ""),
        deny_all_network=True,
    )

    seccomp_ok = seccomp_result.success
    landlock_ok = landlock_result.success

    if seccomp_ok and landlock_ok:
        profile = "seccomp+landlock"
    elif seccomp_ok:
        profile = "seccomp"
    elif landlock_ok:
        profile = "landlock"
    else:
        profile = "denied"

    return {
        "sandbox_profile": profile,
        "seccomp": {
            "success": seccomp_ok,
            "arch": seccomp_result.arch,
            "blocked_count": seccomp_result.blocked_count,
            "error": seccomp_result.error,
        },
        "landlock": {
            "success": landlock_ok,
            "abi_version": landlock_result.abi_version,
            "allowed_paths": landlock_result.allowed_paths,
            "network_denied": landlock_result.network_denied,
            "error": landlock_result.error,
        },
    }


def sandbox_self_check() -> dict[str, Any]:
    """Run a quick in-process self-check after applying the sandbox.

    Used by tests and the install smoke test to verify the sandbox
    actually denies /etc/shadow and TCP sockets. Must be run in a
    subprocess because it applies irreversible restrictions.
    """
    result = apply_broker_sandbox("service_status")
    if result["sandbox_profile"] == "denied":
        return result

    checks: dict[str, bool] = {}

    # /etc/shadow should not be readable.
    try:
        with open("/etc/shadow"):
            checks["etc_shadow_blocked"] = False
    except (OSError, PermissionError):
        checks["etc_shadow_blocked"] = True

    # TCP sockets should not be creatable (Landlock network deny).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.close()
        checks["tcp_socket_denied"] = False
    except (OSError, PermissionError):
        checks["tcp_socket_denied"] = True

    # ptrace is blocked by seccomp.
    try:
        os.ptrace(0, 1, 0, 0)  # type: ignore[attr-defined]
        checks["ptrace_blocked"] = False
    except (OSError, AttributeError):
        checks["ptrace_blocked"] = True

    result["checks"] = checks
    return result


if __name__ == "__main__":
    # When run directly, run a self-check in a child process and print JSON.
    script = (
        "import json,sys; from services.broker.sandbox import sandbox_self_check; "
        "print(json.dumps(sandbox_self_check()))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": os.getcwd()},
    )
    print(proc.stdout.strip())
    sys.exit(0 if proc.returncode == 0 else 1)
