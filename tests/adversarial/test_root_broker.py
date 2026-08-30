"""Adversarial tests for the system root broker.

These tests attempt to bypass the broker's safety properties.
They must fail before the safety is implemented and pass after.
"""

from __future__ import annotations

import json
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
                "timeout": {"min": 5, "max": 60, "default": 30},
                "_required": ["service"],
            },
            "service_stop": {
                "service": ["aios_core"],
                "timeout": {"min": 5, "max": 300, "default": 60},
                "_required": ["service"],
            },
            "journalctl_unit": {
                "unit": ["aios_core"],
                "lines": {"min": 1, "max": 100, "default": 50},
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
