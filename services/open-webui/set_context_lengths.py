#!/usr/bin/env python3
import json
import sqlite3
import sys
import time

DB = sys.argv[1] if len(sys.argv) > 1 else "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

# Keep in sync with the [section] ctx in router.ini and MAX_TOKENS in
# fix_tooling.py. Measured 2026-09-07 on the R9700 (31.9 GiB): qwen35 hybrid
# KV costs ~25.3 KB/token at q4_0, draft-mtp reserves ~1.8 GiB, Devstral f16
# KV ~160 KB/token, and the GPU-resident nano server (llama-nano.service,
# 32k ctx) adds a fixed ~3 GiB alongside whichever preset is loaded.
# qwen-quality is pinned at 16k: Q8_0 weights + q8_0 KV already hit ~30.4 GiB.
contexts = {
    'qwen-fast': 65536,
    'qwen-think': 65536,
    'qwen-balanced': 131072,
    'qwen-quality': 16384,
    'qwen-long': 131072,
    'qwen-128k': 196608,
    'qwen-256k-batch': 262144,
    'devstral-coding': 65536,
    'qwen-dsh-fast': 65536,
    'qwen-dsh-low': 65536,
    'qwen-dsh-medium': 65536,
    'qwen-deepdive': 131072,
    'qwen-free': 131072,
    'nano': 32768,
}

# Completion cap per alias. Reasoning presets and the long-doc/batch tiers get
# 16384 because chain-of-thought tokens share the same max_tokens budget;
# interactive presets get 8192; nano stays small for instant replies.
max_tokens = {
    'qwen-fast': 8192,
    'qwen-think': 16384,
    'qwen-balanced': 8192,
    'qwen-quality': 8192,
    'qwen-long': 8192,
    'qwen-128k': 16384,
    'qwen-256k-batch': 16384,
    'devstral-coding': 8192,
    'qwen-dsh-fast': 8192,
    'qwen-dsh-low': 16384,
    'qwen-dsh-medium': 16384,
    'qwen-deepdive': 16384,
    'qwen-free': 16384,
    'nano': 2048,
}

for mid, ctx in contexts.items():
    c.execute('SELECT params FROM model WHERE id = ?', (mid,))
    row = c.fetchone()
    if not row:
        continue
    params = json.loads(row[0])
    params['context_length'] = ctx
    params['num_ctx'] = ctx
    params['max_tokens'] = max_tokens.get(mid, 4096)
    c.execute('UPDATE model SET params = ?, updated_at = ? WHERE id = ?',
              (json.dumps(params, ensure_ascii=False), now(), mid))
    print(f'updated {mid} context_length={ctx} num_ctx={ctx} max_tokens={params["max_tokens"]}')

conn.commit()
conn.close()
