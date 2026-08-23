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
    'qwen-fast': 16384,
    'qwen-think': 16384,
    'qwen-balanced': 16384,
    'qwen-quality': 16384,
    'qwen-long': 65536,
    'qwen-128k': 131072,
    'qwen-256k-batch': 262144,
    'devstral-coding': 32768,
}

for mid, ctx in contexts.items():
    c.execute('SELECT params FROM model WHERE id = ?', (mid,))
    row = c.fetchone()
    if not row:
        continue
    params = json.loads(row[0])
    params['context_length'] = ctx
    params['num_ctx'] = ctx
    c.execute('UPDATE model SET params = ?, updated_at = ? WHERE id = ?',
              (json.dumps(params, ensure_ascii=False), now(), mid))
    print(f'updated {mid} context_length / num_ctx to {ctx}')

conn.commit()
conn.close()
