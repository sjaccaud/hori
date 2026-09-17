"""
AIOS Memory Consolidation - The "Sleep & Dream" Cycle.

This is not a simple dedupe. It performs real cognitive work:
1. Cluster working-memory points by topic + conversation + time window.
2. Distill each cluster via an LLM call: extract the durable insight.
3. Promote the distilled summary to the project or longterm tier.
4. Update user_model.json and project_state.json from promoted insights.
5. Archive (don't delete) the raw working-memory points.

This is what makes the AIOS compound over time instead of relearning every day.
"""
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List

import requests

from services.aios_core.state import (
    MAX_INTERESTS,
    MAX_QUESTIONS_PER_PROJECT,
    _dedupe_interests,
)

# --- CONFIGURATION ---
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
EMBED_URL = os.getenv("EMBED_URL", "http://127.0.0.1:8082/v1/embeddings")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-base-en-v1.5")
LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:8080/v1/chat/completions")
# Router alias, not a raw model name — the installed unit sets
# LLM_MODEL=qwen-fast; the old default ("Qwen3.8-27B") 400s on the router.
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-fast")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_MODEL_PATH = PROJECT_ROOT / "core" / "state" / "user_model.json"
PROJECT_STATE_PATH = PROJECT_ROOT / "core" / "state" / "project_state.json"

COLLECTION_WORKING = "aios_working"
COLLECTION_PROJECT = "aios_project"
COLLECTION_LONGTERM = "aios_longterm"

DRY_RUN = os.getenv("CONSOLIDATION_DRY_RUN", "false").lower() == "true"

# --- Backend selection ---
# The script must read/promote through the SAME backend hori.yaml selects —
# hardcoding Qdrant here while backend=sqlite left new memories invisible
# to consolidation (the Sep 2026 memory-split finding, same class as the
# Sherpa audit-path drift). hori.config is authoritative; the env override
# exists for tests and direct runs.
def _use_sqlite() -> bool:
    try:
        from hori.config import MEMORY_BACKEND
        return MEMORY_BACKEND == "sqlite"
    except Exception:
        return os.getenv("HORI_MEMORY_BACKEND", "qdrant") == "sqlite"


USE_SQLITE = _use_sqlite()
if not USE_SQLITE:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_embedding(text: str) -> List[float]:
    payload = {"model": EMBED_MODEL, "input": text}
    response = requests.post(EMBED_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def call_llm(system_prompt: str, user_prompt: str) -> str:
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.3,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    response = requests.post(LLM_API_URL, json=payload, timeout=120)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


class _MemPoint:
    """Lightweight shim giving sqlite rows the point.id/point.payload
    shape the cluster/distill code was written against (Qdrant points)."""
    __slots__ = ("id", "payload")

    def __init__(self, id_: str, payload: Dict[str, Any]):
        self.id = id_
        self.payload = payload


def _fetch_working_sqlite(limit: int) -> List:
    from hori import sqlite_memory
    conn = sqlite_memory._get_conn()
    try:
        rows = conn.execute(
            "SELECT id, content, role, conversation_id, topics, tone_tags, "
            "directionality, created_at FROM memories WHERE tier='working' "
            "ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        _MemPoint(r["id"], {
            "content": r["content"],
            "role": r["role"],
            "conversation_id": r["conversation_id"],
        })
        for r in rows
    ]


def _fetch_working_qdrant(client, limit: int) -> List:
    # Exclude already-consolidated points: processed points are marked
    # consolidated=true so the same clusters aren't re-distilled nightly.
    flt = models.Filter(must_not=[
        models.FieldCondition(key="consolidated", match=models.MatchValue(value=True))
    ])
    result = client.scroll(
        collection_name=COLLECTION_WORKING,
        scroll_filter=flt,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return result[0] if result else []


def fetch_working_memory(client, limit: int = 500) -> List:
    """Fetch points from the working memory tier (configured backend)."""
    if USE_SQLITE:
        return _fetch_working_sqlite(limit)
    return _fetch_working_qdrant(client, limit)


def _mark_consolidated(client, point_ids: List[str]) -> None:
    """Archive processed working-memory points so the next run sees only
    unprocessed input (the docstring's 'archive, don't delete' step —
    previously unimplemented, so the same top-10 clusters re-distilled
    every night)."""
    if not point_ids:
        return
    if USE_SQLITE:
        from hori import sqlite_memory
        conn = sqlite_memory._get_conn()
        try:
            conn.executemany(
                "UPDATE memories SET tier='archived' WHERE id=?",
                [(pid,) for pid in point_ids],
            )
            conn.commit()
        finally:
            conn.close()
    else:
        client.set_payload(
            collection_name=COLLECTION_WORKING,
            payload={"consolidated": True},
            points=point_ids,
        )


def cluster_by_conversation(points: List) -> Dict[str, List]:
    """Group working-memory points by conversation_id."""
    clusters: Dict[str, List] = {}
    for point in points:
        conv_id = point.payload.get("conversation_id", "unknown")
        clusters.setdefault(conv_id, []).append(point)
    return clusters


def distill_cluster(points: List) -> Dict[str, Any]:
    """
    Use the LLM to distill a cluster of conversation turns into a durable insight.
    Returns {summary, topics, tone_tags, directionality, decisions, open_questions}.
    """
    turns = []
    for p in sorted(points, key=lambda x: x.payload.get("content", "")):
        role = p.payload.get("role", "?")
        content = p.payload.get("content", "")[:500]
        turns.append(f"[{role}] {content}")

    turns_text = "\n".join(turns)

    system_prompt = (
        "You are the AIOS Memory Consolidator. Your job is to distill a cluster "
        "of conversation turns into a durable, structured insight. "
        "Return ONLY valid JSON with these fields:\n"
        '{"summary": "2-3 sentence distilled insight", '
        '"topics": ["topic1", "topic2"], '
        '"tone_tags": ["tag1"], '
        '"directionality": "where is this heading", '
        '"decisions": ["any decisions made"], '
        '"open_questions": ["any unresolved questions"]}'
    )
    user_prompt = f"Conversation turns to distill:\n\n{turns_text}"

    try:
        response = call_llm(system_prompt, user_prompt)
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean
            clean = clean.rsplit("```", 1)[0]
        return json.loads(clean)
    except Exception as e:
        logger.warning(f"Distillation failed, using fallback: {e}")
        combined = " ".join(p.payload.get("content", "")[:100] for p in points)
        return {
            "summary": combined[:300],
            "topics": [],
            "tone_tags": [],
            "directionality": "",
            "decisions": [],
            "open_questions": [],
        }


def promote_to_tier(
    client,
    distilled: Dict[str, Any],
    source_point_ids: List[str],
    conversation_id: str,
    tier: str = "project",
):
    """Embed and upsert a distilled insight into the project or longterm tier."""
    edges = [{"type": "distilled_from", "target": pid} for pid in source_point_ids]

    if USE_SQLITE:
        from hori import sqlite_memory
        point_id = sqlite_memory.store_memory(
            content=distilled["summary"],
            role="consolidated",
            conversation_id=conversation_id,
            surface="consolidation",
            tier=tier,
            topics=distilled.get("topics", []),
            tone_tags=distilled.get("tone_tags", []),
            directionality=distilled.get("directionality", ""),
            edges=edges,
        )
        logger.info(f"Promoted distilled insight to sqlite {tier}: {point_id}")
        return point_id

    collection = COLLECTION_PROJECT if tier == "project" else COLLECTION_LONGTERM
    vector = get_embedding(distilled["summary"])
    point_id = str(uuid.uuid4())

    payload = {
        "content": distilled["summary"],
        "role": "consolidated",
        "conversation_id": conversation_id,
        "surface": "consolidation",
        "tier": tier,
        "topics": distilled.get("topics", []),
        "tone_tags": distilled.get("tone_tags", []),
        "directionality": distilled.get("directionality", ""),
        "source_point_ids": source_point_ids,
        "edges": edges,
    }

    client.upsert(
        collection_name=collection,
        points=[models.PointStruct(id=point_id, vector=vector, payload=payload)],
    )
    logger.info(f"Promoted distilled insight to {collection}: {point_id}")
    return point_id


def update_state_files(distilled_insights: List[Dict[str, Any]]):
    """
    Update user_model.json and project_state.json from promoted insights.
    This is the loop that makes the system learn the user over time.
    Filters out test/incident noise to prevent state file pollution.
    """
    # Noise filter: topics/decisions/questions from test incidents or stress tests
    NOISE_PATTERNS = {"test_service", "test", "stress", "mock", "dummy", "example"}
    NOISE_PREFIXES = ("test_", "mock_", "dummy_")

    def is_noise(text: str) -> bool:
        lower = text.lower()
        if any(lower == p or lower.startswith(p) for p in NOISE_PREFIXES):
            return True
        if any(p in lower for p in NOISE_PATTERNS):
            return True
        return False

    all_topics = set()
    all_tones = set()
    all_decisions = []
    all_questions = []

    for insight in distilled_insights:
        # Filter topics
        for topic in insight.get("topics", []):
            if not is_noise(topic):
                all_topics.add(topic)
        # Filter tone tags
        for tone in insight.get("tone_tags", []):
            if not is_noise(tone):
                all_tones.add(tone)
        # Filter decisions
        for decision in insight.get("decisions", []):
            if not is_noise(decision):
                all_decisions.append(decision)
        # Filter questions
        for question in insight.get("open_questions", []):
            if not is_noise(question):
                all_questions.append(question)

    # Update user model
    if USER_MODEL_PATH.exists():
        with open(USER_MODEL_PATH, "r") as f:
            user_model = json.load(f)
    else:
        user_model = {}

    existing_tones = set(user_model.get("tone_tags", []))
    existing_tones.update(all_tones)
    user_model["tone_tags"] = list(existing_tones)

    existing_interests = set(user_model.get("active_interests", []))
    existing_interests.update(all_topics)
    # Dedupe near-duplicates and cap to MAX_INTERESTS to prevent
    # unbounded growth. Without this, 500 turns produced 209 interests
    # that consumed 82% of the 16K context window.
    user_model["active_interests"] = _dedupe_interests(list(existing_interests), max_items=MAX_INTERESTS)

    _save_json(USER_MODEL_PATH, user_model)
    logger.info(f"Updated user_model.json with {len(all_tones)} tone tags, {len(all_topics)} topics")

    # Update project state
    if PROJECT_STATE_PATH.exists():
        with open(PROJECT_STATE_PATH, "r") as f:
            project_state = json.load(f)
    else:
        project_state = {"active_projects": [], "recent_decisions": [], "deferred_items": []}

    if all_decisions:
        existing_decisions = project_state.get("recent_decisions", [])
        for d in all_decisions:
            if d not in existing_decisions:
                existing_decisions.append(d)
        project_state["recent_decisions"] = existing_decisions[-20:]  # Keep last 20

    if all_questions:
        for project in project_state.get("active_projects", []):
            existing_qs = set(project.get("open_questions", []))
            for q in all_questions:
                if q not in existing_qs:
                    project.setdefault("open_questions", []).append(q)
                    existing_qs.add(q)
            # Cap open questions to prevent unbounded growth.
            # Without this, 500 turns produced 571 questions.
            project["open_questions"] = project["open_questions"][-MAX_QUESTIONS_PER_PROJECT:]

    _save_json(PROJECT_STATE_PATH, project_state)
    logger.info(f"Updated project_state.json with {len(all_decisions)} decisions, {len(all_questions)} questions")


def _save_json(path: Path, data: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def consolidate_memory(max_clusters: int = 10):
    """
    Main consolidation cycle: cluster -> distill -> promote -> update state.
    Processes at most max_clusters per run to avoid long blocking times.
    """
    logger.info(
        f"Starting Memory Consolidation (max {max_clusters} clusters, "
        f"backend={'sqlite' if USE_SQLITE else 'qdrant'})"
    )

    client = None if USE_SQLITE else QdrantClient(url=QDRANT_URL)

    # 1. Fetch working memory
    points = fetch_working_memory(client)
    if not points:
        logger.info("No working memory points found. Nothing to consolidate.")
        return

    logger.info(f"Loaded {len(points)} working-memory points.")

    # 2. Cluster by conversation
    clusters = cluster_by_conversation(points)
    logger.info(f"Found {len(clusters)} conversation clusters.")

    # 3. Distill and promote each cluster (limited to max_clusters per run)
    distilled_insights = []
    promoted_count = 0

    # Sort clusters by size (largest first) and limit
    sorted_clusters = sorted(clusters.items(), key=lambda x: len(x[1]), reverse=True)
    clusters_to_process = sorted_clusters[:max_clusters]

    for conv_id, cluster_points in clusters_to_process:
        logger.info(f"Distilling cluster (conv={conv_id}, {len(cluster_points)} points)...")

        distilled = distill_cluster(cluster_points)
        distilled_insights.append(distilled)

        source_ids = [p.id for p in cluster_points]

        if DRY_RUN:
            logger.info(f"[DRY RUN] Would promote: {distilled['summary'][:100]}")
        else:
            # Promote to project tier (cross-project insights go to longterm in future)
            promote_to_tier(client, distilled, source_ids, conv_id, tier="project")
            # Archive the source points so the next cycle sees fresh input
            _mark_consolidated(client, source_ids)
            promoted_count += 1

    # 4. Update state files
    if distilled_insights and not DRY_RUN:
        update_state_files(distilled_insights)

    remaining = len(clusters) - len(clusters_to_process)
    logger.info(
        f"Consolidation complete. Distilled {len(clusters_to_process)} clusters, "
        f"promoted {promoted_count} insights. {remaining} clusters remaining for next cycle."
    )


if __name__ == "__main__":
    import sys
    max_clusters = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    consolidate_memory(max_clusters=max_clusters)
