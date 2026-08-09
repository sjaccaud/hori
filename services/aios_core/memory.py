import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import requests

from .config import (
    COLLECTION_LONGTERM,
    COLLECTION_PROJECT,
    COLLECTION_WORKING,
    EMBED_DIM,
    EMBED_MODEL,
    EMBED_URL,
    QDRANT_URL,
)

logger = logging.getLogger(__name__)

# --- Backend selection ---
# HORI supports two memory backends:
#   - "qdrant": requires a Qdrant server (default, best performance)
#   - "sqlite": zero external deps, uses ~/.local/share/hori/memory.db
#
# The backend is selected via hori.yaml → memory.backend or
# HORI_MEMORY_BACKEND env var. All functions below dispatch to the
# active backend transparently.

try:
    from hori.config import MEMORY_BACKEND
except ImportError:
    MEMORY_BACKEND = "qdrant"  # fallback if hori.config not available

_USE_SQLITE = MEMORY_BACKEND == "sqlite"

if _USE_SQLITE:
    from hori import sqlite_memory as _backend
else:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models

    _client: Optional[QdrantClient] = None

    class _QdrantBackend:
        """Wrapper that exposes the same interface as sqlite_memory."""

        def get_client(self) -> QdrantClient:
            global _client
            if _client is None:
                _client = QdrantClient(url=QDRANT_URL)
            return _client

        def ensure_collections(self):
            client = self.get_client()
            for name in (COLLECTION_WORKING, COLLECTION_PROJECT, COLLECTION_LONGTERM):
                try:
                    client.get_collection(collection_name=name)
                except Exception:
                    logger.info(f"Creating Qdrant collection: {name}")
                    client.create_collection(
                        collection_name=name,
                        vectors_config=models.VectorParams(
                            size=EMBED_DIM, distance=models.Distance.COSINE
                        ),
                    )

        def get_embedding(self, text: str) -> List[float]:
            payload = {"model": EMBED_MODEL, "input": text}
            response = requests.post(EMBED_URL, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()["data"][0]["embedding"]

        def store_memory(self, content, role, conversation_id, surface="open_webui",
                         tier="working", topics=None, tone_tags=None,
                         directionality="", work_order_id=None,
                         parent_charter_id=None, edges=None) -> str:
            collection = _tier_to_collection(tier)
            vector = self.get_embedding(content)
            point_id = str(uuid.uuid4())
            payload = {
                "content": content, "role": role,
                "conversation_id": conversation_id, "surface": surface,
                "tier": tier, "topics": topics or [],
                "tone_tags": tone_tags or [], "directionality": directionality,
                "work_order_id": work_order_id,
                "parent_charter_id": parent_charter_id,
                "edges": edges or [],
            }
            self.get_client().upsert(
                collection_name=collection,
                points=[models.PointStruct(id=point_id, vector=vector, payload=payload)],
            )
            logger.debug(f"Stored memory in {collection}: {point_id}")
            return point_id

        def retrieve_memory(self, query, tier="project", limit=5,
                            score_threshold=0.6) -> List[Dict[str, Any]]:
            collection = _tier_to_collection(tier)
            vector = self.get_embedding(query)
            results = self.get_client().query_points(
                collection_name=collection, query=vector,
                limit=limit, score_threshold=score_threshold,
            )
            return [p.payload for p in results.points if p.payload]

        def retrieve_conversation_turns(self, query, conversation_id,
                                        limit=10, score_threshold=0.3) -> List[Dict[str, Any]]:
            vector = self.get_embedding(query)
            query_filter = models.Filter(must=[
                models.FieldCondition(
                    key="conversation_id",
                    match=models.MatchValue(value=conversation_id),
                )
            ])
            results = self.get_client().query_points(
                collection_name=COLLECTION_WORKING, query=vector,
                limit=limit, score_threshold=score_threshold,
                query_filter=query_filter,
            )
            return [p.payload for p in results.points if p.payload]

        def scroll_all(self, tier=None):
            """Scroll all points (for intent graph building)."""
            collections = [tier] if tier else [COLLECTION_WORKING, COLLECTION_PROJECT, COLLECTION_LONGTERM]
            for collection in collections:
                offset = None
                while True:
                    result = self.get_client().scroll(
                        collection_name=collection if not tier else _tier_to_collection(tier),
                        limit=100, offset=offset,
                        with_payload=True, with_vectors=False,
                    )
                    points, next_offset = result
                    if not points:
                        break
                    for point in points:
                        payload = point.payload or {}
                        payload["id"] = str(point.id)
                        yield payload
                    offset = next_offset
                    if offset is None:
                        break

    _backend = _QdrantBackend()


# --- Public API (dispatches to active backend) ---

def get_client():
    """Get the backend client (Qdrant client or None for SQLite)."""
    if _USE_SQLITE:
        return None
    return _backend.get_client()


def ensure_collections():
    """Create memory tables/collections if they don't exist."""
    _backend.ensure_collections()


def get_embedding(text: str) -> List[float]:
    """Get embedding from the dedicated embedding server."""
    return _backend.get_embedding(text)


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
    return _backend.store_memory(
        content=content, role=role, conversation_id=conversation_id,
        surface=surface, tier=tier, topics=topics, tone_tags=tone_tags,
        directionality=directionality, work_order_id=work_order_id,
        parent_charter_id=parent_charter_id, edges=edges,
    )


def retrieve_memory(
    query: str,
    tier: str = "project",
    limit: int = 5,
    score_threshold: float = 0.6,
) -> List[Dict[str, Any]]:
    """Semantic search over a memory tier. Returns matching payloads."""
    return _backend.retrieve_memory(
        query=query, tier=tier, limit=limit,
        score_threshold=score_threshold,
    )


def retrieve_conversation_turns(
    query: str,
    conversation_id: str,
    limit: int = 10,
    score_threshold: float = 0.3,
) -> List[Dict[str, Any]]:
    """Semantic search for past turns within a single conversation.

    This is the retrieval primitive behind the elastic context window
    (docs/operations.md). It does a filtered vector query:
    only points whose `conversation_id` matches, ranked by similarity
    to `query`. Returns an empty list if the backend or embedding server
    is unreachable — callers must treat that as "no history available"
    and fall back to the dumb window.
    """
    return _backend.retrieve_conversation_turns(
        query=query, conversation_id=conversation_id,
        limit=limit, score_threshold=score_threshold,
    )


def scroll_all(tier: Optional[str] = None):
    """Iterate all memory points (for intent graph building)."""
    yield from _backend.scroll_all(tier=tier)


def _tier_to_collection(tier: str) -> str:
    return {
        "working": COLLECTION_WORKING,
        "project": COLLECTION_PROJECT,
        "longterm": COLLECTION_LONGTERM,
    }.get(tier, COLLECTION_WORKING)
