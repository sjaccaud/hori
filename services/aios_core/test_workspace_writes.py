"""Tests for confirmation-gated workspace Markdown writes.

Traces to: docs/safety.md Layer 4 (Human-in-the-Loop) and Pillar VII TDD.
"""
import json
import time

import pytest

from services.aios_core import workspace_writes as ww


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "Projects"
    workspace.mkdir()
    data = tmp_path / "aios_data"
    monkeypatch.setattr(ww, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(ww, "AIOS_DATA_DIR", data)
    monkeypatch.setattr(ww, "PENDING_FILE", data / "pending_workspace_writes.json")
    monkeypatch.setattr(ww, "AUDIT_FILE", data / "workspace_write_audit.jsonl")
    return workspace


def test_propose_confirm_and_rollback_workspace_write(isolated_workspace):
    prop = ww.propose_workspace_write(
        "Create a content strategy scaffold",
        [
            {"path": "content_factory/strategy", "is_dir": True},
            {
                "path": "content_factory/strategy/positioning.md",
                "content": "# Positioning\n",
            },
        ],
    )
    assert prop["dry_run"] is True
    assert prop["requires_confirmation"] is True
    assert "content" not in prop["manifest"][1]

    target_dir = isolated_workspace / "content_factory" / "strategy"
    target_file = target_dir / "positioning.md"
    assert not target_dir.exists()

    dry = ww.confirm_workspace_write(prop["token"], confirmed=False)
    assert dry["dry_run"] is True
    assert not target_dir.exists()

    result = ww.confirm_workspace_write(prop["token"], confirmed=True)
    assert result["created"] is True
    assert target_dir.is_dir()
    assert target_file.read_text() == "# Positioning\n"
    assert ww.AUDIT_FILE.exists()

    rollback = ww.rollback_workspace_write(prop["token"])
    assert rollback["rolled_back"] is True
    assert not target_file.exists()
    assert not target_dir.exists()


def test_workspace_write_rejects_unknown_and_expired_tokens(isolated_workspace):
    target = isolated_workspace / "content_factory" / "expired.md"
    assert "error" in ww.confirm_workspace_write("missing-token", confirmed=True)
    assert not target.exists()

    proposal = ww.propose_workspace_write(
        "Create an expiring scaffold",
        [{"path": "content_factory/expired.md", "content": "# Expired\n"}],
    )
    pending = json.loads(ww.PENDING_FILE.read_text())
    pending[proposal["token"]]["created_at"] = time.time() - ww.PENDING_TTL - 1
    ww.PENDING_FILE.write_text(json.dumps(pending))

    assert "error" in ww.confirm_workspace_write(proposal["token"], confirmed=True)
    assert not target.exists()


def test_workspace_rollback_preserves_pre_existing_directory(isolated_workspace):
    existing_dir = isolated_workspace / "content_factory"
    existing_dir.mkdir()
    target = existing_dir / "new-note.md"

    proposal = ww.propose_workspace_write(
        "Add a note inside an existing directory",
        [{"path": "content_factory/new-note.md", "content": "# New note\n"}],
    )
    created = ww.confirm_workspace_write(proposal["token"], confirmed=True)
    assert created["created"] is True
    assert target.exists()

    rolled_back = ww.rollback_workspace_write(proposal["token"])
    assert rolled_back["rolled_back"] is True
    assert not target.exists()
    assert existing_dir.is_dir()
