"""Unit tests for services.aios_core.episodes — the thread/episode store.

Traces to .devin/prd/episode-threads-mcp.md section 4.
"""

from services.aios_core import episodes


def _seed_thread(root, name="system-memory"):
    tdir = root / name
    (tdir / "episodes").mkdir(parents=True)
    (tdir / "state.md").write_text(
        "---\nstatus: active\nupdated: 2026-09-15\n---\n\n# state\n"
    )
    return tdir


def test_append_creates_episode_file(tmp_path):
    root = tmp_path / "threads"
    _seed_thread(root)

    result = episodes.append_episode(
        thread="system-memory",
        title="First episode",
        summary="Did the thing.",
        author="devin",
        root=root,
    )

    assert result["ok"] is True
    ep = root / "system-memory" / "episodes" / result["file"]
    assert ep.exists()
    text = ep.read_text()
    assert "First episode" in text
    assert "Did the thing." in text
    assert "devin" in text


def test_read_thread_returns_state_and_latest_episode(tmp_path):
    root = tmp_path / "threads"
    _seed_thread(root)
    episodes.append_episode(thread="system-memory", title="One", summary="first",
                            author="devin", root=root)
    episodes.append_episode(thread="system-memory", title="Two", summary="second",
                            author="devin", root=root)

    out = episodes.read_thread("system-memory", root=root)
    assert out["name"] == "system-memory"
    assert "status: active" in out["state"]
    assert len(out["episodes"]) == 1  # default: latest only
    assert "second" in out["episodes"][0]["body"]
    assert out["episodes"][0]["frontmatter"]["title"] == "Two"

    out = episodes.read_thread("system-memory", episodes=2, root=root)
    assert len(out["episodes"]) == 2


def test_list_threads_returns_status(tmp_path):
    root = tmp_path / "threads"
    _seed_thread(root)
    _seed_thread(root, "gaming-platform")
    episodes.append_episode(thread="gaming-platform", title="T", summary="s",
                            author="devin", root=root)

    out = episodes.list_threads(root=root)
    by_name = {t["name"]: t for t in out}
    assert by_name["system-memory"]["status"] == "active"
    assert by_name["gaming-platform"]["episode_count"] == 1
    assert by_name["system-memory"]["episode_count"] == 0


def test_search_finds_substring(tmp_path):
    root = tmp_path / "threads"
    _seed_thread(root)
    episodes.append_episode(thread="system-memory", title="T",
                            summary="the undervolt holds at -100 mV",
                            author="devin", root=root)

    hits = episodes.search_episodes("undervolt", root=root)
    assert len(hits) == 1
    assert "undervolt" in hits[0]["context"]
    assert hits[0]["file"].endswith(".md")

    # scoped to a thread that lacks the term
    _seed_thread(root, "other-thread")
    assert episodes.search_episodes("undervolt", thread="other-thread", root=root) == []


def test_append_is_append_only_no_overwrite(tmp_path):
    root = tmp_path / "threads"
    _seed_thread(root)
    a = episodes.append_episode(thread="system-memory", title="Same",
                                summary="one", author="d", root=root)
    b = episodes.append_episode(thread="system-memory", title="Same",
                                summary="two", author="d", root=root)
    assert a["file"] != b["file"]
    assert (root / "system-memory" / "episodes" / a["file"]).exists()


def test_missing_thread_errors(tmp_path):
    import pytest
    root = tmp_path / "threads"
    root.mkdir()
    with pytest.raises(FileNotFoundError):
        episodes.append_episode(thread="nope", title="t", summary="s",
                                author="d", root=root)
    with pytest.raises(FileNotFoundError):
        episodes.read_thread("nope", root=root)
