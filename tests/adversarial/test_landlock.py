"""Adversarial tests: Landlock filesystem and network restrictions (PoC 15.0b/c).

Tests that Landlock default-deny isolation blocks:
  - /proc/self/environ (credential theft via environment variables)
  - ~/.ssh, ~/.gnupg (private keys)
  - /etc/shadow, /etc/passwd (system files)
  - /var/log (system logs that leak architecture)
  - TCP network (lateral movement to local services)

And that explicitly allowed paths remain accessible:
  - ~/Projects (read-only, configurable via hori.yaml)
  - the workspace (read-write, default /tmp/hori-workspace)

These tests run in subprocesses because Landlock is irreversible — once
applied, the restrictions cannot be widened or removed for the process
or any of its children.

Defends: PoC 15.0b (Landlock file restrictions), PoC 15.0c (Landlock
network restrictions), red-team fix #7 (default-deny model).

Traces to: docs/roadmap.md Tier 2A, PoC 15.0b + 15.0c.
Traces to: docs/tool_safety.md "Layer 1: The Cage (Kernel)".
"""
import json
import os
import socket
import subprocess
import sys

import pytest

from services.tool_daemon.landlock import (
    AllowedPath,
    apply_default_restrictions,
    apply_landlock_restrictions,
    detect_landlock_abi_version,
    is_landlock_available,
)
from hori.config import WORKSPACE_PATH
from pathlib import Path
PROJECTS_DIR = str(Path.home() / "Projects")
AIOS_DIR = str(Path(__file__).resolve().parents[2])
HOME_DIR = str(Path.home())


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not is_landlock_available(),
    reason="Landlock ABI >= 3 not available on this kernel",
)


def _run_in_subprocess(code: str) -> subprocess.CompletedProcess:
    """Run Python code in a subprocess with the project on the path."""
    full_code = f"""
import sys
sys.path.insert(0, {repr(os.getcwd())})
{code}
"""
    return subprocess.run(
        [sys.executable, "-c", full_code],
        capture_output=True,
        text=True,
        cwd=os.getcwd(),
        timeout=10,
    )


def _run_landlock_test(body: str) -> dict:
    """Apply Landlock in a subprocess, run body, return parsed JSON result."""
    code = f"""
import os, sys, json, socket
from services.tool_daemon.landlock import apply_default_restrictions

result = apply_default_restrictions()
if not result.success:
    print(json.dumps({{"landlock_error": result.error}}))
    sys.exit(0)

{body}
"""
    proc = _run_in_subprocess(code)
    if proc.returncode != 0:
        return {"subprocess_error": proc.stderr.strip(), "returncode": proc.returncode}
    try:
        return json.loads(proc.stdout.strip().split("\n")[-1])
    except (json.JSONDecodeError, IndexError):
        return {"raw_stdout": proc.stdout, "raw_stderr": proc.stderr}


# ---------------------------------------------------------------------------
# ABI detection tests
# ---------------------------------------------------------------------------


class TestLandlockAvailability:
    """Landlock must be available and detectable."""

    def test_abi_detected(self):
        """The kernel must report Landlock ABI >= 3."""
        abi = detect_landlock_abi_version()
        assert abi >= 3, f"Landlock ABI {abi} < 3 (minimum required)"


# ---------------------------------------------------------------------------
# Filesystem restriction tests (PoC 15.0b)
# ---------------------------------------------------------------------------


class TestLandlockFilesystemDeny:
    """Landlock must block access to sensitive paths (default-deny)."""

    def test_proc_environ_blocked(self):
        """/proc/self/environ must be blocked — it contains env var tokens."""
        result = _run_landlock_test("""
try:
    with open("/proc/self/environ", "rb") as f:
        f.read(10)
    r = {"can_read_proc_environ": True}
except OSError as e:
    r = {"can_read_proc_environ": False, "errno": e.errno}
print(json.dumps(r))
""")
        assert not result.get("can_read_proc_environ", True), \
            "/proc/self/environ was readable — credential theft possible!"

    def test_ssh_blocked(self):
        """~/.ssh must be blocked — it contains private keys."""
        result = _run_landlock_test("""
import os
try:
    os.listdir(os.path.expanduser("~/.ssh"))
    r = {"can_list_ssh": True}
except OSError:
    r = {"can_list_ssh": False}
print(json.dumps(r))
""")
        assert not result.get("can_list_ssh", True), \
            "~/.ssh was listable — private key theft possible!"

    def test_gnupg_blocked(self):
        """~/.gnupg must be blocked — it contains GPG private keys."""
        result = _run_landlock_test("""
import os
try:
    os.listdir(os.path.expanduser("~/.gnupg"))
    r = {"can_list_gnupg": True}
except OSError:
    r = {"can_list_gnupg": False}
print(json.dumps(r))
""")
        assert not result.get("can_list_gnupg", True), \
            "~/.gnupg was listable — GPG key theft possible!"

    def test_etc_shadow_blocked(self):
        """/etc/shadow must be blocked — it contains password hashes."""
        result = _run_landlock_test("""
try:
    with open("/etc/shadow", "rb") as f:
        f.read(10)
    r = {"can_read_shadow": True}
except OSError:
    r = {"can_read_shadow": False}
print(json.dumps(r))
""")
        assert not result.get("can_read_shadow", True), \
            "/etc/shadow was readable — password hash theft possible!"

    def test_etc_hostname_blocked(self):
        """/etc/hostname must be blocked — default-deny means everything not allowed."""
        result = _run_landlock_test("""
try:
    with open("/etc/hostname", "rb") as f:
        f.read(10)
    r = {"can_read_etc_hostname": True}
except OSError:
    r = {"can_read_etc_hostname": False}
print(json.dumps(r))
""")
        assert not result.get("can_read_etc_hostname", True), \
            "/etc/hostname was readable — default-deny is not working!"

    def test_var_log_blocked(self):
        """/var/log must be blocked — logs leak system architecture."""
        result = _run_landlock_test("""
import os
try:
    os.listdir("/var/log")
    r = {"can_list_var_log": True}
except OSError:
    r = {"can_list_var_log": False}
print(json.dumps(r))
""")
        assert not result.get("can_list_var_log", True), \
            "/var/log was listable — system architecture leak!"

    def test_dev_blocked(self):
        """/dev must be blocked — device files can be used for attacks."""
        result = _run_landlock_test("""
import os
try:
    os.listdir("/dev")
    r = {"can_list_dev": True}
except OSError:
    r = {"can_list_dev": False}
print(json.dumps(r))
""")
        assert not result.get("can_list_dev", True), \
            "/dev was listable — device access possible!"

    def test_sys_blocked(self):
        """/sys must be blocked — kernel parameters can leak info."""
        result = _run_landlock_test("""
import os
try:
    os.listdir("/sys")
    r = {"can_list_sys": True}
except OSError:
    r = {"can_list_sys": False}
print(json.dumps(r))
""")
        assert not result.get("can_list_sys", True), \
            "/sys was listable — kernel parameter access possible!"

    def test_run_blocked(self):
        """/run must be blocked — contains runtime sockets and secrets."""
        result = _run_landlock_test("""
import os
try:
    os.listdir("/run")
    r = {"can_list_run": True}
except OSError:
    r = {"can_list_run": False}
print(json.dumps(r))
""")
        assert not result.get("can_list_run", True), \
            "/run was listable — runtime socket/secret access possible!"


class TestLandlockFilesystemAllow:
    """Landlock must allow access to explicitly granted paths."""

    def test_projects_readable(self):
        """~/Projects must be readable (allowed, read-only)."""
        result = _run_landlock_test(f"""
import os
try:
    os.listdir({PROJECTS_DIR!r})
    r = {{"can_read_projects": True}}
except OSError as e:
    r = {{"can_read_projects": False, "errno": e.errno}}
print(json.dumps(r))
""")
        assert result.get("can_read_projects", False), \
            f"{PROJECTS_DIR} was not readable — allowed path blocked!"

    def test_projects_write_blocked(self):
        """~/Projects must NOT be writable (read-only allow)."""
        result = _run_landlock_test(f"""
import os
try:
    with open({(AIOS_DIR + '/.landlock_write_test')!r}, "w") as f:
        f.write("test")
    os.unlink({(AIOS_DIR + '/.landlock_write_test')!r})
    r = {{"can_write_projects": True}}
except OSError:
    r = {{"can_write_projects": False}}
print(json.dumps(r))
""")
        assert not result.get("can_write_projects", True), \
            f"{PROJECTS_DIR} was writable — read-only allow failed!"

    def test_models_readable(self):
        """The first configured read path must be readable (allowed, read-only)."""
        from hori.config import ALLOWED_READ_PATHS
        if not ALLOWED_READ_PATHS:
            pytest.skip("No allowed_read_paths configured")
        test_path = ALLOWED_READ_PATHS[0]
        result = _run_landlock_test(f"""
import os
try:
    os.listdir({test_path!r})
    r = {{"can_read_models": True}}
except OSError as e:
    r = {{"can_read_models": False, "errno": e.errno}}
print(json.dumps(r))
""")
        assert result.get("can_read_models", False), \
            f"{test_path} was not readable — allowed path blocked!"


class TestLandlockWorkspace:
    """The RW workspace must support file operations but not directory creation."""

    @pytest.fixture(autouse=True)
    def _ensure_workspace(self):
        os.makedirs(WORKSPACE_PATH, exist_ok=True)
        # Check if we can write to the workspace. If the install script
        # set aios-worker-only permissions, skip these tests.
        if not os.access(WORKSPACE_PATH, os.W_OK):
            pytest.skip(
                f"Workspace not writable by current user "
                f"(install script set aios-worker-only permissions). "
                f"Run: sudo chmod 1777 {WORKSPACE_PATH}"
            )
        yield

    def test_workspace_mkdir_blocked(self):
        """The RW workspace must NOT allow directory creation (minimal surface)."""
        result = _run_landlock_test(f"""
import os
try:
    os.mkdir({WORKSPACE_PATH!r} + "/adv_subdir")
    os.rmdir({WORKSPACE_PATH!r} + "/adv_subdir")
    r = {{"can_mkdir_workspace": True}}
except OSError:
    r = {{"can_mkdir_workspace": False}}
print(json.dumps(r))
""")
        assert not result.get("can_mkdir_workspace", True), \
            "RW workspace allowed mkdir — surface area not minimized!"


# ---------------------------------------------------------------------------
# Network restriction tests (PoC 15.0c)
# ---------------------------------------------------------------------------


class TestLandlockNetwork:
    """Landlock must block all TCP network access."""

    def test_localhost_blocked(self):
        """TCP connect to 127.0.0.1 must be blocked (lateral movement)."""
        result = _run_landlock_test("""
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(("127.0.0.1", 8080))
    s.close()
    r = {"can_connect_localhost": True}
except OSError as e:
    r = {"can_connect_localhost": False, "errno": e.errno}
print(json.dumps(r))
""")
        assert not result.get("can_connect_localhost", True), \
            "TCP connect to localhost succeeded — lateral movement possible!"

    def test_tailscale_blocked(self):
        """TCP connect to a non-localhost network address must be blocked."""
        result = _run_landlock_test("""
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(("10.0.0.1", 5680))
    s.close()
    r = {"can_connect_tailscale": True}
except OSError as e:
    r = {"can_connect_tailscale": False, "errno": e.errno}
print(json.dumps(r))
""")
        assert not result.get("can_connect_tailscale", True), \
            "TCP connect to Tailscale succeeded — lateral movement possible!"


# ---------------------------------------------------------------------------
# Irreversibility test
# ---------------------------------------------------------------------------


class TestLandlockIrreversibility:
    """Landlock restrictions must be irreversible — can't be widened."""

    def test_cannot_widen_after_apply(self):
        """Applying a second Landlock with more access must not widen."""
        result = _run_landlock_test("""
import os, json
from services.tool_daemon.landlock import (
    apply_landlock_restrictions, AllowedPath
)



# Try to add /etc as an allowed path AFTER the default restrictions
result2 = apply_landlock_restrictions(
    allowed_paths=[AllowedPath("/etc", read_only=True)],
    deny_all_network=False,
)

# Check if /etc is now readable
try:
    with open("/etc/hostname", "rb") as f:
        f.read(10)
    can_read_etc = True
except OSError:
    can_read_etc = False

r = {
    "second_apply_success": result2.success,
    "can_read_etc_after_widen": can_read_etc,
}
print(json.dumps(r))
""")
        # The second apply might succeed (adding a narrower restriction)
        # but it must NOT widen access to /etc
        assert not result.get("can_read_etc_after_widen", True), \
            "Landlock was widened — /etc became readable after second apply!"
