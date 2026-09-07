#!/usr/bin/env python3
import sqlite3
import sys
import time

DB = sys.argv[1] if len(sys.argv) > 1 else "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

# Workspace models whose id matches the router alias must have base_model_id = NULL
# so Open-WebUI's get_all_models() overrides the base model instead of skipping it.
WORKSPACE_MODELS = [
    "qwen-fast",
    "qwen-long",
    "qwen-128k",
    "qwen-256k-batch",
    "qwen-think",
    "qwen-balanced",
    "qwen-quality",
    "devstral-coding",
    "qwen-dsh-fast",
    "qwen-dsh-low",
    "qwen-dsh-medium",
]

for mid in WORKSPACE_MODELS:
    c.execute("UPDATE model SET base_model_id = NULL, updated_at = ? WHERE id = ?", (now(), mid))
    print(f"updated {mid} base_model_id to NULL")

conn.commit()
conn.close()
