"""Tests for the backend-aware memory consolidation path.

Covers the Sep 17 2026 memory-split fix: when hori.yaml selects the
sqlite backend, consolidation must read working memories from
~/.local/share/hori/memory.db and promote insights back into sqlite —
not into Qdrant collections that nothing reads. Also covers the
archive step: processed points must be marked so the same clusters
aren't re-distilled every night.
"""
import sqlite3
import uuid
from pathlib import Path

import pytest

import scripts.memory_consolidation as mc
from hori import sqlite_memory


@pytest.fixture()
def memdb(tmp_path, monkeypatch):
    """Point the sqlite backend at a temp db and seed working-tier rows."""
    db = tmp_path / "memory.db"
    monkeypatch.setattr(sqlite_memory, "_DB_PATH", db)
    sqlite_memory.ensure_collections()
    return db


def _seed(db: Path, rows):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    for content, role, conv, tier in rows:
        conn.execute(
            "INSERT INTO memories (id, tier, content, role, conversation_id, "
            "surface, created_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), tier, content, role, conv, "test", "2026-09-17T00:00:00"),
        )
    conn.commit()
    conn.close()


def _tiers(db: Path):
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT tier, COUNT(*) FROM memories GROUP BY tier").fetchall()
    conn.close()
    return dict(rows)


def test_sqlite_fetch_returns_point_shims(memdb, monkeypatch):
    monkeypatch.setattr(mc, "USE_SQLITE", True)
    _seed(memdb, [
        ("turn one", "user", "conv-a", "working"),
        ("turn two", "assistant", "conv-a", "working"),
        ("unrelated", "user", "conv-b", "project"),
    ])
    points = mc.fetch_working_memory(client=None)
    assert len(points) == 2
    assert all(p.payload["conversation_id"] for p in points)
    assert {p.payload["conversation_id"] for p in points} == {"conv-a"}


def test_sqlite_clusters_and_archives(memdb, monkeypatch):
    monkeypatch.setattr(mc, "USE_SQLITE", True)
    _seed(memdb, [
        ("a1", "user", "conv-a", "working"),
        ("a2", "assistant", "conv-a", "working"),
        ("b1", "user", "conv-b", "working"),
    ])
    points = mc.fetch_working_memory(client=None)
    clusters = mc.cluster_by_conversation(points)
    assert set(clusters) == {"conv-a", "conv-b"}
    assert len(clusters["conv-a"]) == 2

    ids = [p.id for p in clusters["conv-a"]]
    mc._mark_consolidated(client=None, point_ids=ids)
    assert _tiers(memdb) == {"working": 1, "archived": 2}
    # Archived rows must not be re-fetched
    assert len(mc.fetch_working_memory(client=None)) == 1


def test_sqlite_promote_writes_project_tier(memdb, monkeypatch):
    monkeypatch.setattr(mc, "USE_SQLITE", True)
    monkeypatch.setattr(sqlite_memory, "get_embedding", lambda text: [0.0] * 4)
    distilled = {
        "summary": "insight summary",
        "topics": ["sherpa"],
        "tone_tags": ["focused"],
        "directionality": "hardening",
    }
    pid = mc.promote_to_tier(
        client=None, distilled=distilled,
        source_point_ids=["src-1", "src-2"], conversation_id="conv-x",
        tier="project",
    )
    conn = sqlite3.connect(str(memdb))
    row = conn.execute(
        "SELECT tier, role, conversation_id, edges FROM memories WHERE id=?",
        (pid,),
    ).fetchone()
    conn.close()
    assert row[0] == "project"
    assert row[1] == "consolidated"
    assert row[2] == "conv-x"
    assert "src-1" in row[3]  # distilled_from edges recorded
