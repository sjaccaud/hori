#!/usr/bin/env python3
import requests

BASE = "http://127.0.0.1:3000"

# Sign in
r = requests.post(f"{BASE}/api/v1/auths/signin", json={"email": "admin@localhost", "password": "admin"})
r.raise_for_status()
token = r.json()["token"]
headers = {"Authorization": f"Bearer {token}"}

# Find Local Benchmarks collection
r = requests.get(f"{BASE}/api/v1/knowledge/", headers=headers)
r.raise_for_status()
print('list response:', r.text[:500])
data = r.json()
if isinstance(data, dict):
    items = data.get('items', []) or data.get('data', [])
else:
    items = data
knowledge_id = None
for k in items:
    if k.get("name") == "Local Benchmarks":
        knowledge_id = k["id"]
        break

if not knowledge_id:
    print("Local Benchmarks collection not found")
    exit(1)

print(f"Reindexing {knowledge_id}")
r = requests.post(f"{BASE}/api/v1/knowledge/{knowledge_id}/reindex", headers=headers)
print(r.status_code, r.text[:500])
