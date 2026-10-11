#!/usr/bin/env python3
"""Create a dedicated Local Benchmarks collection and attach benchmark files to it."""

import requests
import json
import sqlite3
import time

BASE = "http://127.0.0.1:3000"
DB = "/app/backend/data/webui.db"

# Sign in
r = requests.post(f"{BASE}/api/v1/auths/signin", json={"email": "admin@localhost", "password": "admin"})
r.raise_for_status()
token = r.json()["token"]
headers = {"Authorization": f"Bearer {token}"}

# Get existing benchmark file IDs from Project Decisions & Architecture collection
conn = sqlite3.connect(DB)
c = conn.cursor()
old_knowledge_id = "200a4f3f-d7d7-40e2-ac27-2e8b2a85eec8"
c.execute("""
    SELECT file_id FROM knowledge_file WHERE knowledge_id = ? AND file_id IN (
        SELECT id FROM file WHERE filename LIKE '%benchmark%' OR filename LIKE 'results_%' OR filename LIKE 'matrix_%' OR filename LIKE '%v2%'
    )
""", (old_knowledge_id,))
file_ids = [row[0] for row in c.fetchall()]
print(f"Found {len(file_ids)} benchmark files in old collection")

# Create new collection
r = requests.post(
    f"{BASE}/api/v1/knowledge/create",
    json={
        "name": "Local Benchmarks",
        "description": "Benchmark results and matrices for local LLM inference",
        "data": {"status": True}
    },
    headers=headers,
)
r.raise_for_status()
new_knowledge = r.json()
new_knowledge_id = new_knowledge["id"]
print(f"Created knowledge collection: {new_knowledge_id}")

# Add files to new collection
for file_id in file_ids:
    r = requests.post(
        f"{BASE}/api/v1/knowledge/{new_knowledge_id}/file/add",
        json={"file_id": file_id},
        headers=headers,
    )
    if not r.ok:
        print(f"add error for {file_id}: {r.status_code} {r.text[:200]}")
    else:
        print(f"added {file_id}")

# Update qwen-fast (and other Qwen models) to use the new collection instead of Project Decisions & Architecture
models_to_update = ["qwen-fast", "qwen-long", "qwen-think", "qwen-balanced", "qwen-quality", "devstral-coding"]
for mid in models_to_update:
    c.execute("SELECT meta FROM model WHERE id = ?", (mid,))
    row = c.fetchone()
    if not row:
        continue
    meta = json.loads(row[0])
    knowledge = meta.get("knowledge", [])
    # Remove old Project Decisions & Architecture, add Local Benchmarks
    knowledge = [k for k in knowledge if k["id"] != old_knowledge_id]
    knowledge.append({
        "id": new_knowledge_id,
        "name": "Local Benchmarks",
        "type": "collection"
    })
    meta["knowledge"] = knowledge
    c.execute(
        "UPDATE model SET meta = ?, updated_at = ? WHERE id = ?",
        (json.dumps(meta, ensure_ascii=False), int(time.time()), mid),
    )
    print(f"updated {mid} knowledge list")

conn.commit()
conn.close()
print("Done.")
