"""Adversarial test: episode writes stay confined to the threads root.

The episode/threads MCP surface accepts model-supplied thread names and
episode fields and writes Markdown files under the threads root. An
untrusted caller could otherwise escape the root via crafted thread names
(../, absolute paths, separators), follow a symlinked thread directory out
of the root, write oversized payloads, or break out of the episode
frontmatter to corrupt the file format.

This test defines the TDD contract: all of those must be rejected before
any write occurs. It must fail before the safety is implemented and pass
once the safety is built, per Pillar VII.

Defends: the remote MCP write surface (episode_append) on aios_core and the
integrity of ~/.local/share/hori/threads/ as a durable memory store.

Traces to: .devin/prd/episode-threads-mcp.md section 3.
"""

import pytest


def _seed_thread(root, name="system-memory"):
    tdir = root / name
    (tdir / "episodes").mkdir(parents=True)
    (tdir / "state.md").write_text(
        "---\nstatus: active\nupdated: 2026-09-15\n---\n\n# state\n"
    )
    return tdir


def test_rejects_traversal_thread_names(tmp_path):
    """Thread names containing traversal or separators must never touch disk."""
    from services.aios_core import episodes

    root = tmp_path / "threads"
    _seed_thread(root)
    outside = tmp_path / "outside"
    outside.mkdir()

    bad_names = [
        "..",
        "../outside",
        "../../etc",
        "foo/bar",
        "foo\\bar",
        "/tmp/abs",
        "a" * 65,  # over 64-char slug cap
        "UPPER",
        "has space",
        "name.md",
        ".hidden",
        "",
    ]
    for name in bad_names:
        with pytest.raises(ValueError):
            episodes.append_episode(
                thread=name, title="t", summary="s", author="test", root=root
            )

    # Nothing may have been created outside the threads root.
    assert list(outside.iterdir()) == []
    assert not (tmp_path / "abs").exists()


def test_never_writes_through_symlinked_thread_dir(tmp_path):
    """A symlinked thread dir must not let episodes land outside the root."""
    from services.aios_core import episodes

    root = tmp_path / "threads"
    _seed_thread(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linkout").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        episodes.append_episode(
            thread="linkout", title="t", summary="s", author="test", root=root
        )

    assert list(outside.iterdir()) == []


def test_rejects_oversized_episode_and_fields(tmp_path):
    """Oversized bodies/fields must be rejected with nothing written."""
    from services.aios_core import episodes

    root = tmp_path / "threads"
    _seed_thread(root)

    with pytest.raises(ValueError):
        episodes.append_episode(
            thread="system-memory",
            title="t",
            summary="x" * (64 * 1024 + 1),
            author="test",
            root=root,
        )
    with pytest.raises(ValueError):
        episodes.append_episode(
            thread="system-memory",
            title="t" * 201,
            summary="s",
            author="test",
            root=root,
        )
    with pytest.raises(ValueError):
        episodes.append_episode(
            thread="system-memory",
            title="t",
            summary="s",
            author="test",
            open_issues=["i"] * 51,
            root=root,
        )

    assert list((root / "system-memory" / "episodes").iterdir()) == []


def test_rejects_frontmatter_breakout(tmp_path):
    """Fields that could corrupt frontmatter must be rejected or escaped.

    `title` is a scalar: newlines or '---' sequences are rejected outright.
    """
    from services.aios_core import episodes

    root = tmp_path / "threads"
    _seed_thread(root)

    bad_titles = [
        "legit\n---\nstatus: hacked",
        "a\nb",
        "ends with ---\n---",
        "carriage\rreturn",
    ]
    for title in bad_titles:
        with pytest.raises(ValueError):
            episodes.append_episode(
                thread="system-memory",
                title=title,
                summary="s",
                author="test",
                root=root,
            )

    assert list((root / "system-memory" / "episodes").iterdir()) == []
