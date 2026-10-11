#!/usr/bin/env python3
"""Remove web_search from default for legacy Qwen aliases and fix prompts."""

import json
import sqlite3
import time

DB = "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

# Restore the original plan-style prompt (no explicit tool call) for legacy models
legacy_prompt = (
    "You are an expert software engineering and research assistant. "
    "When web search results or knowledge from the attached collections are provided, use them as authoritative sources and cite them with [id]. "
    "When the user asks about Jacko’s projects, hardware, preferences, or background, use the attached knowledge collections and cite sources with [id]. "
    "Prefer concise, accurate, and well-reasoned answers. If a task is complex, think step by step before answering. "
    "Do not preface or qualify answers with meta-commentary such as \"This answer is based on general knowledge,\" \"The context does not contain...,\" or \"I am answering based on...\" unless the user explicitly asks for a source assessment. Just answer directly. "
    "Be concise: prefer short answers and avoid unnecessary lists or preamble."
)

# For native models, keep the explicit query_knowledge_files instruction
native_prompt = (
    "You are an expert software engineering, coding, and research assistant. "
    "When the user asks about Jacko’s projects, hardware, preferences, background, or any local files/decisions/benchmarks, "
    "first use the query_knowledge_files tool to search the attached knowledge collections and cite sources with [id]. "
    "If the knowledge collections do not contain the answer, then you may use web search. "
    "When web search results or memory context are provided, use them and cite with [id]. "
    "Be precise and avoid unnecessary preamble."
)

legacy_models = ["qwen-fast", "qwen-long", "qwen-think", "qwen-balanced"]
native_models = ["qwen-quality", "devstral-coding"]

for mid in legacy_models:
    c.execute("SELECT params, meta FROM model WHERE id = ?", (mid,))
    row = c.fetchone()
    if not row:
        continue
    params = json.loads(row[0])
    meta = json.loads(row[1])

    params["system"] = legacy_prompt
    # Remove web_search from default, keep knowledge + memory
    meta["default_feature_ids"] = ["knowledge", "memory"]
    # Keep web_search in capabilities so it can be toggled, but knowledge is default
    meta["capabilities"]["web_search"] = True
    meta["capabilities"]["knowledge"] = True
    meta["builtinTools"] = {"knowledge": True, "time": True}

    c.execute(
        "UPDATE model SET params = ?, meta = ?, updated_at = ? WHERE id = ?",
        (json.dumps(params, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), now(), mid),
    )
    print(f"updated {mid} (legacy, web_search off by default)")

for mid in native_models:
    c.execute("SELECT params, meta FROM model WHERE id = ?", (mid,))
    row = c.fetchone()
    if not row:
        continue
    params = json.loads(row[0])
    meta = json.loads(row[1])

    params["system"] = native_prompt
    # For native, keep web_search default but put knowledge first
    meta["default_feature_ids"] = ["knowledge", "web_search", "memory"]
    meta["capabilities"]["knowledge"] = True
    meta["capabilities"]["web_search"] = True
    meta["builtinTools"] = {"knowledge": True, "time": True}

    c.execute(
        "UPDATE model SET params = ?, meta = ?, updated_at = ? WHERE id = ?",
        (json.dumps(params, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), now(), mid),
    )
    print(f"updated {mid} (native, knowledge first)")

conn.commit()
conn.close()
print("Web search default fix complete.")
