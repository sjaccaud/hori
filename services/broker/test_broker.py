"""Unit tests for the system root broker."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

from .allowlist import Allowlist
from .audit import BrokerAuditEntry
from .broker import RootBroker
from .client import RootBrokerClient


@pytest.fixture
def runtime_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def broker(runtime_dir: Path):
    """A root broker running in a temporary directory."""
    socket_path = runtime_dir / "broker.sock"
    allowlist_path = runtime_dir / "allowlist.json"
    audit_log_path = runtime_dir / "audit.jsonl"
    secrets_path = runtime_dir / "secrets.env"
    token = "test-token-" + uuid.uuid4().hex

    secrets_path.write_text(f'ROOT_BROKER_TOKEN="{token}"\n')

    b = RootBroker(
        socket_path=socket_path,
        allowlist_path=allowlist_path,
        audit_log_path=audit_log_path,
        secrets_path=secrets_path,
    )
    b.allowlist = Allowlist(allowlist_path, fallback=Allowlist.DEFAULT)

    thread = threading.Thread(target=b.start, daemon=True)
    thread.start()
    time.sleep(0.1)

    yield b, token

    b.shutdown()
    thread.join(timeout=1)


@pytest.fixture
def client(broker, runtime_dir: Path):
    b, token = broker
    return RootBrokerClient(socket_path=b.socket_path, token=token)


def test_authentication_failure(broker, runtime_dir: Path):
    b, _ = broker
    bad_client = RootBrokerClient(socket_path=b.socket_path, token="wrong-token")
    response = bad_client.call("service_status", {"service": "aios_core"})
    assert response["status"] == "denied"
    assert "authentication" in response["error"].lower()


def test_service_status_success(client: RootBrokerClient, broker, runtime_dir: Path):
    b, _ = broker
    response = client.call("service_status", {"service": "aios_core"})
    assert response["request_id"]
    assert "status" in response


def test_non_allowlisted_service_is_denied(client: RootBrokerClient):
    response = client.call("service_status", {"service": "nginx"})
    assert response["status"] == "denied"


def test_non_allowlisted_operation_is_denied(client: RootBrokerClient):
    response = client.call("arbitrary_shell", {"command": "id"})
    assert response["status"] == "denied"


def test_audit_log_is_written(client: RootBrokerClient, broker):
    b, _ = broker
    prior = len(b.audit_logger.read())
    client.call("service_status", {"service": "aios_core"})
    entries = b.audit_logger.read()
    assert len(entries) == prior + 1
    assert entries[-1]["operation"] == "service_status"
    assert entries[-1]["allowed"] is True


def test_audit_log_does_not_contain_raw_token(client: RootBrokerClient, broker):
    b, _ = broker
    client.call("service_status", {"service": "aios_core"})
    raw = b.audit_logger.log_path.read_text()
    assert b._expected_token not in raw


def test_allowlist_default_restricts_services():
    allowlist = Allowlist("/nonexistent/allowlist.json")
    assert allowlist.is_operation_allowed("service_restart")
    assert not allowlist.is_operation_allowed("package_install")
    ok, reason = allowlist.validate_params("service_restart", {"service": "aios_core"})
    assert ok
    ok, reason = allowlist.validate_params("service_restart", {"service": "nginx"})
    assert not ok


def test_service_stop_allowlisted_success(
    client: RootBrokerClient, broker, monkeypatch
):
    b, _ = broker

    def _fake_run(command, timeout, env=None):
        assert command[0:4] == ["sudo", "-n", "systemctl", "stop"]
        assert command[4] == "aios_core"
        return 0, "stopped", ""

    monkeypatch.setattr(b, "_run_command", _fake_run)
    response = client.call("service_stop", {"service": "aios_core"})
    assert response["request_id"]
    assert response["status"] == "ok"
    assert response["exit_code"] == 0
    assert response["stdout"] == "stopped"


def test_service_stop_non_allowlisted_denied(client: RootBrokerClient):
    response = client.call("service_stop", {"service": "nginx"})
    assert response["status"] == "denied"


def test_journalctl_unit_allowlisted_success(
    client: RootBrokerClient, broker, monkeypatch
):
    b, _ = broker

    def _fake_run(command, timeout, env=None, input_data=None):
        assert command == ["sudo", "-n", "/usr/local/sbin/hori-journalctl"]
        assert input_data is not None
        payload = json.loads(input_data)
        assert payload["unit"] == "aios_core"
        assert payload["lines"] == 50
        return 0, "log line", ""

    monkeypatch.setattr(b, "_run_command", _fake_run)
    response = client.call("journalctl_unit", {"unit": "aios_core", "lines": 50})
    assert response["request_id"]
    assert response["status"] == "ok"
    assert response["exit_code"] == 0
    assert response["stdout"] == "log line"


def test_journalctl_unit_non_allowlisted_denied(client: RootBrokerClient):
    response = client.call("journalctl_unit", {"unit": "nginx", "lines": 50})
    assert response["status"] == "denied"


def test_journalctl_unit_default_lines(client: RootBrokerClient, broker, monkeypatch):
    b, _ = broker

    def _fake_run(command, timeout, env=None, input_data=None):
        assert command == ["sudo", "-n", "/usr/local/sbin/hori-journalctl"]
        assert input_data is not None
        payload = json.loads(input_data)
        assert payload["unit"] == "aios_core"
        assert payload["lines"] == 50
        assert "since" not in payload
        return 0, "log line", ""

    monkeypatch.setattr(b, "_run_command", _fake_run)
    response = client.call("journalctl_unit", {"unit": "aios_core"})
    assert response["status"] == "ok"


def test_journalctl_unit_lines_bound(client: RootBrokerClient):
    response = client.call("journalctl_unit", {"unit": "aios_core", "lines": 9999})
    assert response["status"] == "denied"


def test_journalctl_unit_rejects_invalid_since(client: RootBrokerClient):
    response = client.call(
        "journalctl_unit", {"unit": "aios_core", "lines": 50, "since": "; rm -rf /"}
    )
    assert response["status"] == "denied"


def test_audit_entry_serialization():
    entry = BrokerAuditEntry(
        timestamp=time.time(),
        request_id="r-1",
        caller_uid=1000,
        caller_exe=None,
        operation="service_status",
        params={"service": "aios_core"},
        allowed=True,
        confirmed_by="secret-token-1234",
        exit_code=0,
        stdout="active",
        stderr="",
        error=None,
        sandbox_profile="none",
        revert_id=None,
    )
    line = entry.to_jsonl()
    assert "secret-token-1234" not in line
    assert "secr...1234" in line
