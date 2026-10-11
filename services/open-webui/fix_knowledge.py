#!/usr/bin/env python3
"""Enable knowledge as a default feature for all Qwen / Devstral aliases."""

import json
import sqlite3
import time

DB = "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

def update_model(mid):
    c.execute("SELECT params, meta FROM model WHERE id = ?", (mid,))
    row = c.fetchone()
    if not row:
        print(f"skip {mid}: not found")
        return
    params = json.loads(row[0])
    meta = json.loads(row[1])

    # Enable knowledge in capabilities
    caps = meta.get("capabilities", {})
    caps["knowledge"] = True
    caps["builtin_tools"] = True
    meta["capabilities"] = caps

    # Enable knowledge as a default feature
    feats = meta.get("default_feature_ids", [])
    if "knowledge" not in feats:
        feats.append("knowledge")
    meta["default_feature_ids"] = feats

    # Enable the knowledge builtin tool
    tools = meta.get("builtinTools") or {}
    if isinstance(tools, dict):
        tools["knowledge"] = True
    meta["builtinTools"] = tools

    # Update system prompt to prefer knowledge for local/Jacko questions
    old_sys = params.get("system", "")
    if "query_knowledge_files" not in old_sys and "knowledge collections" in old_sys:
        params["system"] = old_sys.replace(
            "use the attached knowledge collections",
            "use the query_knowledge_files tool to search the attached knowledge collections"
        )
    elif "query_knowledge_files" not in old_sys:
        prepend = (
            "When the user asks about Jacko’s projects, hardware, preferences, background, "
            "or any local files/decisions/benchmarks, first use the query_knowledge_files tool "
            "to search the attached knowledge collections and cite sources with [id]. "
            "If the knowledge collections do not contain the answer, then use web search. "
        )
        params["system"] = prepend + old_sys

    c.execute(
        "UPDATE model SET params = ?, meta = ?, updated_at = ? WHERE id = ?",
        (json.dumps(params, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), now(), mid),
    )
    print(f"updated {mid}")

for mid in ["qwen-fast", "qwen-long", "qwen-think", "qwen-balanced", "qwen-quality", "devstral-coding"]:
    update_model(mid)

# Also enable knowledge in the global default metadata so new auto-detected models get it
c.execute("SELECT value FROM config WHERE key = 'models.default_metadata'")
row = c.fetchone()
if row:
    meta = json.loads(row[0])
    meta["capabilities"]["knowledge"] = True
    feats = meta.get("default_feature_ids", [])
    if "knowledge" not in feats:
        feats.append("knowledge")
    meta["default_feature_ids"] = feats
    c.execute(
        "UPDATE config SET value = ?, updated_at = ? WHERE key = 'models.default_metadata'",
        (json.dumps(meta, ensure_ascii=False), now()),
    )
    print("updated models.default_metadata")

conn.commit()
conn.close()
print("Knowledge fix complete.")
