"""Adversarial tests for the system root broker.

These tests attempt to bypass the broker's safety properties.
They must fail before the safety is implemented and pass after.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from services.broker.broker import RootBroker
from services.broker.client import RootBrokerClient


@pytest.fixture
def broker():
    with tempfile.TemporaryDirectory() as tmp:
        runtime = Path(tmp)
        socket_path = runtime / "broker.sock"
        allowlist_path = runtime / "allowlist.json"
        audit_log_path = runtime / "audit.jsonl"
        secrets_path = runtime / "secrets.env"
        token = "adversarial-token"

        # Default allowlist in this test is restricted to one service/unit.
        allowlist = {
            "service_status": {
                "service": ["aios_core"],
                "confirmed_by": {"pattern": r"^[A-Za-z0-9_.:-]{1,128}$"},
                "timeout": {"min": 5, "max": 60, "default": 30},
                "_required": ["service"],
            },
            "service_restart": {
                "service": ["aios_core"],
                "confirmed_by": {"pattern": r"^[A-Za-z0-9_.:-]{1,128}$"},
                "timeout": {"min": 5, "max": 300, "default": 60},
                "_required": ["service"],
            },
            "service_stop": {
                "service": ["aios_core"],
                "confirmed_by": {"pattern": r"^[A-Za-z0-9_.:-]{1,128}$"},
                "timeout": {"min": 5, "max": 300, "default": 60},
                "_required": ["service"],
            },
            "journalctl_unit": {
                "unit": ["aios_core"],
                "lines": {"min": 1, "max": 100, "default": 50},
                "confirmed_by": {"pattern": r"^[A-Za-z0-9_.:-]{1,128}$"},
                "_required": ["unit"],
            },
        }
        allowlist_path.write_text(json.dumps(allowlist))
        secrets_path.write_text(f"ROOT_BROKER_TOKEN={token}\n")

        b = RootBroker(
            socket_path=socket_path,
            allowlist_path=allowlist_path,
            audit_log_path=audit_log_path,
            secrets_path=secrets_path,
        )

        thread = threading.Thread(target=b.start, daemon=True)
        thread.start()
        time.sleep(0.1)

        yield b
        b.shutdown()
        thread.join(timeout=1)


@pytest.fixture
def client(broker: RootBroker):
    return RootBrokerClient(socket_path=broker.socket_path, token="adversarial-token")


def test_broker_rejects_wrong_token(client: RootBrokerClient, broker: RootBroker):
    bad = RootBrokerClient(socket_path=broker.socket_path, token="wrong")
    response = bad.call("service_status", {"service": "aios_core"})
    assert response["status"] == "denied"
    assert "authentication" in response["error"].lower()


def test_broker_rejects_non_allowlisted_service(
    client: RootBrokerClient, broker: RootBroker
):
    response = client.call("service_status", {"service": "nginx"})
    assert response["status"] == "denied"


def test_broker_rejects_shell_injection_via_service_name(
    client: RootBrokerClient, broker: RootBroker
):
    response = client.call("service_status", {"service": "aios_core; rm -rf /"})
    assert response["status"] == "denied"


def test_broker_rejects_arbitrary_operation(
    client: RootBrokerClient, broker: RootBroker
):
    response = client.call("exec", {"command": ["bash", "-c", "id"]})  # type: ignore[arg-type]
    assert response["status"] == "denied"


def test_broker_rejects_timeout_escape(client: RootBrokerClient, broker: RootBroker):
    response = client.call(
        "service_status", {"service": "aios_core", "timeout": 999999}
    )
    assert response["status"] == "denied"


def test_broker_audit_log_never_leaks_token(
    client: RootBrokerClient, broker: RootBroker
):
    client.call("service_status", {"service": "aios_core"})
    client.call("service_status", {"service": "nginx"})
    raw = broker.audit_logger.log_path.read_text()
    assert "adversarial-token" not in raw


def test_broker_rejects_shell_injection_via_service_stop(
    client: RootBrokerClient, broker: RootBroker
):
    """A semicolon in the service name must not reach systemctl stop.

    Defends: root broker allowlist validation.
    """
    response = client.call("service_stop", {"service": "aios_core; rm -rf /"})
    assert response["status"] == "denied"


def test_broker_rejects_shell_injection_via_journalctl_unit(
    client: RootBrokerClient, broker: RootBroker
):
    """A semicolon in the unit name must not reach journalctl.

    Defends: root broker allowlist value validation.
    """
    response = client.call(
        "journalctl_unit", {"unit": "aios_core; cat /etc/shadow", "lines": 50}
    )
    assert response["status"] == "denied"


def test_broker_journalctl_rejects_arbitrary_arguments(
    client: RootBrokerClient, broker: RootBroker
):
    """`--output=json`, `--follow`, or other journalctl options are not allowlisted.

    Defends: root broker parameter allowlist.
    """
    response = client.call(
        "journalctl_unit",
        {"unit": "aios_core", "lines": 50, "output": "json"},
    )
    assert response["status"] == "denied"


def test_broker_journalctl_rejects_disallowed_unit(
    client: RootBrokerClient, broker: RootBroker
):
    """`journalctl` must only be run for the four broker-allowlisted units.

    Defends: root broker unit allowlist.
    """
    response = client.call("journalctl_unit", {"unit": "systemd-journald", "lines": 50})
    assert response["status"] == "denied"


def test_broker_rejects_destructive_op_without_confirmation(
    client: RootBrokerClient, broker: RootBroker
):
    """Destructive operations must require an explicit confirmed_by identity.

    Defends: root broker confirmation gate (Tier 2).
    Traces to: .devin/prd/system-root-broker-03.md and .devin/prd/root-broker.md.
    """
    response = client.call("service_stop", {"service": "aios_core"})
    assert response["status"] == "denied"
    assert "confirmed_by" in response.get("error", "").lower()


def test_broker_confirmation_identity_cannot_be_token(
    client: RootBrokerClient, broker: RootBroker
):
    """The broker token itself must not be accepted as a confirmation identity.

    Defends: confirmation-gate bypass via token reuse.
    Traces to: .devin/prd/system-root-broker-03.md.
    """
    response = client.call(
        "service_stop",
        {"service": "aios_core", "confirmed_by": "adversarial-token"},
    )
    assert response["status"] == "denied"
    assert "token" in response.get("error", "").lower()


def test_broker_confirmation_identity_rejects_injection(
    client: RootBrokerClient, broker: RootBroker
):
    """confirmed_by containing shell meta-characters is denied before it
    reaches any command.

    Defends: shell injection through the confirmation identity.
    Traces to: .devin/prd/system-root-broker-03.md.
    """
    response = client.call(
        "service_stop",
        {"service": "aios_core", "confirmed_by": "operator; rm -rf /"},
    )
    assert response["status"] == "denied"
    assert "pattern" in response.get("error", "").lower()


def _run_in_sandboxed_child(code: str) -> subprocess.CompletedProcess:
    """Run `code` in a new Python process after applying the broker sandbox."""
    script = f"""
import sys, json
sys.path.insert(0, ".")
from services.broker.sandbox import apply_broker_sandbox

result = apply_broker_sandbox("service_status")
if result["sandbox_profile"] == "denied":
    print(json.dumps(result))
    sys.exit(2)

{code}
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )


def test_broker_sandbox_blocks_forbidden_path():
    """A sandboxed child cannot open /etc/shadow, /etc/group, or ~/.ssh/authorized_keys.

    Defends: filesystem confinement (Landlock).
    Traces to: .devin/prd/system-root-broker-03.md.
    """
    code = """
from pathlib import Path
import errno

# /etc/group is normally world-readable, so it proves Landlock is the one
# blocking. /etc/shadow is also included as the explicit contract target.
forbidden = ["/etc/shadow", "/etc/group", str(Path.home() / ".ssh" / "authorized_keys")]
blocked = 0
for path in forbidden:
    try:
        with open(path) as f:
            f.read(1)
    except PermissionError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            blocked += 1
if blocked == len(forbidden):
    sys.exit(0)
sys.exit(1)
"""
    proc = _run_in_sandboxed_child(code)
    assert proc.returncode == 0, f"Forbidden path was readable: {proc.stderr}"


def test_broker_sandbox_denies_network_connect():
    """A sandboxed child cannot create a TCP socket (Landlock network deny).

    Defends: network confinement.
    Traces to: .devin/prd/system-root-broker-03.md.
    """
    code = """
import socket, errno
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.close()
    sys.exit(1)
except OSError as exc:
    if exc.errno in (errno.EACCES, errno.EPERM):
        sys.exit(0)
    sys.exit(1)
"""
    proc = _run_in_sandboxed_child(code)
    assert proc.returncode == 0, f"TCP socket was not denied: {proc.stderr}"


def test_broker_sandbox_blocks_ptrace():
    """A sandboxed child cannot invoke ptrace (seccomp blocklist).

    Defends: seccomp-bpf syscall filtering.
    Traces to: .devin/prd/system-root-broker-03.md.
    """
    code = """
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
# ptrace syscall on x86_64; if not blocked it returns -1.
libc.syscall(101, 0, 0, 0, 0)
# If we get here, seccomp did not kill us.
sys.exit(1)
"""
    proc = _run_in_sandboxed_child(code)
    # SECCOMP_RET_KILL_PROCESS terminates the child with SIGSYS.
    assert proc.returncode == -signal.SIGSYS, (
        f"ptrace was not blocked: rc={proc.returncode} stderr={proc.stderr}"
    )


def test_broker_audit_records_confirmation_and_sandbox_profile(
    client: RootBrokerClient, broker: RootBroker, monkeypatch
):
    """A confirmed destructive op records confirmed_by and sandbox_profile.

    Defends: audit completeness (Tier 2).
    Traces to: .devin/prd/system-root-broker-03.md.
    """

    def _fake_run(command, timeout, env=None, input_data=None):
        return (
            0,
            json.dumps(
                {
                    "status": "ok",
                    "exit_code": 0,
                    "stdout": "stopped",
                    "stderr": "",
                    "sandbox_profile": "seccomp+landlock",
                }
            ),
            "",
        )

    monkeypatch.setattr(broker, "_run_command", _fake_run)
    identity = "operator-2026-08-30"
    response = client.call(
        "service_stop",
        {"service": "aios_core", "confirmed_by": identity},
    )
    assert response["status"] == "ok"
    entries = broker.audit_logger.read()
    assert entries[-1]["confirmed_by"] == identity
    assert entries[-1]["sandbox_profile"] == "seccomp+landlock"
