#!/usr/bin/env python3
import requests

BASE = "http://127.0.0.1:3000"

r = requests.post(f"{BASE}/api/v1/auths/signin", json={"email": "admin@localhost", "password": "admin"})
r.raise_for_status()
token = r.json()["token"]
headers = {"Authorization": f"Bearer {token}"}

# Get files in Local Benchmarks
r = requests.get(f"{BASE}/api/v1/knowledge/f851345f-935d-48a2-a101-6590466a9be2/files", headers=headers)
r.raise_for_status()
data = r.json()
files = data.get('items') or data.get('files') or data.get('data') or []
print(f'Found {len(files)} files in Local Benchmarks')

for f in files:
    fid = f['id']
    print(f'Reprocessing {f.get("filename", fid)} ({fid}) ...')
    r = requests.post(
        f"{BASE}/api/v1/retrieval/process/file",
        json={"file_id": fid},
        headers=headers,
    )
    if r.ok:
        print('  ok')
    else:
        print('  error:', r.status_code, r.text[:200])
