#!/usr/bin/env python3
import requests
import json

BASE = "http://127.0.0.1:3000"

r = requests.post(f"{BASE}/api/v1/auths/signin", json={"email": "admin@localhost", "password": "admin"})
token = r.json()["token"]
headers = {"Authorization": f"Bearer {token}"}

r = requests.get(f"{BASE}/api/v1/models/list", headers=headers)
resp = r.json()
print('models response keys:', resp.keys() if isinstance(resp, dict) else type(resp))
data = resp.get('data') or resp.get('items') or []
print('data len:', len(data), 'first type:', type(data[0]) if data else None)
print('ids:', [m.get('id') for m in data])
for m in data:
    if m.get('id') == 'qwen-fast':
        print('--- workspace model ---')
        print(json.dumps({k: v for k, v in m.items() if k in ['id','name','meta','params','base_model_id']}, indent=2))
        break
