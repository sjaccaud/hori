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

contexts = {
    'qwen-fast': 32768,
    'qwen-think': 16384,
    'qwen-balanced': 16384,
    'qwen-quality': 16384,
    'qwen-long': 65536,
    'qwen-128k': 131072,
    'qwen-256k-batch': 262144,
    'devstral-coding': 32768,
    'qwen-dsh-fast': 32768,
    'qwen-dsh-low': 32768,
    'qwen-dsh-medium': 32768,
}

# Long-context models get more room to summarize/emit plans; interactive/coding models stay concise.
max_tokens = {
    'qwen-fast': 4096,
    'qwen-think': 4096,
    'qwen-balanced': 4096,
    'qwen-quality': 4096,
    'qwen-long': 8192,
    'qwen-128k': 8192,
    'qwen-256k-batch': 8192,
    'devstral-coding': 4096,
    'qwen-dsh-fast': 4096,
    'qwen-dsh-low': 4096,
    'qwen-dsh-medium': 4096,
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
