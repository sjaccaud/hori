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

        # Default allowlist in this test is restricted to one service.
        allowlist = {
            "service_status": {
                "service": ["aios_core"],
                "timeout": {"min": 5, "max": 60, "default": 30},
                "_required": ["service"],
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
