#!/usr/bin/env python3
import requests

BASE = "http://127.0.0.1:3000"

r = requests.post(f"{BASE}/api/v1/auths/signin", json={"email": "admin@localhost", "password": "admin"})
r.raise_for_status()
token = r.json()["token"]

query = "What were the v2d benchmark results for Qwen3.8-27B IQ4_NL with draft-mtp"
r = requests.post(
    f"{BASE}/api/v1/retrieval/query/doc",
    json={"collection_name": "f851345f-935d-48a2-a101-6590466a9be2", "query": query, "k": 3},
    headers={"Authorization": f"Bearer {token}"}
)
print('status:', r.status_code)
if r.ok:
    data = r.json()
    docs = data.get('documents', [[]])[0]
    print('docs:', len(docs))
    for i, doc in enumerate(docs):
        print(f'--- doc {i} ---')
        print(doc[:600])
