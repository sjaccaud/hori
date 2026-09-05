"""Tests for the safe project-creation workflow.

Traces to: docs/safety.md "Layer 2: The Cage", docs/manifesto.md Pillar VII.
"""
from pathlib import Path

import pytest

import services.aios_core.project_creation as pc


@pytest.fixture
def isolated_pc(tmp_path, monkeypatch):
    """Return the project_creation module with all state isolated to tmp_path."""
    projects = tmp_path / "Projects"
    data = tmp_path / "aios_data"
    monkeypatch.setattr(pc, "DEFAULT_PARENT", str(projects))
    monkeypatch.setattr(pc, "AIOS_DATA_DIR", data)
    monkeypatch.setattr(pc, "PENDING_FILE", data / "pending_projects.json")
    monkeypatch.setattr(pc, "AUDIT_FILE", data / "project_creation_audit.jsonl")
    return pc


def test_propose_and_create_python_cli(isolated_pc):
    """A full happy path: propose, confirm, verify files, rollback."""
    prop = isolated_pc.propose_project(
        "CLI Todo App",
        "Simple command-line todo manager.",
        template="python-cli",
    )
    assert prop["dry_run"] is True
    assert prop["requires_confirmation"] is True
    assert prop["name"] == "cli-todo-app"
    assert prop["template"] == "python-cli"
    paths = {f["path"] for f in prop["manifest"]}
    assert "README.md" in paths
    assert "pyproject.toml" in paths
    assert "src/cli_todo_app/cli.py" in paths

    # Without confirmation, no files are written
    dry = isolated_pc.create_project(prop["token"], confirmed=False)
    assert dry["dry_run"] is True
    project_root = Path(prop["project_root"])
    assert not project_root.exists()

    # Confirm and create
    result = isolated_pc.create_project(prop["token"], confirmed=True)
    assert result["created"] is True
    assert (project_root / "README.md").exists()
    assert (project_root / "src" / "cli_todo_app" / "cli.py").exists()

    # Audit log records the creation
    assert isolated_pc.AUDIT_FILE.exists()

    # Rollback removes the project
    rb = isolated_pc.rollback_project(prop["token"])
    assert rb["rolled_back"] is True
    assert not project_root.exists()


def test_propose_rejects_path_outside_allowed_parent(isolated_pc, tmp_path):
    """Project parent must be under the configured allowed parent."""
    result = isolated_pc.propose_project(
        "Bad Project",
        "Trying to escape.",
        parent_dir=str(tmp_path / "elsewhere"),
    )
    assert "error" in result
    assert "must be under" in result["error"]


def test_create_requires_valid_token(isolated_pc):
    result = isolated_pc.create_project("no-such-token", confirmed=True)
    assert "error" in result
    assert "No pending project" in result["error"]


def test_detect_creation_intent_python_cli():
    text = 'Create a new project in ~/Projects for a "Python CLI todo app"'
    intent = pc.detect_creation_intent(text)
    assert intent is not None
    assert intent["template"] == "python-cli"
    assert "python-cli" in intent["template"]


def test_detect_creation_intent_no_match():
    assert pc.detect_creation_intent("What's the weather?") is None


@pytest.mark.parametrize("text", ["yes", "YES", "go ahead", "create it", "do it"])
def test_detect_confirmation_yes(text):
    assert pc.detect_confirmation(text) is True


def test_detect_confirmation_no():
    assert pc.detect_confirmation("no thanks") is False
