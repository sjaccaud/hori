#!/usr/bin/env python3
"""Migrate Open-WebUI DB from old plan/act aliases to new qwen-* aliases."""

import json
import sqlite3
import time
from copy import deepcopy

DB = "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

def get_model(mid):
    c.execute("SELECT id, base_model_id, name, params, meta FROM model WHERE id = ?", (mid,))
    row = c.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "base_model_id": row[1],
        "name": row[2],
        "params": json.loads(row[3]),
        "meta": json.loads(row[4]),
    }

def insert_model(mid, base, name, params, meta):
    c.execute(
        "INSERT OR REPLACE INTO model (id, base_model_id, name, params, meta, updated_at, created_at, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, base, name, json.dumps(params, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), now(), now(), 1),
    )

def update_model(mid, base, name, params, meta):
    c.execute(
        "UPDATE model SET base_model_id = ?, name = ?, params = ?, meta = ?, updated_at = ? WHERE id = ?",
        (base, name, json.dumps(params, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), now(), mid),
    )

# Load old plan and act templates
plan = get_model("plan")
act = get_model("act")

if not plan or not act:
    raise RuntimeError("Could not find plan or act in model table")

# 1. Rename plan -> qwen-long, act -> devstral-coding
plan["id"] = "qwen-long"
plan["name"] = "qwen-long"
plan["meta"]["description"] = "Planning/reasoning profile — Qwen3.8-27B-IQ4_NL, 65k context, FP16 KV, draft-mtp"
plan["params"]["max_tokens"] = 8192
insert_model("qwen-long", None, "qwen-long", plan["params"], plan["meta"])

act["id"] = "devstral-coding"
act["name"] = "devstral-coding"
act["meta"]["description"] = "Coding/acting profile — Devstral-Small-2-24B-Instruct-2512-IQ4_NL, 32k context, FP16 KV, ngram-mod"
insert_model("devstral-coding", None, "devstral-coding", act["params"], act["meta"])

c.execute("DELETE FROM model WHERE id IN ('plan', 'act')")

# 2. Update derived models base_model_id
base_map = {
    "review": "devstral-coding",
    "writer": "qwen-long",
    "rocm-support": "qwen-long",
    "quick-plan": "qwen-fast",
}

for mid, new_base in base_map.items():
    m = get_model(mid)
    if not m:
        continue
    m["base_model_id"] = new_base
    # Update description to match new context
    if mid == "quick-plan":
        m["meta"]["description"] = "Quick planning profile — Qwen3.8-27B-IQ4_NL, 8k context, FP16 KV, draft-mtp"
        m["params"]["max_tokens"] = 4096
    update_model(mid, m["base_model_id"], m["name"], m["params"], m["meta"])

# 3. Create new qwen-* aliases

def qwen_fast_from_plan():
    m = deepcopy(plan)
    m["id"] = "qwen-fast"
    m["name"] = "qwen-fast"
    m["base_model_id"] = None
    m["meta"]["description"] = "Fast chat — Qwen3.8-27B-IQ4_NL, 8k context, FP16 KV, draft-mtp"
    m["params"]["max_tokens"] = 4096
    return m

def qwen_think_from_plan():
    m = deepcopy(plan)
    m["id"] = "qwen-think"
    m["name"] = "qwen-think"
    m["base_model_id"] = None
    m["meta"]["description"] = "Chain-of-thought reasoning — Qwen3.8-27B-IQ4_NL, 8k context, thinking enabled"
    m["params"]["max_tokens"] = 4096
    m["params"]["system"] = (
        "You are an expert reasoning assistant. Think step by step and show your reasoning clearly. "
        "When web search results or knowledge from the attached collections are provided, use them as authoritative sources and cite them with [id]. "
        "When the user asks about Jacko’s projects, hardware, preferences, or background, use the attached knowledge collections and cite sources with [id]. "
        "Be thorough but concise."
    )
    return m

def qwen_quality_from_act():
    # Start from act (coding) settings but use Qwen Q8_0 model
    m = deepcopy(act)
    m["id"] = "qwen-quality"
    m["name"] = "qwen-quality"
    m["base_model_id"] = None
    m["meta"]["description"] = "Highest-quality coding and reasoning — Qwen3.8-27B-Q8_0, 8k context, Q8_0 KV, draft-mtp"
    m["params"]["max_tokens"] = 4096
    m["params"]["temperature"] = 0.6
    m["params"]["system"] = (
        "You are an expert software engineering, coding, and research assistant. "
        "Write correct, idiomatic, and well-documented code. "
        "When web search results, memory context, or knowledge from the attached collections are provided, use them and cite with [id]. "
        "When the user asks about Jacko’s interests, projects, preferences, hardware, or background, you MUST use the query_knowledge_files tool to search the attached knowledge collections before answering. "
        "Be precise and avoid unnecessary preamble."
    )
    return m

def qwen_balanced_from_plan():
    m = deepcopy(plan)
    m["id"] = "qwen-balanced"
    m["name"] = "qwen-balanced"
    m["base_model_id"] = None
    m["meta"]["description"] = "Balanced quality/speed — Qwen3.8-27B-Q6_K, 8k context, FP16 KV"
    m["params"]["max_tokens"] = 4096
    return m

for builder in [qwen_fast_from_plan, qwen_think_from_plan, qwen_quality_from_act, qwen_balanced_from_plan]:
    m = builder()
    insert_model(m["id"], m["base_model_id"], m["name"], m["params"], m["meta"])

# 4. Update config table
config_updates = {
    "ui.default_models": "qwen-fast",
    "ui.default_pinned_models": "qwen-fast,qwen-long,qwen-quality,qwen-balanced,qwen-think,devstral-coding,quick-plan,writer,rocm-support,review",
    "ui.model_order_list": json.dumps(["qwen-fast", "qwen-long", "qwen-quality", "qwen-balanced", "qwen-think", "devstral-coding", "quick-plan", "writer", "rocm-support", "review"]),
    "task.model.default": "devstral-coding",
}

for key, value in config_updates.items():
    c.execute("UPDATE config SET value = ?, updated_at = ? WHERE key = ?", (value, now(), key))

conn.commit()
conn.close()
print("Migration complete.")
