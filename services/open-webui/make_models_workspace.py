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

for mid in ['qwen-fast', 'qwen-long', 'qwen-128k', 'qwen-256k-batch', 'qwen-think', 'qwen-balanced', 'qwen-quality', 'devstral-coding']:
    # Set base_model_id to the same mid so Open-WebUI treats these as workspace/derived models
    # whose meta (default_feature_ids, capabilities, system prompt) is used by the UI.
    c.execute('UPDATE model SET base_model_id = ?, updated_at = ? WHERE id = ?', (mid, now(), mid))
    print(f'updated {mid} base_model_id to {mid}')

conn.commit()
conn.close()
