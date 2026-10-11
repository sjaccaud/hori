#!/usr/bin/env python3
import requests

BASE = "http://127.0.0.1:3000"

r = requests.post(f"{BASE}/api/v1/auths/signin", json={"email": "admin@localhost", "password": "admin"})
r.raise_for_status()
token = r.json()["token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

payload = {
    "model": "qwen-fast",
    "messages": [
        {"role": "user", "content": "What were the v2d benchmark results for Qwen3.8-27B IQ4_NL with draft-mtp?"}
    ],
    "stream": False,
    "max_tokens": 1024,
}

r = requests.post(f"{BASE}/api/chat/completions", json=payload, headers=headers)
print('status:', r.status_code)
if r.ok:
    data = r.json()
    msg = data['choices'][0]['message']['content']
    print(msg)
else:
    print(r.text[:1000])
