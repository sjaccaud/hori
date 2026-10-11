"""OpenAI-compatible /v1/embeddings endpoint using BAAI/bge-base-en-v1.5.

Runs inside the Open-WebUI container and reuses the sentence-transformer
model that Open-WebUI already caches in its Docker volume."""
import glob
import os
from typing import List, Union

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

_BAAI_GLOB = (
    "/app/backend/data/cache/embedding/models/"
    "models--BAAI--bge-base-en-v1.5/snapshots/*"
)
_PATHS = sorted(glob.glob(_BAAI_GLOB))
if not _PATHS:
    raise RuntimeError(f"BAAI/bge-base-en-v1.5 snapshot not found at {_BAAI_GLOB}")

MODEL = SentenceTransformer(_PATHS[0])

app = FastAPI()


class EmbedRequest(BaseModel):
    input: Union[str, List[str]]
    model: str = "BAAI/bge-base-en-v1.5"


@app.post("/v1/embeddings")
def embed(req: EmbedRequest):
    texts = req.input if isinstance(req.input, list) else [req.input]
    vectors = MODEL.encode(texts, normalize_embeddings=True).tolist()
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": v, "index": i}
            for i, v in enumerate(vectors)
        ],
        "model": "BAAI/bge-base-en-v1.5",
    }
