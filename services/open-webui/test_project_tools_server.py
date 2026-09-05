"""Tests for the Open-WebUI project-tools server.

These run the stdlib HTTP server in a thread and exercise the endpoints.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

# The server module imports aios_core modules; we patch project_creation
# locations to a temp dir so tests never touch ~/Projects.
import services.aios_core.project_creation as pc

sys.path.insert(0, str(Path(__file__).parent))
import project_tools_server as server


def _start_server(port, monkeypatch, tmp_path):
    """Start the project-tools HTTP server in a thread, return base URL."""
    projects = tmp_path / "Projects"
    data = tmp_path / "aios_data"
    monkeypatch.setattr(pc, "DEFAULT_PARENT", str(projects))
    monkeypatch.setattr(pc, "AIOS_DATA_DIR", data)
    monkeypatch.setattr(pc, "PENDING_FILE", data / "pending_projects.json")
    monkeypatch.setattr(pc, "AUDIT_FILE", data / "project_creation_audit.jsonl")

    monkeypatch.setattr(server, "HOST", "127.0.0.1")
    monkeypatch.setattr(server, "PORT", port)

    httpd = server.HTTPServer(("127.0.0.1", port), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}", httpd


@pytest.fixture
def base_url(monkeypatch, tmp_path):
    port = 18769  # unlikely to clash
    url, httpd = _start_server(port, monkeypatch, tmp_path)
    yield url
    httpd.shutdown()


def _post(url, path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_health_and_openapi(base_url):
    req = urllib.request.Request(f"{base_url}/openapi.json")
    with urllib.request.urlopen(req, timeout=5) as resp:
        spec = json.loads(resp.read().decode("utf-8"))
    assert spec["info"]["id"] == "project-tools"

    req = urllib.request.Request(f"{base_url}/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.read()


def test_propose_and_confirm_create_project(base_url):
    prop = _post(base_url, "/propose_create_project", {
        "name": "CLI Todo App",
        "description": "A simple todo manager",
        "template": "python-cli",
    })
    assert prop["dry_run"] is True
    assert "token" in prop
    assert prop["template"] == "python-cli"

    # Dry-run confirm
    dry = _post(base_url, "/confirm_create_project", {
        "token": prop["token"],
        "confirmed": False,
    })
    assert dry["dry_run"] is True
    project_root = Path(prop["project_root"])
    assert not project_root.exists()

    # Real confirm
    result = _post(base_url, "/confirm_create_project", {
        "token": prop["token"],
        "confirmed": True,
    })
    assert result["created"] is True
    assert (project_root / "README.md").exists()

    # Rollback
    rb = _post(base_url, "/rollback_project", {"token": prop["token"]})
    assert rb["rolled_back"] is True
    assert not project_root.exists()


def test_confirm_unknown_token(base_url):
    with pytest.raises(HTTPError) as exc_info:
        _post(base_url, "/confirm_create_project", {"token": "nope", "confirmed": True})
    assert exc_info.value.code == 422
