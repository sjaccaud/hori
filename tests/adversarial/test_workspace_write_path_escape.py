"""Adversarial test: workspace Markdown writer path escape.

The workspace write surface accepts model-supplied relative paths and creates
directories or Markdown files under a configured Projects workspace. An
untrusted caller could otherwise escape the workspace, write non-Markdown
payloads, follow a symlink out of the root, or overwrite an existing file.

This test defines the TDD contract: proposals containing any of those paths
must be rejected before any write occurs. It must fail before the safety is
implemented and pass once the safety is built, per Pillar VII.

Defends: the Open-WebUI workspace write surface used by model-selected project
scaffolds under the configured workspace root.
"""

def test_workspace_write_rejects_escape_symlink_and_non_markdown(tmp_path, monkeypatch):
    """Malicious manifest entries must be rejected without writing anything."""
    from services.aios_core import workspace_writes as ww

    workspace = tmp_path / "Projects"
    workspace.mkdir()
    data_dir = tmp_path / "aios_data"
    monkeypatch.setattr(ww, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(ww, "AIOS_DATA_DIR", data_dir)
    monkeypatch.setattr(ww, "PENDING_FILE", data_dir / "pending_workspace_writes.json")
    monkeypatch.setattr(ww, "AUDIT_FILE", data_dir / "workspace_write_audit.jsonl")

    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    existing = workspace / "existing.md"
    existing.write_text("# keep me\n")

    bad_entries = [
        {"path": "../outside/evil.md", "content": "escape"},
        {"path": str(outside / "absolute.md"), "content": "absolute"},
        {"path": "escape/evil.md", "content": "symlink"},
        {"path": "content_factory/payload.txt", "content": "not markdown"},
        {"path": "existing.md", "content": "overwrite"},
    ]
    for entry in bad_entries:
        result = ww.propose_workspace_write("bad write", [entry])
        assert "error" in result, entry

    assert not (outside / "evil.md").exists()
    assert not (outside / "absolute.md").exists()
    assert not (workspace / "content_factory" / "payload.txt").exists()
    assert existing.read_text() == "# keep me\n"


def test_workspace_rollback_rejects_symlink_swap(tmp_path, monkeypatch):
    """Rollback must not delete a pre-existing target via a swapped symlink."""
    from services.aios_core import workspace_writes as ww

    workspace = tmp_path / "Projects"
    workspace.mkdir()
    data_dir = tmp_path / "aios_data"
    monkeypatch.setattr(ww, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(ww, "AIOS_DATA_DIR", data_dir)
    monkeypatch.setattr(ww, "PENDING_FILE", data_dir / "pending_workspace_writes.json")
    monkeypatch.setattr(ww, "AUDIT_FILE", data_dir / "workspace_write_audit.jsonl")

    keep = workspace / "keep.md"
    keep.write_text("# keep me\n")
    proposal = ww.propose_workspace_write(
        "scaffold",
        [{"path": "content_factory/draft.md", "content": "# draft\n"}],
    )
    created = ww.confirm_workspace_write(proposal["token"], confirmed=True)
    assert created["created"] is True

    created_path = workspace / "content_factory" / "draft.md"
    created_path.unlink()
    created_path.symlink_to(keep)

    rolled_back = ww.rollback_workspace_write(proposal["token"])
    assert "error" in rolled_back
    assert keep.read_text() == "# keep me\n"
    assert created_path.is_symlink()
