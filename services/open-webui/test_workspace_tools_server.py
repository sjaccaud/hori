"""Tests for the Open-WebUI workspace-tools server.

The server is a thin OpenAPI adapter over aios_core.workspace_writes; tests
isolate the workspace and audit state to tmp_path.
"""
import json
import sys
import threading
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

from services.aios_core import workspace_writes as ww

sys.path.insert(0, str(Path(__file__).parent))
import workspace_tools_server as server


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


def test_workspace_tools_propose_confirm_and_rollback(monkeypatch, tmp_path):
    workspace = tmp_path / "Projects"
    workspace.mkdir()
    data = tmp_path / "aios_data"
    monkeypatch.setattr(ww, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(ww, "AIOS_DATA_DIR", data)
    monkeypatch.setattr(ww, "PENDING_FILE", data / "pending_workspace_writes.json")
    monkeypatch.setattr(ww, "AUDIT_FILE", data / "workspace_write_audit.jsonl")

    httpd = server.HTTPServer(("127.0.0.1", 18771), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:18771"
    try:
        spec = json.loads(
            urllib.request.urlopen(f"{base}/openapi.json", timeout=5).read()
        )
        assert spec["info"]["id"] == "workspace-tools"
        assert "/propose_workspace_write" in spec["paths"]

        proposal = _post(base, "/propose_workspace_write", {
            "description": "Create a content scaffold",
            "entries": [
                {"path": "content_factory/strategy", "is_dir": True},
                {"path": "content_factory/strategy/notes.md", "content": "# Notes\n"},
            ],
        })
        assert proposal["requires_confirmation"] is True
        target = workspace / "content_factory" / "strategy" / "notes.md"
        assert not target.exists()

        created = _post(base, "/confirm_workspace_write", {
            "token": proposal["token"],
            "confirmed": True,
        })
        assert created["created"] is True
        assert target.read_text() == "# Notes\n"

        rolled_back = _post(
            base, "/rollback_workspace_write", {"token": proposal["token"]}
        )
        assert rolled_back["rolled_back"] is True
        assert not target.exists()
    finally:
        httpd.shutdown()


def test_workspace_tools_rejects_unknown_tool(tmp_path, monkeypatch):
    workspace = tmp_path / "Projects"
    workspace.mkdir()
    monkeypatch.setattr(ww, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(ww, "AIOS_DATA_DIR", tmp_path / "aios_data")
    httpd = server.HTTPServer(("127.0.0.1", 18772), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as exc_info:
            _post("http://127.0.0.1:18772", "/delete_everything", {})
        assert exc_info.value.code == 404
    finally:
        httpd.shutdown()


def test_workspace_tools_rejects_oversized_body(tmp_path, monkeypatch):
    workspace = tmp_path / "Projects"
    workspace.mkdir()
    monkeypatch.setattr(ww, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(ww, "AIOS_DATA_DIR", tmp_path / "aios_data")
    monkeypatch.setattr(server, "MAX_HTTP_BODY_BYTES", 64)

    httpd = server.HTTPServer(("127.0.0.1", 18773), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        body = b'{"description":"x","entries":' + b" " * 128 + b"[]}"
        req = urllib.request.Request(
            "http://127.0.0.1:18773/propose_workspace_write",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 413
    finally:
        httpd.shutdown()
