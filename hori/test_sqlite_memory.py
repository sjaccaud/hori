"""Tests for hori.sqlite_memory — SQLite-based memory backend.

These tests use a temporary SQLite database and mock the embedding server
so they run without any external dependencies.
"""
import json
import math
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hori import sqlite_memory


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point sqlite_memory at a temp database."""
    db_path = tmp_path / "test_memory.db"
    monkeypatch.setattr(sqlite_memory, "_DB_PATH", db_path)
    sqlite_memory.ensure_collections()
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def mock_embedding(monkeypatch):
    """Mock get_embedding to return deterministic vectors."""
    def fake_embedding(text: str):
        # Simple deterministic embedding: hash-based vector
        vec = [0.0] * 8
        for i, c in enumerate(text):
            vec[i % 8] += ord(c) / 1000.0
        # Normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    monkeypatch.setattr(sqlite_memory, "get_embedding", fake_embedding)
    return fake_embedding


class TestEnsureCollections:
    def test_creates_table(self, temp_db):
        import sqlite3
        conn = sqlite3.connect(str(temp_db))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        assert ("memories",) in tables

    def test_idempotent(self, temp_db):
        # Calling again should not error
        sqlite_memory.ensure_collections()
        sqlite_memory.ensure_collections()

    def test_indexes_exist(self, temp_db):
        import sqlite3
        conn = sqlite3.connect(str(temp_db))
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        conn.close()
        index_names = [r[0] for r in indexes]
        assert "idx_tier" in index_names
        assert "idx_conv" in index_names


class TestStoreAndRetrieve:
    def test_store_returns_id(self, temp_db, mock_embedding):
        pid = sqlite_memory.store_memory(
            content="Hello world", role="user",
            conversation_id="conv1", tier="working",
        )
        assert isinstance(pid, str)
        assert len(pid) > 0

    def test_retrieve_finds_match(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="The sky is blue", role="assistant",
            conversation_id="conv1", tier="working",
        )
        results = sqlite_memory.retrieve_memory(
            query="sky color", tier="working", limit=5, score_threshold=0.0,
        )
        assert len(results) >= 1
        assert "sky is blue" in results[0]["content"]

    def test_retrieve_respects_tier(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="working memory", role="user",
            conversation_id="conv1", tier="working",
        )
        sqlite_memory.store_memory(
            content="project memory", role="user",
            conversation_id="conv1", tier="project",
        )
        # Query working tier only
        results = sqlite_memory.retrieve_memory(
            query="memory", tier="working", limit=10, score_threshold=0.0,
        )
        assert all(r["tier"] == "working" for r in results)
        # Query project tier only
        results = sqlite_memory.retrieve_memory(
            query="memory", tier="project", limit=10, score_threshold=0.0,
        )
        assert all(r["tier"] == "project" for r in results)

    def test_retrieve_respects_limit(self, temp_db, mock_embedding):
        for i in range(10):
            sqlite_memory.store_memory(
                content=f"memory item {i}", role="user",
                conversation_id="conv1", tier="working",
            )
        results = sqlite_memory.retrieve_memory(
            query="memory", tier="working", limit=3, score_threshold=0.0,
        )
        assert len(results) <= 3

    def test_retrieve_score_threshold(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="completely unrelated xyzzy", role="user",
            conversation_id="conv1", tier="working",
        )
        # High threshold should filter out poor matches
        results = sqlite_memory.retrieve_memory(
            query="something different", tier="working",
            limit=5, score_threshold=0.99,
        )
        assert len(results) == 0


class TestConversationTurns:
    def test_filtered_by_conversation(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="turn 1 in conv A", role="user",
            conversation_id="convA", tier="working",
        )
        sqlite_memory.store_memory(
            content="turn 1 in conv B", role="user",
            conversation_id="convB", tier="working",
        )
        results = sqlite_memory.retrieve_conversation_turns(
            query="turn", conversation_id="convA",
            limit=10, score_threshold=0.0,
        )
        assert all(r["conversation_id"] == "convA" for r in results)
        assert len(results) == 1

    def test_empty_for_nonexistent_conversation(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="some memory", role="user",
            conversation_id="convA", tier="working",
        )
        results = sqlite_memory.retrieve_conversation_turns(
            query="memory", conversation_id="nonexistent",
            limit=10, score_threshold=0.0,
        )
        assert len(results) == 0


class TestScrollAll:
    def test_scroll_all_tiers(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="working", role="user",
            conversation_id="c1", tier="working",
        )
        sqlite_memory.store_memory(
            content="project", role="user",
            conversation_id="c1", tier="project",
        )
        sqlite_memory.store_memory(
            content="longterm", role="user",
            conversation_id="c1", tier="longterm",
        )
        all_points = list(sqlite_memory.scroll_all())
        assert len(all_points) == 3

    def test_scroll_by_tier(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="working", role="user",
            conversation_id="c1", tier="working",
        )
        sqlite_memory.store_memory(
            content="project", role="user",
            conversation_id="c1", tier="project",
        )
        working_only = list(sqlite_memory.scroll_all(tier="working"))
        assert len(working_only) == 1
        assert working_only[0]["tier"] == "working"

    def test_scroll_includes_id(self, temp_db, mock_embedding):
        pid = sqlite_memory.store_memory(
            content="test", role="user",
            conversation_id="c1", tier="working",
        )
        points = list(sqlite_memory.scroll_all())
        assert any(p["id"] == pid for p in points)

    def test_scroll_includes_payload_fields(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="test content", role="assistant",
            conversation_id="conv123", surface="voice",
            tier="working", topics=["ai", "safety"],
            tone_tags=["friendly"],
        )
        points = list(sqlite_memory.scroll_all())
        p = points[0]
        assert p["content"] == "test content"
        assert p["role"] == "assistant"
        assert p["conversation_id"] == "conv123"
        assert p["surface"] == "voice"
        assert p["topics"] == ["ai", "safety"]
        assert p["tone_tags"] == ["friendly"]


class TestCountAndDelete:
    def test_count_all(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="a", role="user", conversation_id="c", tier="working",
        )
        sqlite_memory.store_memory(
            content="b", role="user", conversation_id="c", tier="project",
        )
        assert sqlite_memory.count() == 2

    def test_count_by_tier(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="a", role="user", conversation_id="c", tier="working",
        )
        sqlite_memory.store_memory(
            content="b", role="user", conversation_id="c", tier="working",
        )
        sqlite_memory.store_memory(
            content="c", role="user", conversation_id="c", tier="project",
        )
        assert sqlite_memory.count(tier="working") == 2
        assert sqlite_memory.count(tier="project") == 1

    def test_delete_by_tier(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="a", role="user", conversation_id="c", tier="working",
        )
        sqlite_memory.store_memory(
            content="b", role="user", conversation_id="c", tier="project",
        )
        deleted = sqlite_memory.delete_all(tier="working")
        assert deleted == 1
        assert sqlite_memory.count(tier="working") == 0
        assert sqlite_memory.count(tier="project") == 1

    def test_delete_all(self, temp_db, mock_embedding):
        sqlite_memory.store_memory(
            content="a", role="user", conversation_id="c", tier="working",
        )
        sqlite_memory.store_memory(
            content="b", role="user", conversation_id="c", tier="project",
        )
        deleted = sqlite_memory.delete_all()
        assert deleted == 2
        assert sqlite_memory.count() == 0


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert sqlite_memory._cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert sqlite_memory._cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert sqlite_memory._cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert sqlite_memory._cosine_similarity([], []) == 0.0

    def test_different_length_vectors(self):
        assert sqlite_memory._cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    def test_zero_norm(self):
        assert sqlite_memory._cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
