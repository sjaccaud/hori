"""
Ingest foundational AIOS documents into the aios_longterm memory tier.

This grounds every chat in the intent hierarchy (manifesto, charter,
governance, architecture, roadmap) so the system always knows what it is
and what it's building toward.
"""
import hashlib
import logging
import os
from pathlib import Path
from typing import List

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
EMBED_URL = os.getenv("EMBED_URL", "http://localhost:8081/v1/embeddings")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text-v1.5.Q8_0")
COLLECTION = "aios_longterm"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Foundational documents to ingest
DOCS_TO_INGEST = [
    PROJECT_ROOT / "docs" / "manifesto.md",
    PROJECT_ROOT / "docs" / "governance_safety.md",
    PROJECT_ROOT / "docs" / "architecture_analysis.md",
    PROJECT_ROOT / "docs" / "roadmap.md",
    PROJECT_ROOT / "core" / "intent" / "manifesto.json",
    PROJECT_ROOT / "core" / "intent" / "charter.json",
    PROJECT_ROOT / "core" / "intent" / "schema.json",
    PROJECT_ROOT / "core" / "state" / "user_model.json",
    PROJECT_ROOT / "core" / "state" / "project_state.json",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def get_embedding(text: str) -> List[float]:
    payload = {"model": EMBED_MODEL, "input": text}
    response = requests.post(EMBED_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def chunk_text(text: str, max_chars: int = 1000) -> List[str]:
    """Split text into chunks at paragraph boundaries."""
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > max_chars and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks


def ingest():
    client = QdrantClient(url=QDRANT_URL)

    # Ensure collection exists
    try:
        client.get_collection(collection_name=COLLECTION)
    except Exception:
        logger.info(f"Creating collection: {COLLECTION}")
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE),
        )

    total_ingested = 0

    for doc_path in DOCS_TO_INGEST:
        if not doc_path.exists():
            logger.warning(f"File not found: {doc_path}")
            continue

        text = doc_path.read_text()
        chunks = chunk_text(text)
        logger.info(f"Ingesting {doc_path.name}: {len(chunks)} chunks")

        for i, chunk in enumerate(chunks):
            if len(chunk) < 20:
                continue

            doc_id = hashlib.md5(f"{doc_path.name}_{i}".encode()).hexdigest()
            try:
                vector = get_embedding(chunk)
            except Exception as e:
                logger.warning(f"Failed to embed chunk {i} of {doc_path.name}: {e}")
                continue

            payload = {
                "content": chunk,
                "role": "system",
                "conversation_id": "foundational",
                "surface": "ingestion",
                "tier": "longterm",
                "source_file": str(doc_path.relative_to(PROJECT_ROOT)),
                "chunk_index": i,
                "topics": [doc_path.stem],
                "tone_tags": [],
                "directionality": "",
                "edges": [],
            }

            client.upsert(
                collection_name=COLLECTION,
                points=[models.PointStruct(id=doc_id, vector=vector, payload=payload)],
            )
            total_ingested += 1

    logger.info(f"Ingestion complete. {total_ingested} chunks stored in {COLLECTION}.")


if __name__ == "__main__":
    ingest()
