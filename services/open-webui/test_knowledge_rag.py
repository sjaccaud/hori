#!/usr/bin/env python3
import requests
import json

r = requests.post('http://127.0.0.1:3000/api/v1/auths/signin', json={'email': 'admin@localhost', 'password': 'admin'})
print('signin', r.status_code)
token = r.json()['token']

query = "What were the v2d benchmark results for Qwen3.8-27B IQ4_NL with draft-mtp"

# Query the knowledge collection (Project Decisions & Architecture)
r = requests.post(
    'http://127.0.0.1:3000/api/v1/retrieval/query/doc',
    json={'collection_name': '200a4f3f-d7d7-40e2-ac27-2e8b2a85eec8', 'query': query, 'k': 5},
    headers={'Authorization': f'Bearer {token}'}
)
print('knowledge collection', r.status_code)
if r.ok:
    data = r.json()
    docs = data.get('documents', [[]])[0]
    print('docs:', len(docs))
    for i, doc in enumerate(docs):
        print(i, doc[:300])
else:
    print(r.text[:500])
