"""SQLite-based memory backend for HORI.

This module provides the same interface as services/aios_core/memory.py
but uses SQLite + cosine similarity instead of Qdrant. This allows HORI
to run with zero external dependencies beyond Python — no Qdrant server
required.

The SQLite backend stores embeddings as JSON arrays in a TEXT column.
Cosine similarity is computed in Python (numpy). This is slower than
Qdrant's HNSW index but fine for the typical HORI workload (hundreds
to low thousands of memories, not millions).

Interface (matches services/aios_core/memory.py):
  - ensure_collections()   → create tables if they don't exist
  - get_embedding(text)    → get embedding from embedding server
  - store_memory(...)      → embed and insert a memory point
  - retrieve_memory(...)   → semantic search over a tier
  - retrieve_conversation_turns(...) → filtered search within a conversation
  - scroll_all(tier)       → iterate all points (for intent graph building)

Storage:
  ~/.local/share/hori/memory.db

  Tables:
    memories(id TEXT PRIMARY KEY, tier TEXT, content TEXT, role TEXT,
             conversation_id TEXT, surface TEXT, topics TEXT, tone_tags TEXT,
             directionality TEXT, work_order_id TEXT, parent_charter_id TEXT,
             edges TEXT, embedding TEXT, created_at TEXT)
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)

# --- Config (resolved lazily so tests can patch) ---
_DB_PATH: Optional[Path] = None
_EMBED_URL: Optional[str] = None
_EMBED_MODEL: Optional[str] = None
_EMBED_DIM: Optional[int] = None

# Tier → table name mapping (single table, filtered by tier column)
# We use one table with a tier column instead of three tables because
# it simplifies the schema and queries. The tier is just a filter.


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH
    # Default: ~/.local/share/hori/memory.db
    db = Path.home() / ".local" / "share" / "hori" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    _DB_PATH = db
    return db


def _get_embed_url() -> str:
    global _EMBED_URL
    if _EMBED_URL is not None:
        return _EMBED_URL
    try:
        from services.aios_core.config import EMBED_URL
        return EMBED_URL
    except ImportError:
        return os.getenv("EMBED_URL", "http://localhost:8081/v1/embeddings")


def _get_embed_model() -> str:
    global _EMBED_MODEL
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    try:
        from services.aios_core.config import EMBED_MODEL
        return EMBED_MODEL
    except ImportError:
        return os.getenv("EMBED_MODEL", "nomic-embed-text-v1.5.Q8_0")


def _get_embed_dim() -> int:
    global _EMBED_DIM
    if _EMBED_DIM is not None:
        return _EMBED_DIM
    try:
        from services.aios_core.config import EMBED_DIM
        return EMBED_DIM
    except ImportError:
        return int(os.getenv("EMBED_DIM", "768"))


import os  # noqa: E402 (needed for env fallbacks above)


# --- Database connection ---

def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection. Creates the DB file if it doesn't exist."""
    conn = sqlite3.connect(str(_get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_collections():
    """Create the memories table if it doesn't exist.

    Matches the Qdrant backend's ensure_collections() interface.
    """
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                tier TEXT NOT NULL DEFAULT 'working',
                content TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                conversation_id TEXT NOT NULL DEFAULT '',
                surface TEXT NOT NULL DEFAULT '',
                topics TEXT NOT NULL DEFAULT '[]',
                tone_tags TEXT NOT NULL DEFAULT '[]',
                directionality TEXT NOT NULL DEFAULT '',
                work_order_id TEXT,
                parent_charter_id TEXT,
                edges TEXT NOT NULL DEFAULT '[]',
                embedding TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tier ON memories(tier)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conv ON memories(conversation_id)")
        conn.commit()
        logger.info("SQLite memory table ensured")
    finally:
        conn.close()


# --- Embedding ---

def get_embedding(text: str) -> List[float]:
    """Get embedding from the embedding server (same as Qdrant backend)."""
    payload = {"model": _get_embed_model(), "input": text}
    response = requests.post(_get_embed_url(), json=payload, timeout=30)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


# --- Cosine similarity ---

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# --- Store / Retrieve ---

def store_memory(
    content: str,
    role: str,
    conversation_id: str,
    surface: str = "open_webui",
    tier: str = "working",
    topics: Optional[List[str]] = None,
    tone_tags: Optional[List[str]] = None,
    directionality: str = "",
    work_order_id: Optional[str] = None,
    parent_charter_id: Optional[str] = None,
    edges: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Embed and store a memory point. Returns the point ID."""
    vector = get_embedding(content)
    point_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO memories
               (id, tier, content, role, conversation_id, surface, topics,
                tone_tags, directionality, work_order_id, parent_charter_id,
                edges, embedding, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                point_id, tier, content, role, conversation_id, surface,
                json.dumps(topics or []),
                json.dumps(tone_tags or []),
                directionality,
                work_order_id,
                parent_charter_id,
                json.dumps(edges or []),
                json.dumps(vector),
                now,
            ),
        )
        conn.commit()
        logger.debug(f"Stored memory in SQLite ({tier}): {point_id}")
    finally:
        conn.close()
    return point_id


def retrieve_memory(
    query: str,
    tier: str = "project",
    limit: int = 5,
    score_threshold: float = 0.6,
) -> List[Dict[str, Any]]:
    """Semantic search over a memory tier. Returns matching payloads."""
    vector = get_embedding(query)
    return _search(vector, tier=tier, limit=limit, score_threshold=score_threshold)


def retrieve_conversation_turns(
    query: str,
    conversation_id: str,
    limit: int = 10,
    score_threshold: float = 0.3,
) -> List[Dict[str, Any]]:
    """Semantic search for past turns within a single conversation."""
    vector = get_embedding(query)
    return _search(
        vector, tier="working", limit=limit,
        score_threshold=score_threshold,
        conversation_id=conversation_id,
    )


def _search(
    query_vector: List[float],
    tier: str = "working",
    limit: int = 5,
    score_threshold: float = 0.6,
    conversation_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search memories by cosine similarity. Returns payloads, best first."""
    conn = _get_conn()
    try:
        if conversation_id:
            rows = conn.execute(
                "SELECT * FROM memories WHERE tier = ? AND conversation_id = ?",
                (tier, conversation_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories WHERE tier = ?",
                (tier,),
            ).fetchall()
    finally:
        conn.close()

    # Score each row
    scored = []
    for row in rows:
        emb = json.loads(row["embedding"])
        score = _cosine_similarity(query_vector, emb)
        if score >= score_threshold:
            scored.append((score, row))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[:limit]

    # Convert rows to payload dicts (matching Qdrant's payload format)
    results = []
    for score, row in scored:
        payload = {
            "content": row["content"],
            "role": row["role"],
            "conversation_id": row["conversation_id"],
            "surface": row["surface"],
            "tier": row["tier"],
            "topics": json.loads(row["topics"]),
            "tone_tags": json.loads(row["tone_tags"]),
            "directionality": row["directionality"],
            "work_order_id": row["work_order_id"],
            "parent_charter_id": row["parent_charter_id"],
            "edges": json.loads(row["edges"]),
            # SQLite-specific: include score and ID for debugging
            "_id": row["id"],
            "_score": score,
        }
        results.append(payload)
    return results


# --- Scroll (for intent graph building) ---

def scroll_all(tier: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Iterate all memory points, optionally filtered by tier.

    This replaces Qdrant's scroll() for the intent graph builder.
    Yields payload dicts with an added 'id' field.
    """
    conn = _get_conn()
    try:
        if tier:
            rows = conn.execute(
                "SELECT * FROM memories WHERE tier = ?", (tier,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memories").fetchall()
    finally:
        conn.close()

    for row in rows:
        yield {
            "id": row["id"],
            "content": row["content"],
            "role": row["role"],
            "conversation_id": row["conversation_id"],
            "surface": row["surface"],
            "tier": row["tier"],
            "topics": json.loads(row["topics"]),
            "tone_tags": json.loads(row["tone_tags"]),
            "directionality": row["directionality"],
            "work_order_id": row["work_order_id"],
            "parent_charter_id": row["parent_charter_id"],
            "edges": json.loads(row["edges"]),
        }


# --- Maintenance ---

def count(tier: Optional[str] = None) -> int:
    """Count memories, optionally filtered by tier."""
    conn = _get_conn()
    try:
        if tier:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM memories WHERE tier = ?", (tier,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()


def delete_all(tier: Optional[str] = None) -> int:
    """Delete all memories, optionally filtered by tier. Returns count deleted."""
    conn = _get_conn()
    try:
        if tier:
            cur = conn.execute("DELETE FROM memories WHERE tier = ?", (tier,))
        else:
            cur = conn.execute("DELETE FROM memories")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _tier_to_collection(tier: str) -> str:
    """Compatibility shim — SQLite uses a single table, but this returns
    the tier name for code that expects a collection name."""
    return tier
