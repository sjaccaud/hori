#!/usr/bin/env python3
"""Disable web_search capability for legacy Qwen models so they cannot trigger web search."""

import json
import sqlite3
import time

DB = "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

legacy_models = ["qwen-fast", "qwen-long", "qwen-think", "qwen-balanced"]

for mid in legacy_models:
    c.execute("SELECT params, meta FROM model WHERE id = ?", (mid,))
    row = c.fetchone()
    if not row:
        continue
    params = json.loads(row[0])
    meta = json.loads(row[1])

    # Disable web_search as a capability and default
    meta["capabilities"]["web_search"] = False
    if "web_search" in meta.get("default_feature_ids", []):
        meta["default_feature_ids"].remove("web_search")
    meta["default_feature_ids"] = list(set(meta.get("default_feature_ids", [])))

    # Update system prompt to focus on provided knowledge only
    params["system"] = (
        "You are an expert software engineering and research assistant. "
        "When knowledge from the attached collections is provided, use it as an authoritative source and cite it with [id]. "
        "When the user asks about Jacko’s projects, hardware, preferences, or background, use the attached knowledge collections and cite sources with [id]. "
        "Prefer concise, accurate, and well-reasoned answers. If a task is complex, think step by step before answering. "
        "Do not preface or qualify answers with meta-commentary such as \"This answer is based on general knowledge,\" \"The context does not contain...,\" or \"I am answering based on...\" unless the user explicitly asks for a source assessment. Just answer directly. "
        "Be concise: prefer short answers and avoid unnecessary lists or preamble."
    )

    c.execute(
        "UPDATE model SET params = ?, meta = ?, updated_at = ? WHERE id = ?",
        (json.dumps(params, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), now(), mid),
    )
    print(f"disabled web_search for {mid}")

conn.commit()
conn.close()
print("Web search disabled for legacy Qwen aliases.")
