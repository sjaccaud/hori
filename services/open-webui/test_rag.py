#!/usr/bin/env python3
import requests
import json

r = requests.post('http://127.0.0.1:3000/api/v1/auths/signin', json={'email': 'admin@localhost', 'password': 'admin'})
print('signin', r.status_code)
token = r.json()['token']

query = "Qwen3.8-27B IQ4_NL draft-mtp benchmark tokens per second"
for coll in ['file-552b85eb-49b0-4708-8901-0719d465aeba', 'file-ff89e586-c0fa-4fa5-b5f4-6e71a2ffddff']:
    r = requests.post(
        'http://127.0.0.1:3000/api/v1/retrieval/query/doc',
        json={'collection_name': coll, 'query': query, 'k': 3},
        headers={'Authorization': f'Bearer {token}'}
    )
    print(coll, r.status_code, r.text[:500])
    if r.ok:
        data = r.json()
        docs = data.get('documents', [[]])[0]
        print('docs:', len(docs))
        for i, doc in enumerate(docs):
            print(i, doc[:200])
