#!/usr/bin/env python3
"""Sync derived Open-WebUI models with their base model settings."""

import json
import sqlite3
import time

DB = "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

c.execute("SELECT id, base_model_id, name, params, meta FROM model")
rows = c.fetchall()

for mid, base, name, params_str, meta_str in rows:
    if not base:
        continue
    # Get base model
    c.execute("SELECT params, meta FROM model WHERE id = ?", (base,))
    base_row = c.fetchone()
    if not base_row:
        print(f"skip {mid}: base {base} not found")
        continue
    base_params = json.loads(base_row[0])
    base_meta = json.loads(base_row[1])

    # Merge: keep derived model's name/description/knowledge? No, sync relevant fields
    params = json.loads(params_str)
    meta = json.loads(meta_str)

    # Sync function_calling, temperature, max_tokens from base
    for key in ["function_calling", "temperature", "max_tokens", "top_p", "top_k", "presence_penalty", "frequency_penalty"]:
        if key in base_params:
            params[key] = base_params[key]

    # Sync system prompt from base
    if "system" in base_params:
        params["system"] = base_params["system"]

    # Sync capabilities/default_feature_ids from base, but keep derived-specific extras
    meta["capabilities"] = base_meta.get("capabilities", {})
    meta["default_feature_ids"] = base_meta.get("default_feature_ids", [])
    meta["builtinTools"] = base_meta.get("builtinTools", {})

    # Also update knowledge list to include Local Benchmarks if base has it
    meta["knowledge"] = base_meta.get("knowledge", [])

    c.execute(
        "UPDATE model SET params = ?, meta = ?, updated_at = ? WHERE id = ?",
        (json.dumps(params, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), now(), mid),
    )
    print(f"synced {mid} (base {base})")

conn.commit()
conn.close()
print("Derived models synced.")
