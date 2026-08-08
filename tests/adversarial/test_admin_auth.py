"""Adversarial test: admin panel authentication.

Tests that all /admin/api/* endpoints require a bearer token. The admin
panel (PoC 16.3) introduced a privileged HTTP surface on the Tailscale
mesh: service restart via sudoers, stress test execution, memory
consolidation triggers, and log viewing. Without authentication, any
device on the Tailnet can restart services or trigger consolidation —
the "trust boundary = Tailnet" model is broader than "trust boundary =
the admin token holder."

The fix: a bearer-token dependency (HORI_ADMIN_TOKEN from
/etc/hori/secrets.env). Fail-closed: if the token is not configured, all
admin API calls are rejected with 403.

Defends: PoC 16.3 (Admin Panel) privilege surface. Closes the
unauthenticated-service-restart hole found in the post-Tier-1A audit.

Traces to: docs/roadmap.md Gate Criteria (AIOS 1.6 -> 2.0) — an
unauthenticated privileged surface is an unmitigated safety incident.
"""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _mock_subprocess():
    """Return a context manager that mocks all subprocess calls used by
    admin endpoints (Popen, run, asyncio.create_subprocess_exec).

    Without this, POST endpoints would actually execute systemctl
    restarts, spawn stress tests, or run consolidation — destructive
    side effects that must not happen in a test."""
    popen_mock = MagicMock()
    popen_mock.return_value = MagicMock(pid=12345)
    run_mock = MagicMock(returncode=0)

    async_exec_mock = AsyncMock()
    # create_subprocess_exec returns a process with communicate()
    proc_mock = MagicMock()
    proc_mock.returncode = 0
    proc_mock.communicate = AsyncMock(return_value=(b"mocked output", b""))
    proc_mock.kill = MagicMock()
    async_exec_mock.return_value = proc_mock

    return (
        patch("subprocess.Popen", popen_mock),
        patch("subprocess.run", return_value=run_mock),
        patch("asyncio.create_subprocess_exec", async_exec_mock),
    )


@pytest.fixture
def client():
    """Client with subprocess mocked. AIOS_ADMIN_TOKEN is NOT set
    (fail-closed scenario)."""
    from services.aios_core.main import app

    old = os.environ.get("AIOS_ADMIN_TOKEN")
    os.environ.pop("AIOS_ADMIN_TOKEN", None)
    p1, p2, p3 = _mock_subprocess()
    p1.start(); p2.start(); p3.start()
    try:
        yield TestClient(app)
    finally:
        p1.stop(); p2.stop(); p3.stop()
        if old is not None:
            os.environ["AIOS_ADMIN_TOKEN"] = old


@pytest.fixture
def authed_client():
    """Client with a valid admin token set and subprocess mocked."""
    from services.aios_core.main import app

    old = os.environ.get("AIOS_ADMIN_TOKEN")
    os.environ["AIOS_ADMIN_TOKEN"] = "test-secret-token"
    p1, p2, p3 = _mock_subprocess()
    p1.start(); p2.start(); p3.start()
    try:
        yield TestClient(app)
    finally:
        p1.stop(); p2.stop(); p3.stop()
        if old is not None:
            os.environ["AIOS_ADMIN_TOKEN"] = old
        else:
            os.environ.pop("AIOS_ADMIN_TOKEN", None)


def _auth_headers(token="test-secret-token"):
    return {"Authorization": f"Bearer {token}"}


class TestAdminAuthFailClosed:
    """When AIOS_ADMIN_TOKEN is not set, all admin API calls must be rejected."""

    def test_no_token_service_restart_rejected(self, client):
        """Service restart without any token configured must 403 (fail-closed)."""
        resp = client.post("/admin/api/service/restart?service=aios-sherpa")
        assert resp.status_code == 403

    def test_no_token_stress_start_rejected(self, client):
        """Stress start without any token configured must 403."""
        resp = client.post("/admin/api/stress/start", json={})
        assert resp.status_code == 403

    def test_no_token_consolidate_rejected(self, client):
        """Consolidation trigger without any token configured must 403."""
        resp = client.post("/admin/api/consolidate")
        assert resp.status_code == 403

    def test_no_token_logs_rejected(self, client):
        """Log viewing without any token configured must 403."""
        resp = client.get("/admin/api/logs/aios-core")
        assert resp.status_code == 403

    def test_no_token_gate_status_rejected(self, client):
        """Gate status without any token configured must 403."""
        resp = client.get("/admin/api/gate")
        assert resp.status_code == 403

    def test_no_token_state_rejected(self, client):
        """System state without any token configured must 403."""
        resp = client.get("/admin/api/state")
        assert resp.status_code == 403


class TestAdminAuthUnauthenticated:
    """When a token IS configured, requests without/with-wrong token are rejected."""

    def test_unauthenticated_service_restart_rejected(self, authed_client):
        """POST service restart with no Authorization header must 401."""
        resp = authed_client.post("/admin/api/service/restart?service=aios-sherpa")
        assert resp.status_code == 401

    def test_wrong_token_service_restart_rejected(self, authed_client):
        """POST service restart with wrong token must 403."""
        resp = authed_client.post(
            "/admin/api/service/restart?service=aios-sherpa",
            headers=_auth_headers("wrong-token"),
        )
        assert resp.status_code == 403

    def test_unauthenticated_stress_start_rejected(self, authed_client):
        """POST stress start with no auth must 401."""
        resp = authed_client.post("/admin/api/stress/start", json={})
        assert resp.status_code == 401

    def test_unauthenticated_consolidate_rejected(self, authed_client):
        """POST consolidate with no auth must 401."""
        resp = authed_client.post("/admin/api/consolidate")
        assert resp.status_code == 401

    def test_unauthenticated_logs_rejected(self, authed_client):
        """GET logs with no auth must 401."""
        resp = authed_client.get("/admin/api/logs/aios-core")
        assert resp.status_code == 401

    def test_unauthenticated_gate_rejected(self, authed_client):
        """GET gate status with no auth must 401."""
        resp = authed_client.get("/admin/api/gate")
        assert resp.status_code == 401

    def test_unauthenticated_state_rejected(self, authed_client):
        """GET system state with no auth must 401."""
        resp = authed_client.get("/admin/api/state")
        assert resp.status_code == 401

    def test_unauthenticated_stress_status_rejected(self, authed_client):
        """GET stress status with no auth must 401."""
        resp = authed_client.get("/admin/api/stress/status")
        assert resp.status_code == 401


class TestAdminAuthValidToken:
    """A valid token must pass the auth gate (the request proceeds to logic)."""

    def test_valid_token_service_status_passes_auth(self, authed_client):
        """POST service status with correct token must not be 401/403."""
        resp = authed_client.post(
            "/admin/api/service/status?service=aios-sherpa",
            headers=_auth_headers(),
        )
        assert resp.status_code not in (401, 403)

    def test_valid_token_gate_passes_auth(self, authed_client):
        """GET gate status with correct token must not be 401/403."""
        resp = authed_client.get("/admin/api/gate", headers=_auth_headers())
        assert resp.status_code not in (401, 403)

    def test_valid_token_state_passes_auth(self, authed_client):
        """GET system state with correct token must not be 401/403."""
        resp = authed_client.get("/admin/api/state", headers=_auth_headers())
        assert resp.status_code not in (401, 403)


class TestAdminPageUnprotected:
    """The /admin HTML page itself stays open — it's static markup with no data.
    All data comes from authenticated API calls. This is the standard SPA pattern."""

    def test_admin_html_accessible_without_auth(self, client):
        """GET /admin without auth must return the HTML page (200), not 401."""
        resp = client.get("/admin")
        # 200 if the file exists, 404 if not — but NOT 401/403
        assert resp.status_code not in (401, 403)
