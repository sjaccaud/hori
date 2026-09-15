"""Unit tests for services.aios_core.thread_consolidation.

Traces to .devin/prd/thread-consolidation.md section 4.
"""

import json

import pytest

from services.aios_core import thread_consolidation as tc


def _seed_thread(root, name="hori", updated="2026-09-01"):
    tdir = root / name
    (tdir / "episodes").mkdir(parents=True)
    (tdir / "state.md").write_text(
        f'---\nthread: {name}\nstatus: active\nupdated: "{updated}"\n---\n'
        "\n# state body\n"
    )
    return tdir


def _episode(tdir, n, date="2026-09-15"):
    (tdir / "episodes" / f"{n:03d}-ep.md").write_text(
        f'---\ntitle: "ep"\ndate: "{date}T12:00:00+00:00"\n---\n\nbody\n'
    )


def test_plan_finds_latest_episode_date(tmp_path):
    tdir = _seed_thread(tmp_path)
    _episode(tdir, 1, "2026-09-10")
    _episode(tdir, 2, "2026-09-14")

    plan = tc.plan_mechanical_update(tdir)
    assert plan == {"updated": "2026-09-14"}


def test_mechanical_updates_only_updated_key(tmp_path):
    tdir = _seed_thread(tmp_path)
    tc.apply_mechanical(tdir, {"updated": "2026-09-15"})

    text = (tdir / "state.md").read_text()
    assert '"2026-09-15"' in text
    assert "status: active" in text
    assert "thread: hori" in text


def test_mechanical_preserves_body(tmp_path):
    tdir = _seed_thread(tmp_path)
    before = (tdir / "state.md").read_text()
    body = before.split("---", 2)[2]

    tc.apply_mechanical(tdir, {"updated": "2026-09-15"})
    after = (tdir / "state.md").read_text()
    assert after.split("---", 2)[2] == body


def test_detect_stale_flags_sessions_without_episodes(tmp_path):
    root = tmp_path / "threads"
    _seed_thread(root, "hori")
    _seed_thread(root, "llm-stack")
    _episode(root / "hori", 1)  # hori has an episode, llm-stack doesn't

    log = tmp_path / "sessions.jsonl"
    log.write_text("\n".join([
        json.dumps({"ts": "2026-09-15T10:00:00+00:00", "tool": "devin-cli",
                    "session_id": "a", "reason": "eof",
                    "cwd": "/home/user/Projects/aios"}),
        json.dumps({"ts": "2026-09-15T11:00:00+00:00", "tool": "devin-cli",
                    "session_id": "b", "reason": "eof",
                    "cwd": "/home/user/Projects/aios"}),
    ]) + "\n")

    stale = tc.detect_stale(root, log, days=7,
                            cwd_map={"aios": "hori"})
    # cwd maps to 'hori' which HAS a recent episode -> not stale
    assert stale == []

    # sessions in a dir mapping to llm-stack (no episodes) -> flagged
    log.write_text(json.dumps({
        "ts": "2026-09-15T10:00:00+00:00", "tool": "devin-cli",
        "session_id": "a", "reason": "eof",
        "cwd": "/home/user/Projects/llm-stack"}) + "\n")
    stale = tc.detect_stale(root, log, days=7)
    assert len(stale) == 1
    assert stale[0]["thread"] == "llm-stack"
    assert stale[0]["sessions"] == 1


def test_write_proposal_forces_pending(tmp_path):
    review = tmp_path / "review"
    result = tc.write_proposal(
        review, kind="state-prose", thread="hori",
        proposed="add a line about the MCP surface",
        rationale="two episodes mention it", sources=["001-x.md"])
    assert result["ok"] is True
    text = (review / result["file"]).read_text()
    assert "status: pending" in text


def test_llm_down_skips_proposals_cleanly(tmp_path):
    root = tmp_path / "threads"
    tdir = _seed_thread(root)
    _episode(tdir, 1)
    review = tmp_path / "review"

    def failing_llm(system, user):
        raise ConnectionError("router down")

    report = tc.run_consolidation(root, review, call_llm=failing_llm)
    assert (tdir / "state.md").read_text().count("2026-09-15") == 1
    assert report["llm_available"] is False
    assert report["proposals"] == []
