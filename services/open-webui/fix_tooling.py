#!/usr/bin/env python3
"""Fix Open-WebUI model metadata, filters, and defaults for local-first tooling."""

import json
import os
import sqlite3
import sys
import time

DB = sys.argv[1] if len(sys.argv) > 1 else "/app/backend/data/webui.db"

# User-specific values are injected at runtime so the script stays generic.
USER = os.environ.get("USER", "the user")
USER_NAME = os.environ.get("OPENWEBUI_USER_NAME", USER)
USER_HOME = os.environ.get("HOME", "/home/" + USER)
ALL_QWEN_DEVSTRAL = [
    "qwen-fast",
    "qwen-long",
    "qwen-think",
    "qwen-balanced",
    "qwen-quality",
    "devstral-coding",
    "qwen-128k",
    "qwen-256k-batch",
]

TOOL_USAGE = (
    "\n\nTool-use rules:\n"
    "- kb_exec is disabled; do not use it.\n"
    "- read_file and run_safe_command ({USER_NAME} Local Tools) are the right tools for inspecting the host filesystem and reading exact file content.\n"
    "- For project/directory surveys, 'list my projects', or questions that need current filesystem state, prefer run_safe_command (ls, tree, find, etc.) and read_file. "
    "Only call query_knowledge_files for conceptual/historical questions that are likely covered in the attached knowledge collections.\n"
    "- Do not issue multiple parallel read_file/run_safe_command calls with huge outputs. Issue one focused command, read the result, then decide the next step."
)

PLANNING_PROMPT = ((
    "You are a helpful planning and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
    "- query_knowledge_files: search attached knowledge collections for local docs, benchmarks, decisions, and {USER_NAME}'s notes.\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, lsusb, ps, df, systemctl, journalctl, dmesg, etc.\n\n"
    "When the user asks about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Use query_knowledge_files for conceptual questions that may be covered in the knowledge collections, "
    "and read_file or run_safe_command when you need exact current system values.\n\n"
    "Only use web search if the user explicitly asks for external/fresh information (e.g. 'latest news', 'what does the internet say', 'current events'), "
    "or if local tools cannot answer after a genuine attempt. "
    "When web search results or knowledge are provided, cite them with [id].\n\n"
    "Be concise, accurate, and avoid unnecessary preamble. "
    "Do not output code unless the user explicitly asks for it."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

LONG_PROMPT = ((
    "You are a helpful document analysis and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
    "- query_knowledge_files: search attached knowledge collections for local docs, benchmarks, decisions, and {USER_NAME}'s notes.\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, ps, df, etc.\n\n"
    "For questions about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Only use web search if the user explicitly asks for external/fresh information or if local tools cannot answer.\n\n"
    "Be thorough but concise. Summarize long documents clearly. "
    "Do not output code unless the user explicitly asks for it."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

THINK_PROMPT = ((
    "You are a helpful reasoning and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
    "- query_knowledge_files: search attached knowledge collections for local docs, benchmarks, decisions, and {USER_NAME}'s notes.\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, ps, df, etc.\n\n"
    "For questions about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Only use web search if the user explicitly asks for external/fresh information or if local tools cannot answer.\n\n"
    "Think step by step and show your reasoning clearly when the question benefits from it. "
    "Be concise, accurate, and avoid unnecessary preamble. "
    "Do not output code unless the user explicitly asks for it."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

BALANCED_PROMPT = ((
    "You are a helpful software engineering and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
    "- query_knowledge_files: search attached knowledge collections for local docs, benchmarks, decisions, and {USER_NAME}'s notes.\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, ps, df, etc.\n\n"
    "For questions about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Only use web search if the user explicitly asks for external/fresh information or if local tools cannot answer.\n\n"
    "Be concise, accurate, and avoid unnecessary preamble. "
    "You may include small code examples only when directly relevant to the user's request."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

CODING_PROMPT = ((
    "You are an expert software engineering, coding, and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
    "- query_knowledge_files: search attached knowledge collections for local docs, benchmarks, decisions, and {USER_NAME}'s notes.\n"
    "- execute_code: run Python/shell code in the Open-WebUI sandbox for calculations and small transformations.\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like ls, cat, grep, ps, df, systemctl, journalctl, dmesg, inxi, rocm-smi, etc.\n\n"
    "When the user asks about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Read files directly with read_file or run_safe_command when you need exact content. "
    "Use query_knowledge_files for conceptual questions that may be covered in the knowledge collections.\n\n"
    "Only use web search if the user explicitly asks for external/fresh information (e.g. 'latest news', 'what does the internet say', 'current events'), "
    "or if local tools cannot answer after a genuine attempt. "
    "When web search results or knowledge are provided, cite them with [id].\n\n"
    "Be concise, accurate, and avoid unnecessary preamble. "
    "Do not preface answers with meta-commentary about sources unless the user asks."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

# function_calling: Qwen3.8-27B (with --jinja) emits OpenAI-compatible tool_calls,
# so use native for all Qwen aliases. devstral-coding also uses native.
FUNCTION_CALLING_BY_MODEL = {
    "qwen-fast": "native",
    "qwen-long": "native",
    "qwen-think": "native",
    "qwen-balanced": "native",
    "qwen-128k": "native",
    "qwen-256k-batch": "native",
    "qwen-quality": "native",
    "devstral-coding": "native",
}

# Per-model prompts align with AGENTS.md usage:
# - qwen-fast: planning/chat
# - qwen-long/qwen-128k/qwen-256k-batch: long-document analysis
# - qwen-think: reasoning
# - qwen-balanced: balanced coding/research
# - qwen-quality/devstral-coding: coding with local tools
MODEL_PROMPTS = {
    "qwen-fast": PLANNING_PROMPT,
    "qwen-long": LONG_PROMPT,
    "qwen-128k": LONG_PROMPT,
    "qwen-256k-batch": LONG_PROMPT,
    "qwen-think": THINK_PROMPT,
    "qwen-balanced": BALANCED_PROMPT,
    "qwen-quality": CODING_PROMPT,
    "devstral-coding": CODING_PROMPT,
}

# Per-model default features:
# - fast/long/think/balanced/128k: knowledge, time, web_search, memory (no code by default)
# - qwen-256k-batch: batch, no web search
# - quality/devstral: knowledge, time, code_interpreter, memory, web_search
MODEL_DEFAULT_FEATURES = {
    "qwen-fast": ["knowledge", "time", "web_search", "memory"],
    "qwen-long": ["knowledge", "time", "web_search", "memory"],
    "qwen-128k": ["knowledge", "time", "web_search", "memory"],
    "qwen-think": ["knowledge", "time", "web_search", "memory"],
    "qwen-balanced": ["knowledge", "time", "web_search", "memory"],
    "qwen-256k-batch": ["knowledge", "time", "memory"],
    "qwen-quality": ["knowledge", "time", "code_interpreter", "memory", "web_search"],
    "devstral-coding": ["knowledge", "time", "code_interpreter", "memory", "web_search"],
}

META_CAPABILITIES = {
    "web_search": True,       # toggleable, but not in default features
    "knowledge": True,
    "memory": True,
    "builtin_tools": True,
    "code_interpreter": True,
    "time": True,
}

META_DEFAULT_FEATURES = ["knowledge", "time", "code_interpreter", "memory"]

META_BUILTINTOOLS = {
    "knowledge": True,
    "time": True,
    "code_interpreter": True,
    "web_search": True,       # only injected when user toggles it
}

TOOL_SERVER_FILTER_SOURCE = '''
from pydantic import BaseModel, Field
import logging

log = logging.getLogger(__name__)


class Filter:
    def __init__(self):
        self.valves = None

    class Valves(BaseModel):
        models: str = Field(
            default="qwen-fast,qwen-long,qwen-think,qwen-balanced,qwen-quality,devstral-coding,qwen-128k,qwen-256k-batch",
            description="Comma-separated model ids to inject the local tool server into.",
        )
        server_tool_id: str = Field(
            default="server:local-tools",
            description="Tool ID used by Open-WebUI to resolve the server via get_tools() (server-side execution).",
        )

    async def inlet(self, body: dict, __metadata__: dict = None, __request__=None, __user__: dict = None) -> dict:
        if not body:
            return body

        model_id = body.get("model")
        if not model_id:
            return body

        allowed = {m.strip() for m in (self.valves.models or "").split(",") if m.strip()}
        if not allowed or model_id not in allowed:
            return body

        try:
            metadata = body.get("metadata") or {}
            existing = metadata.get("tool_ids") or []
            if not isinstance(existing, list):
                existing = [existing]

            server_tool_id = (
                self.valves.server_tool_id
                if self.valves and self.valves.server_tool_id
                else "server:local-tools"
            )

            if server_tool_id not in existing:
                existing.append(server_tool_id)

            metadata["tool_ids"] = existing
            body["metadata"] = metadata
            if __metadata__ is not None:
                __metadata__["tool_ids"] = existing

            log.info(
                "TOOL_SERVER_FILTER: injected %r for %r (tool_ids=%r)",
                server_tool_id,
                model_id,
                existing,
            )
        except Exception as e:
            log.exception("TOOL_SERVER_FILTER: error %s", e)

        return body
'''


def now() -> int:
    return int(time.time())


def _clone_qwen_long(c, source_id: str, target_id: str, description: str):
    """Create a new Open-WebUI model record by cloning the qwen-long template."""
    user_id = "db2358ef-3324-4bf9-95b1-0e2c8a714fad"
    row = c.execute(
        "SELECT id, base_model_id, params, meta FROM model WHERE id = ?", (source_id,)
    ).fetchone()
    if not row:
        raise RuntimeError(f"Cannot clone from missing source model {source_id}")

    params = json.loads(row[2]) if row[2] else {}
    meta = json.loads(row[3]) if row[3] else {}

    params["system"] = MODEL_PROMPTS.get(target_id, LONG_PROMPT)
    params["function_calling"] = FUNCTION_CALLING_BY_MODEL.get(target_id, "native")
    params["max_tokens"] = 4096

    meta["description"] = description
    meta["capabilities"] = {**meta.get("capabilities", {}), **META_CAPABILITIES}
    meta["default_feature_ids"] = MODEL_DEFAULT_FEATURES.get(
        target_id, ["knowledge", "time", "web_search", "memory"]
    )
    meta["builtinTools"] = {**meta.get("builtinTools", {}), **META_BUILTINTOOLS}

    c.execute(
        "INSERT OR REPLACE INTO model (id, user_id, base_model_id, name, params, meta, updated_at, created_at, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            target_id,
            user_id,
            target_id,
            target_id,
            json.dumps(params, ensure_ascii=False),
            json.dumps(meta, ensure_ascii=False),
            now(),
            now(),
            1,
        ),
    )


def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # 0. Ensure the long-context Qwen aliases exist before tooling fix runs.
    for target, desc in [
        ("qwen-128k", "Planning/long-doc analysis — Qwen3.8-27B-IQ4_NL, 128k context, q4_0 KV, draft-mtp"),
        ("qwen-256k-batch", "Batch ingestion only — Qwen3.8-27B-IQ4_NL, 256k context, q4_0 KV, no speculative decode"),
    ]:
        exists = c.execute("SELECT 1 FROM model WHERE id = ?", (target,)).fetchone()
        if not exists:
            _clone_qwen_long(c, "qwen-long", target, desc)
            print(f"created model {target}")
        else:
            print(f"model {target} already exists")

    # 1. Update tool_server connection function_name_filter_list
    row = c.execute("SELECT value FROM config WHERE key='tool_server.connections'").fetchone()
    if row:
        conns = json.loads(row[0])
        for conn_cfg in conns:
            if conn_cfg.get('url') == 'http://127.0.0.1:8766':
                conn_cfg['config'] = conn_cfg.get('config', {})
                conn_cfg['config']['enable'] = True
                conn_cfg['config']['function_name_filter_list'] = 'read_file,run_safe_command'
                conn_cfg['info'] = conn_cfg.get('info', {})
                conn_cfg['info']['id'] = 'local-tools'
                conn_cfg['info']['name'] = 'Local Tools'
        c.execute(
            "UPDATE config SET value=?, updated_at=? WHERE key=?",
            (json.dumps(conns, ensure_ascii=False), now(), 'tool_server.connections'),
        )
        print('updated tool_server.connections')

    # 1. Update per-model params + meta
    for mid in ALL_QWEN_DEVSTRAL:
        row = c.execute("SELECT params, meta FROM model WHERE id = ?", (mid,)).fetchone()
        if not row:
            print(f"skip {mid}: not in model table")
            continue

        params = json.loads(row[0]) if row[0] else {}
        meta = json.loads(row[1]) if row[1] else {}

        # Set per-model prompt, function_calling, and limits
        params["system"] = MODEL_PROMPTS.get(mid, PLANNING_PROMPT)
        params["function_calling"] = FUNCTION_CALLING_BY_MODEL.get(mid, "legacy")
        params["max_tokens"] = 4096

        # Apply meta overrides
        meta["capabilities"] = {**meta.get("capabilities", {}), **META_CAPABILITIES}
        meta["default_feature_ids"] = MODEL_DEFAULT_FEATURES.get(
            mid, ["knowledge", "time", "web_search", "memory"]
        )
        meta["builtinTools"] = {**meta.get("builtinTools", {}), **META_BUILTINTOOLS}

        # Preserve description if present, add generic if missing
        if "description" not in meta:
            if mid == "devstral-coding":
                meta["description"] = "Coding assistant — Devstral-Small-2-24B, 32k ctx, ngram-mod"
            elif mid == "qwen-quality":
                meta["description"] = "Highest-quality Qwen3.8-27B — Q8_0, 16k ctx, draft-mtp"
            else:
                meta["description"] = f"Local model {mid}"

        c.execute(
            "UPDATE model SET params = ?, meta = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(params, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
                now(),
                mid,
            ),
        )
        print(f"updated model {mid}")

    # 2. Update the tool server filter source + make it global.
    # Resolve the actual filter ids from the DB so the script stays PII-free.
    tool_server_filter_id = c.execute(
        "SELECT id FROM function WHERE type = 'filter' AND id LIKE '%tool_server_filter' AND is_global = 1"
    ).fetchone()
    local_first_filter_id = c.execute(
        "SELECT id FROM function WHERE type = 'filter' AND id LIKE '%local_first_filter' AND is_global = 1"
    ).fetchone()
    if not tool_server_filter_id or not local_first_filter_id:
        raise RuntimeError("Could not find the global tool-server or local-first filter in the DB")

    # Use the real server tool id from the tool_server.connections config when available.
    server_tool_id = "server:local-tools"
    row = c.execute("SELECT value FROM config WHERE key = 'tool_server.connections'").fetchone()
    if row:
        conns = json.loads(row[0])
        for conn_cfg in conns:
            if conn_cfg.get("url") == "http://127.0.0.1:8766":
                server_tool_id = "server:" + conn_cfg.get("info", {}).get("id", "local-tools")
                break

    c.execute(
        "UPDATE function SET content = ?, is_global = 1, valves = ?, updated_at = ? WHERE id = ?",
        (
            TOOL_SERVER_FILTER_SOURCE.strip(),
            json.dumps(
                {
                    "models": ",".join(ALL_QWEN_DEVSTRAL),
                    "server_tool_id": server_tool_id,
                },
                ensure_ascii=False,
            ),
            now(),
            tool_server_filter_id[0],
        ),
    )
    print(f"updated {tool_server_filter_id[0]}")

    # 3. Update the local-first filter valves to all current models
    c.execute(
        "UPDATE function SET valves = ?, updated_at = ? WHERE id = ?",
        (
            json.dumps({"models": ",".join(ALL_QWEN_DEVSTRAL)}, ensure_ascii=False),
            now(),
            local_first_filter_id[0],
        ),
    )
    print(f"updated {local_first_filter_id[0]}")

    # 4. Update global default metadata / params for auto-discovered models
    # Global defaults for auto-discovered or fallback models: planning/chat.
    default_meta = {
        "capabilities": META_CAPABILITIES,
        "default_feature_ids": ["knowledge", "time", "web_search", "memory"],
        "builtinTools": META_BUILTINTOOLS,
    }
    default_params = {
        "function_calling": "legacy",
        "max_tokens": 4096,
        "temperature": 0.7,
        "system": PLANNING_PROMPT,
    }

    c.execute(
        "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
        (json.dumps(default_meta, ensure_ascii=False), now(), "models.default_metadata"),
    )
    c.execute(
        "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
        (json.dumps(default_params, ensure_ascii=False), now(), "models.default_params"),
    )
    print("updated models.default_metadata and models.default_params")

    # 5. Keep the UI model list and pinned list current.
    pinned = [
        "qwen-fast",
        "qwen-long",
        "qwen-128k",
        "qwen-quality",
        "qwen-balanced",
        "qwen-think",
        "devstral-coding",
        "qwen-256k-batch",
    ]
    c.execute(
        "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
        (json.dumps(pinned, ensure_ascii=False), now(), "ui.default_pinned_models"),
    )
    c.execute(
        "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
        (json.dumps(pinned, ensure_ascii=False), now(), "ui.model_order_list"),
    )
    print("updated ui.default_pinned_models and ui.model_order_list")

    # 6. Normalize all config.value rows to valid JSON text. Open-WebUI's SQLAlchemy
    # model expects the value column to be JSON; integer/real/CSV values cause a
    # JSONDecodeError at startup. This is a defensive fix for config drift.
    c.execute("SELECT key, value FROM config")
    for key, val in c.fetchall():
        if val is None:
            continue
        if isinstance(val, str):
            if val == "":
                new_val = json.dumps("")
            else:
                try:
                    json.loads(val)
                    continue
                except json.JSONDecodeError:
                    # If it looks like a comma-separated list, convert to a JSON array;
                    # otherwise treat the whole string as a JSON string.
                    if "," in val and "\n" not in val:
                        new_val = json.dumps(
                            [x.strip() for x in val.split(",") if x.strip()],
                            ensure_ascii=False,
                        )
                    else:
                        new_val = json.dumps(val, ensure_ascii=False)
                c.execute(
                    "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
                    (new_val, now(), key),
                )
        else:
            new_val = json.dumps(val, ensure_ascii=False)
            c.execute(
                "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
                (new_val, now(), key),
            )
    print("normalized config.value JSON types")

    conn.commit()
    conn.close()
    print("Open-WebUI tooling fix complete.")


if __name__ == "__main__":
    main()
