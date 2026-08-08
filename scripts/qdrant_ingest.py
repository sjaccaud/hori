import hashlib
import json
import os

import mido
import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models

# --- CONFIGURATION ---
MIDI_VAULT_PATH = "/mnt/8TB/midi_archives"
QDRANT_URL = "http://localhost:6333"
EMBED_URL = "http://localhost:8081/v1/embeddings"
EMBEDDING_MODEL = "nomic-embed-text-v1.5.Q8_0"
COLLECTION_NAME = "riff_library_qdrant"

print("🧠 Booting Riffmaster Qdrant Ingestion (UNLIMITED)...")

# 1. Initialize Qdrant Client
client = QdrantClient(url=QDRANT_URL)

# Ensure collection exists
try:
    client.get_collection(collection_name=COLLECTION_NAME)
    print(f"✅ Found existing collection: {COLLECTION_NAME}")
except Exception:
    print(f"🆕 Creating new collection: {COLLECTION_NAME}")
    # We'll determine vector size from the first embedding
    # For now, let's assume 768 based on our test
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE),
    )

def parse_midi_to_riffscript(filepath):
    """A lightweight parser to convert MIDI files into RiffScript context."""
    try:
        mid = mido.MidiFile(filepath)
        sequence = []
        
        # Simple extraction: just grabbing the first 16 note events
        # to give the AI a 'vibe'
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'note_on' and msg.velocity > 0:
                    # Guess articulation based on velocity (just for training context)
                    art = "pm" if msg.velocity < 90 else "pco"
                    
                    event = {
                        "n": msg.note,
                        "v": msg.velocity,
                        "d": 0.25, # Normalized for context
                        "w": 0.25,
                        "a": art
                    }
                    sequence.append(event)
                    
                    if len(sequence) >= 16:
                        return json.dumps(sequence, separators=(',', ':'))
        
        return json.dumps(sequence, separators=(',', ':')) if sequence else None
    except Exception:
        # print(f"Error parsing {filepath}: {e}")
        return None

def get_embedding(text):
    """Get embedding from llama-server's OpenAI-compatible /v1/embeddings endpoint."""
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text
    }
    try:
        response = requests.post(EMBED_URL, json=payload)
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]
    except Exception as e:
        print(f"❌ Error getting embedding: {e}")
        return None

# 2. Scan and Ingest
print(f"📂 Scanning {MIDI_VAULT_PATH} for MIDI files...")

if not os.path.exists(MIDI_VAULT_PATH):
    print(f"❌ Error: Cannot find {MIDI_VAULT_PATH}. Is the NAS mounted?")
    exit(1)

documents = []
metadatas = []
ids = []

processed_count = 0

for root, dirs, files in os.walk(MIDI_VAULT_PATH):
    for file in files:
        if file.lower().endswith(('.mid', '.midi')):
            filepath = os.path.join(root, file)
            riffscript = parse_midi_to_riffscript(filepath)
            
            if riffscript and len(riffscript) > 10:
                doc_id = hashlib.md5(filepath.encode()).hexdigest()
                
                documents.append(riffscript)
                metadatas.append({"filename": file, "source": "nas_vault"})
                ids.append(doc_id)
                
                processed_count += 1
                if processed_count % 100 == 0:
                    print(f"🔄 Found {processed_count} valid MIDI files...")

# 3. Embed and Upsert into Qdrant
if documents:
    print(f"🧬 Embedding {len(documents)} RiffScripts into Qdrant...")
    
    batch_size = 50
    for i in range(0, len(documents), batch_size):
        end_idx = min(i + batch_size, len(documents))
        batch_docs = documents[i:end_idx]
        batch_metas = metadatas[i:end_idx]
        batch_ids = ids[i:end_idx]
        
        batch_vectors = []
        valid_batch_indices = []

        for idx, doc in enumerate(batch_docs):
            emb = get_embedding(doc)
            if emb:
                batch_vectors.append(emb)
                valid_batch_indices.append(idx)
            else:
                print(f"⚠️ Failed to embed doc {idx} in batch")

        if batch_vectors:
            # Re-align metadata and ids with successful embeddings
            final_vectors = batch_vectors
            final_metas = [batch_metas[idx] for idx in valid_batch_indices]
            final_ids = [batch_ids[idx] for idx in valid_batch_indices]

            client.upsert(
                collection_name=COLLECTION_NAME,
                points=[
                    models.PointStruct(
                        id=final_ids[j],
                        vector=final_vectors[j],
                        payload=final_metas[j]
                    ) for j in range(len(final_ids))
                ]
            )
            print(
                f"📦 Embedded batch {i} to {end_idx} "
                f"({len(final_vectors)} successful)..."
            )

    print(
        f"🎸 Brain ingestion complete! {processed_count} files processed. "
        f"Qdrant collection '{COLLECTION_NAME}' is ready."
    )
else:
    print("⚠️ No valid MIDI files found to ingest.")