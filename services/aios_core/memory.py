import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models

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

_client: Optional[QdrantClient] = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL)
    return _client


def ensure_collections():
    """Create the three memory tier collections if they don't exist."""
    client = get_client()
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


def get_embedding(text: str) -> List[float]:
    """Get embedding from the dedicated embedding server."""
    payload = {"model": EMBED_MODEL, "input": text}
    response = requests.post(EMBED_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


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
    """Embed and upsert a memory point into the appropriate tier collection."""
    collection = _tier_to_collection(tier)
    vector = get_embedding(content)
    point_id = str(uuid.uuid4())

    payload = {
        "content": content,
        "role": role,
        "conversation_id": conversation_id,
        "surface": surface,
        "tier": tier,
        "topics": topics or [],
        "tone_tags": tone_tags or [],
        "directionality": directionality,
        "work_order_id": work_order_id,
        "parent_charter_id": parent_charter_id,
        "edges": edges or [],
    }

    get_client().upsert(
        collection_name=collection,
        points=[models.PointStruct(id=point_id, vector=vector, payload=payload)],
    )
    logger.debug(f"Stored memory in {collection}: {point_id}")
    return point_id


def retrieve_memory(
    query: str,
    tier: str = "project",
    limit: int = 5,
    score_threshold: float = 0.6,
) -> List[Dict[str, Any]]:
    """Semantic search over a memory tier. Returns matching payloads."""
    collection = _tier_to_collection(tier)
    vector = get_embedding(query)

    results = get_client().query_points(
        collection_name=collection,
        query=vector,
        limit=limit,
        score_threshold=score_threshold,
    )

    return [point.payload for point in results.points if point.payload]


def retrieve_conversation_turns(
    query: str,
    conversation_id: str,
    limit: int = 10,
    score_threshold: float = 0.3,
) -> List[Dict[str, Any]]:
    """Semantic search for past turns within a single conversation.

    This is the retrieval primitive behind the elastic context window
    (docs/elastic_context_window.md). It does a filtered vector query on
    the `aios_working` collection: only points whose `conversation_id`
    payload matches `conversation_id`, ranked by similarity to `query`.

    Why filtered by conversation_id: the elastic window retrieves "past
    turns in THIS conversation, ranked by similarity to THIS prompt."
    Without the filter, retrieval would mix in turns from unrelated
    conversations and the LLM would see context it can't ground. The
    filter is what makes the moat work (see the design doc's "Competitive
    Moat" section — conversation identity is one of the four pillars).

    The filtered query is verified working against Qdrant; see the design
    doc for the exact query shape. Returns matching payloads (each has
    `content`, `role`, `conversation_id`, `tier`, etc.), highest
    similarity first. Returns an empty list if Qdrant or the embedding
    server is unreachable — callers must treat that as "no history
    available" and fall back to the dumb window.
    """
    vector = get_embedding(query)
    query_filter = models.Filter(must=[
        models.FieldCondition(
            key="conversation_id",
            match=models.MatchValue(value=conversation_id),
        )
    ])
    results = get_client().query_points(
        collection_name=COLLECTION_WORKING,
        query=vector,
        limit=limit,
        score_threshold=score_threshold,
        query_filter=query_filter,
    )
    return [point.payload for point in results.points if point.payload]


def _tier_to_collection(tier: str) -> str:
    return {
        "working": COLLECTION_WORKING,
        "project": COLLECTION_PROJECT,
        "longterm": COLLECTION_LONGTERM,
    }.get(tier, COLLECTION_WORKING)
