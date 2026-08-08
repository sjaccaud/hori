
import pytest
import requests

# Configuration
EMBED_URL = "http://localhost:8081/v1/embeddings"
QDRANT_URL = "http://localhost:6333"
EMBEDDING_MODEL = "nomic-embed-text-v1.5.Q8_0"
COLLECTION_NAME = "test_collection"

@pytest.fixture
def sample_text():
    return "The quick brown fox jumps over the lazy dog."

@pytest.fixture
def sample_embedding(sample_text):
    payload = {
        "model": EMBEDDING_MODEL,
        "input": sample_text
    }
    response = requests.post(EMBED_URL, json=payload)
    response.raise_for_status()
    embedding = response.json()["data"][0]["embedding"]
    if not embedding:
        raise ValueError("Failed to get embedding from response.")
    return embedding

def test_embedding_endpoint(sample_text):
    print(f"Testing llama-server embedding for: '{sample_text}'")
    payload = {
        "model": EMBEDDING_MODEL,
        "input": sample_text
    }
    response = requests.post(EMBED_URL, json=payload)
    response.raise_for_status()
    embedding = response.json()["data"][0]["embedding"]
    assert embedding is not None
    assert len(embedding) > 0

def test_qdrant_connection():
    print(f"Testing Qdrant connection at {QDRANT_URL}")
    response = requests.get(f"{QDRANT_URL}/collections")
    assert response.status_code == 200
    collections = response.json().get("collections", [])
    assert isinstance(collections, list)

def test_qdrant_upsert(sample_embedding, sample_text):
    print(f"Testing Qdrant upsert for text: '{sample_text}'")
    payload = {
        "action": "upsert",
        "collection_name": COLLECTION_NAME,
        "points": [
            {
                "id": 1,
                "vector": sample_embedding,
                "payload": {"content": sample_text}
            }
        ]
    }
    
    # First, ensure collection exists
    requests.put(f"{QDRANT_URL}/collections/{COLLECTION_NAME}", json={
        "vectors": {
            "size": len(sample_embedding),
            "distance": "Cosine"
        }
    })
    
    url = f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points"
    response = requests.put(url, json=payload)
    assert response.status_code == 200
