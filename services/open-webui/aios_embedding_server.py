"""Minimal OpenAI-compatible embedding server for the Open-WebUI image.

Runs inside ghcr.io/open-webui/open-webui:main using the image's built-in
sentence-transformers / FastAPI packages. Loads the model from the
RAG_EMBEDDING_MODEL env var and serves /v1/embeddings and /health.
"""
import logging
import os
import time
from typing import List, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

MODEL_NAME = os.environ.get("RAG_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
TRUST_REMOTE_CODE = os.environ.get("RAG_EMBEDDING_MODEL_TRUST_REMOTE_CODE", "").lower() in (
    "true",
    "1",
    "yes",
)
BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "32"))

app = FastAPI(title="aios-embedding")
model: SentenceTransformer | None = None


@app.on_event("startup")
def load_model() -> None:
    """Load the embedding model once at startup."""
    global model
    log.info("Loading embedding model: %s", MODEL_NAME)
    start = time.time()
    model = SentenceTransformer(
        MODEL_NAME,
        trust_remote_code=TRUST_REMOTE_CODE,
        device="cpu",
    )
    log.info("Model loaded in %.2f seconds", time.time() - start)


class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]]
    model: str = MODEL_NAME


@app.get("/health")
def health() -> dict:
    """Docker health check endpoint."""
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {"status": True}


@app.post("/v1/embeddings")
def create_embeddings(req: EmbeddingRequest) -> dict:
    """OpenAI-compatible embeddings endpoint."""
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    texts = req.input if isinstance(req.input, list) else [req.input]
    if not texts:
        raise HTTPException(status_code=400, detail="input must not be empty")

    start = time.time()
    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    log.info("Encoded %d texts in %.2f seconds", len(texts), time.time() - start)

    return {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "embedding": embedding.tolist(),
                "index": idx,
            }
            for idx, embedding in enumerate(embeddings)
        ],
        "model": req.model or MODEL_NAME,
    }
