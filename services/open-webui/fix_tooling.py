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
    "qwen-dsh-fast",
    "qwen-dsh-low",
    "qwen-dsh-medium",
]

TOOL_USAGE = (
    "\n\nTool-use rules:\n"
    "- kb_exec is disabled; do not use it.\n"
    "- get_status ({USER_NAME} Local Tools) is the right tool for 'what is my system status?', 'are my services running?', or quick GPU/VRAM/disk summaries.\n"
    "- read_file and run_safe_command ({USER_NAME} Local Tools) are the right tools for inspecting the host filesystem and reading exact file content.\n"
    "- For project/directory surveys, 'list my projects', or questions that need current filesystem state, prefer run_safe_command (ls, tree, find, du, etc.) and read_file. "
    "Only call query_knowledge_files for conceptual/historical questions that are likely covered in the attached knowledge collections; do NOT call query_knowledge_files, view_knowledge_file, search_memories, search_chats, or search_notes for status questions.\n"
    "- For small known files, you may call read_file up to 4 times in parallel. For large outputs or exploration, issue one focused call, read the result, then decide the next step.\n"
    "- For questions about the CURRENT state of the system — downloads, files on disk, running services/processes, disk usage, or which models exist — you MUST call run_safe_command, read_file, or get_status BEFORE answering. "
    "Never answer current-state questions from attached knowledge or memory alone, even when it looks relevant; it may be stale.\n"
    "- The offline model archive lives at /mnt/4TB/model-archive (categories: llm/, vlm/, stt/, slm/, tts/, embedding/). For 'haul', 'stash', 'the downloads', or similar, check /mnt/4TB/model-archive and /opt/ai-models with run_safe_command (ls/find/du) first.\n"
    "- run_safe_command is shell-free: no pipes |, chaining ; &&, $(), or find -exec. Glob chars (* ? [ ]) in paths are expanded automatically. If a call fails, read the error and adjust once — do not retry the same shape.\n"
    "- Never paste raw tool output (JSON envelopes with ok/stdout/stderr fields, or unfiltered stdout dumps) into your reply. Summarize tool results as prose or a compact table.\n"
    "- Do not use web_search or fetch_url for local status questions, and do not ask the user to clarify when a tool call can resolve the ambiguity. Check first, then report what you found."
)

PLANNING_PROMPT = ((
    "You are a helpful planning and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
    "- query_knowledge_files: search attached knowledge collections for local docs, benchmarks, decisions, and {USER_NAME}'s notes.\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- get_status ({USER_NAME} Local Tools server): fetch a concise system status snapshot (services, GPU/VRAM, disk, loaded model).\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, lsusb, ps, df, systemctl, journalctl, dmesg, etc.\n\n"
    "When the user asks about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Use get_status for 'what is my system status?' or 'are my services running?'. "
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
    "- get_status ({USER_NAME} Local Tools server): fetch a concise system status snapshot (services, GPU/VRAM, disk, loaded model).\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, ps, df, etc.\n\n"
    "For questions about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Use get_status for 'what is my system status?' or 'are my services running?'. "
    "Only use web search if the user explicitly asks for external/fresh information or if local tools cannot answer.\n\n"
    "Be thorough but concise. Summarize long documents clearly. "
    "Do not output code unless the user explicitly asks for it."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

THINK_PROMPT = ((
    "You are a helpful reasoning and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
    "- query_knowledge_files: search attached knowledge collections for local docs, benchmarks, decisions, and {USER_NAME}'s notes.\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- get_status ({USER_NAME} Local Tools server): fetch a concise system status snapshot (services, GPU/VRAM, disk, loaded model).\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, ps, df, etc.\n\n"
    "For questions about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Use get_status for 'what is my system status?' or 'are my services running?'. "
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
    "- get_status ({USER_NAME} Local Tools server): fetch a concise system status snapshot (services, GPU/VRAM, disk, loaded model).\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, ps, df, etc.\n\n"
    "For questions about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Use get_status for 'what is my system status?' or 'are my services running?'. "
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
    "- get_status ({USER_NAME} Local Tools server): fetch a concise system status snapshot (services, GPU/VRAM, disk, loaded model).\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like ls, cat, grep, ps, df, systemctl, journalctl, dmesg, inxi, rocm-smi, etc.\n\n"
    "When the user asks about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Use get_status for 'what is my system status?' or 'are my services running?'. "
    "Read files directly with read_file or run_safe_command when you need exact content. "
    "Use query_knowledge_files for conceptual questions that may be covered in the knowledge collections.\n\n"
    "Only use web search if the user explicitly asks for external/fresh information (e.g. 'latest news', 'what does the internet say', 'current events'), "
    "or if local tools cannot answer after a genuine attempt. "
    "When web search results or knowledge are provided, cite them with [id].\n\n"
    "Be concise, accurate, and avoid unnecessary preamble. "
    "Do not preface answers with meta-commentary about sources unless the user asks."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

# Extra system-prompt rules appended for WEB_SEARCH_MODELS (defined below).
# These teach the model *when* to search and how to stay grounded — the
# local-model hallucination risk (invented URLs, answering from stale memory
# despite search results) is addressed here rather than by disabling the
# feature. No {USER_NAME} placeholders: appended to already-formatted prompts.
WEB_SEARCH_BEHAVIOR = (
    "\n\nWeb search rules (search_web / fetch_url tools):\n"
    "- Call search_web when the question depends on current, specific, or external facts: latest versions, releases, news, prices, or documentation you do not already have locally. Answer stable knowledge directly without searching.\n"
    "- After search_web, call fetch_url on the most relevant result URL when the snippet is not enough to answer confidently.\n"
    "- Ground the answer in what the tools actually return and cite the sources. If the results do not answer the question, say so explicitly — never invent URLs or fill gaps with guesses.\n"
    "- One or two searches is usually enough. If a search returns nothing useful, rephrase the query once, then stop and report what could not be verified.\n"
    "- run_safe_command is for inspecting THIS host only. Never use it (or curl/wget through it) to reach the web — all external requests go through search_web and fetch_url."
)

# Short display names for the Open-WebUI model selector (≤3 words —
# the long use-case names crowded the mobile UI). Enriched into
# /api/v1/models by patch_openai_model_names.py.
# Contract: test_web_search_pipeline.py::test_model_display_names_are_short.
MODEL_NAMES = {
    "qwen-fast": "Fast Chat",
    "qwen-long": "Long Docs",
    "qwen-128k": "Deep Plan",
    "qwen-256k-batch": "Batch Only",
    "qwen-think": "Think Mode",
    "qwen-balanced": "Balanced",
    "qwen-quality": "Max Quality",
    "devstral-coding": "Devstral Code",
    "qwen-dsh-fast": "Agentic Fast",
    "qwen-dsh-low": "Agentic Best",
    "qwen-dsh-medium": "Agentic Mid",
}

# Canonical selector order (ui.model_order_list): everyday chat first,
# then reasoning, long-context docs/planning, quality tiers, coding,
# agentic variants, and the non-interactive batch preset last.
MODEL_ORDER = [
    "qwen-fast",
    "qwen-think",
    "qwen-long",
    "qwen-128k",
    "qwen-balanced",
    "qwen-quality",
    "devstral-coding",
    "qwen-dsh-fast",
    "qwen-dsh-medium",
    "qwen-dsh-low",
    "qwen-256k-batch",
]

# Short use-case hints shown as selector subtitles / in Admin -> Models.
# Kept canonical here so `apply-model-config` restores them.
MODEL_DESCRIPTIONS = {
    "qwen-fast": "Everyday chat and quick planning — the fast default.",
    "qwen-long": "Long documents and deep analysis (65k ctx).",
    "qwen-128k": "Big planning, roadmaps, and audits (128k ctx).",
    "qwen-256k-batch": "Bulk ingestion only — not interactive.",
    "qwen-think": "Shows explicit step-by-step reasoning.",
    "qwen-balanced": "Quality/VRAM balance for code and research.",
    "qwen-quality": "Highest accuracy for high-stakes code (Q8_0).",
    "devstral-coding": "Coding and command execution (Devstral 24B).",
    "qwen-dsh-fast": "Agentic coding, no reasoning — fastest.",
    "qwen-dsh-low": "Agentic coding, light reasoning — best output.",
    "qwen-dsh-medium": "Agentic coding, medium reasoning — quick.",
}

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
    "qwen-dsh-fast": "native",
    "qwen-dsh-low": "native",
    "qwen-dsh-medium": "native",
}

# Anti-loop sampling: Qwen3.8's recommended presence_penalty for non-thinking
# mode is 1.5 (prevents the "I'll check..." narration loop where the model
# repeats intent text instead of emitting tool_calls). Coding aliases get 1.0
# (gentler on legitimately repetitive syntax); thinking models stay at 0.0 —
# penalizing already-seen tokens degrades long chain-of-thought. devstral and
# the oak-* models are left alone.
PRESENCE_PENALTY_BY_MODEL = {
    "qwen-fast": 1.5,
    "qwen-long": 1.5,
    "qwen-128k": 1.5,
    "qwen-256k-batch": 1.5,
    "qwen-quality": 1.0,
    "qwen-balanced": 1.0,
    "qwen-dsh-fast": 1.0,
    "qwen-think": 0.0,
    "qwen-dsh-low": 0.0,
    "qwen-dsh-medium": 0.0,
}

# Per-model prompts align with AGENTS.md usage:
# - qwen-fast: planning/chat
# - qwen-long/qwen-128k/qwen-256k-batch: long-document analysis
# - qwen-think: reasoning
# - qwen-balanced: balanced coding/research
# - qwen-quality/devstral-coding/qwen-dsh-*: coding with local tools
MODEL_PROMPTS = {
    "qwen-fast": PLANNING_PROMPT,
    "qwen-long": LONG_PROMPT,
    "qwen-128k": LONG_PROMPT,
    "qwen-256k-batch": LONG_PROMPT,
    "qwen-think": THINK_PROMPT,
    "qwen-balanced": BALANCED_PROMPT,
    "qwen-quality": CODING_PROMPT,
    "devstral-coding": CODING_PROMPT,
    "qwen-dsh-fast": CODING_PROMPT,
    "qwen-dsh-low": CODING_PROMPT,
    "qwen-dsh-medium": CODING_PROMPT,
}

# Per-model default features:
# - Web search and code interpreter are available (capabilities/builtinTools) but OFF by default
#   so the assistant doesn't call out to the internet or run code unless you explicitly toggle it.
# - Non-coding models: knowledge, time, memory, local tools
# - Coding/DSH models: also code_interpreter (toggleable, on by default for quick coding)
MODEL_DEFAULT_FEATURES = {
    "qwen-fast": ["knowledge", "time", "memory"],
    "qwen-long": ["knowledge", "time", "memory"],
    "qwen-128k": ["knowledge", "time", "memory"],
    "qwen-think": ["knowledge", "time", "memory"],
    "qwen-balanced": ["knowledge", "time", "memory"],
    "qwen-256k-batch": ["knowledge", "time", "memory"],
    "qwen-quality": ["knowledge", "time", "code_interpreter", "memory"],
    "devstral-coding": ["knowledge", "time", "code_interpreter", "memory"],
    "qwen-dsh-fast": ["knowledge", "time", "code_interpreter", "memory"],
    "qwen-dsh-low": ["knowledge", "time", "code_interpreter", "memory"],
    "qwen-dsh-medium": ["knowledge", "time", "code_interpreter", "memory"],
}

# Capabilities = what the UI is allowed to show/offer for a model.
# All toggles (web_search, code_interpreter, builtin_tools) are available by default.
META_CAPABILITIES = {
    "web_search": True,
    "knowledge": True,
    "memory": True,
    "builtin_tools": True,
    "code_interpreter": True,
    "time": True,
}

# Vision: these router aliases load the Qwen3.8 mmproj encoder (2026-09-06), so
# expose the image-attach UI. qwen-quality has no mmproj (Q8_0 + q8_0 KV leaves
# too little VRAM headroom); devstral-coding is text-only.
VISION_MODELS = {
    "qwen-fast",
    "qwen-long",
    "qwen-128k",
    "qwen-256k-batch",
    "qwen-think",
    "qwen-balanced",
    "qwen-dsh-fast",
    "qwen-dsh-low",
    "qwen-dsh-medium",
}

# Default feature toggles for auto-discovered / fallback models.
# Web search and code interpreter are OFF by default; the user toggles them when needed.
META_DEFAULT_FEATURES = ["knowledge", "time", "memory"]

# Models that get the builtin search_web / fetch_url tools (model decides when
# to search via native function calling). Piloted on qwen-dsh-low 2026-09-07,
# then armed for all interactive presets the same day. qwen-256k-batch stays
# unarmed: it is a non-interactive bulk-ingestion preset and should not wander
# the web mid-batch. Guardrails: WEB_SEARCH_BEHAVIOR prompt rules + the global
# CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS cap in docker-compose.yml.
# Test contract: services/integration_tests/test_web_search_pipeline.py.
WEB_SEARCH_MODELS = set(ALL_QWEN_DEVSTRAL) - {"qwen-256k-batch"}

# Builtin tool categories: Open-WebUI defaults EVERY unlisted category to True,
# so anything not explicitly disabled is injected into the tool list. Keep the
# useful read/knowledge tools; disable the workspace/social categories that only
# serve as tool-misfire bait on small local models (e.g. update_task loops).
# web_search stays OFF for all models except WEB_SEARCH_MODELS.
META_BUILTINTOOLS = {
    "knowledge": True,
    "time": True,
    "code_interpreter": True,
    "web_search": False,
    "memory": True,
    "files": True,
    "chats": True,
    "user_input": False,
    "subagents": False,
    "image_generation": False,
    "notes": False,
    "channels": False,
    "skills": False,
    "tasks": False,
    "automations": False,
    "calendar": False,
    "notifications": False,
}


def builtin_tools_for(model_id: str) -> dict:
    """Builtin-tool map for a model: META_BUILTINTOOLS plus per-model
    web_search enablement from WEB_SEARCH_MODELS."""
    tools = dict(META_BUILTINTOOLS)
    tools["web_search"] = model_id in WEB_SEARCH_MODELS
    return tools


def system_prompt_for(model_id: str, fallback: str = None) -> str:
    """System prompt for a model: MODEL_PROMPTS entry (or fallback) plus the
    WEB_SEARCH_BEHAVIOR rules for armed models."""
    base = MODEL_PROMPTS.get(model_id, fallback or PLANNING_PROMPT)
    if model_id in WEB_SEARCH_MODELS:
        return base + WEB_SEARCH_BEHAVIOR
    return base


def default_features_for(model_id: str) -> list:
    """default_feature_ids for a model: the per-model list plus 'web_search'
    for WEB_SEARCH_MODELS (the request must carry features.web_search for the
    builtin search_web tool to be registered — see tools.py gate)."""
    feats = list(MODEL_DEFAULT_FEATURES.get(model_id, META_DEFAULT_FEATURES))
    if model_id in WEB_SEARCH_MODELS and "web_search" not in feats:
        feats.append("web_search")
    return feats


# Attaches local tool servers via metadata['tool_ids'] using 'server:<id>' ids.
#
# Two distinct mechanisms in Open-WebUI 0.11.x:
#   - metadata['tool_ids'] + 'server:<id>'  -> get_tools() resolves against
#     app.state.TOOL_SERVERS and builds SERVER-SIDE callables (type='external',
#     direct=False) executed via HTTP to the tool server. THIS is what we want.
#   - metadata['tool_servers'] (server objects) -> registered as 'direct': True
#     tools executed over the browser websocket ('execute:tool' event). Direct
#     tools are for browser-side servers only; a server-side tool attached this
#     way fails at runtime with 'Tool Server Not Found'.
#
# So: always inject 'server:<id>' into tool_ids, never tool_servers.
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
            description="Comma-separated model ids to attach local tool servers to.",
        )
        server_tool_ids: str = Field(
            default="server:local-tools,server:project-tools",
            description="Comma-separated 'server:<id>' tool_ids to attach (server-side execution).",
        )
        debug: bool = Field(
            default=True,
            description="Write debug lines to /app/backend/data/tool_server_filter_debug.log",
        )

    def _dbg(self, msg: str) -> None:
        try:
            import time as _time
            with open("/app/backend/data/tool_server_filter_debug.log", "a") as f:
                f.write(str(int(_time.time())) + " " + msg + chr(10))
        except Exception:
            pass

    async def inlet(self, body: dict, __metadata__: dict = None, __request__=None, __user__: dict = None) -> dict:
        if not body:
            return body

        model_id = body.get("model")
        if not model_id:
            if self.valves and getattr(self.valves, "debug", False):
                self._dbg("no model in body; keys=" + ",".join(body.keys()))
            return body

        if self.valves and getattr(self.valves, "debug", False):
            # Diagnostic: show what builtinTools the running model object actually has.
            btm = "n/a"
            try:
                models = getattr(__request__.app.state, "MODELS", {}) if __request__ else {}
                model_info = models.get(model_id, {}) if hasattr(models, "get") else {}
                if isinstance(model_info, dict):
                    info = model_info.get("info", {})
                    meta = info.get("meta", {}) if isinstance(info, dict) else {}
                    btm = meta.get("builtinTools", {})
                    self._dbg(f"diagnostic model={model_id} builtinTools={btm}")
                else:
                    self._dbg(f"diagnostic model={model_id} type={type(model_info).__name__}")
            except Exception as e:
                self._dbg(f"diagnostic model={model_id} err={e}")

        allowed = {m.strip() for m in (self.valves.models or "").split(",") if m.strip()}
        if not allowed or model_id not in allowed:
            if self.valves and getattr(self.valves, "debug", False):
                self._dbg(f"skip model={model_id} (not in allowed)")
            return body

        try:
            metadata = body.get("metadata") or {}
            existing = metadata.get("tool_ids") or []
            if not isinstance(existing, list):
                existing = [existing]

            server_tool_ids = (
                self.valves.server_tool_ids
                if self.valves and self.valves.server_tool_ids
                else "server:local-tools"
            )

            for tool_id in {t.strip() for t in server_tool_ids.split(",") if t.strip()}:
                if tool_id not in existing:
                    existing.append(tool_id)

            metadata["tool_ids"] = existing
            body["metadata"] = metadata
            if __metadata__ is not None:
                __metadata__["tool_ids"] = existing

            # Open-WebUI rebuilds metadata['tool_ids'] from the TOP-LEVEL
            # 'tool_ids' key (form_data.pop + metadata.update), stomping
            # metadata-level injection — so inject at top level too, merging
            # any caller-selected tools.
            top_level = body.get("tool_ids") or []
            if not isinstance(top_level, list):
                top_level = [top_level]
            for tool_id in {t.strip() for t in server_tool_ids.split(",") if t.strip()}:
                if tool_id not in top_level:
                    top_level.append(tool_id)
            body["tool_ids"] = top_level

            log.info(
                "TOOL_SERVER_FILTER: injected %r for %r (tool_ids=%r)",
                server_tool_ids,
                model_id,
                existing,
            )
            if self.valves and getattr(self.valves, "debug", False):
                meta_keys = ",".join(sorted(metadata.keys()))
                self._dbg(
                    f"inject model={model_id} tool_ids={existing} meta_keys={meta_keys}"
                )
        except Exception as e:
            log.exception("TOOL_SERVER_FILTER: error %s", e)
            if self.valves and getattr(self.valves, "debug", False):
                self._dbg(f"ERROR model={model_id} {e}")

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

    params["system"] = system_prompt_for(target_id, LONG_PROMPT)
    params["function_calling"] = FUNCTION_CALLING_BY_MODEL.get(target_id, "native")
    params["max_tokens"] = 4096
    if target_id in PRESENCE_PENALTY_BY_MODEL:
        params["presence_penalty"] = PRESENCE_PENALTY_BY_MODEL[target_id]

    meta["description"] = description
    meta["capabilities"] = {
        **meta.get("capabilities", {}),
        **META_CAPABILITIES,
        **({"vision": True} if target_id in VISION_MODELS else {}),
    }
    meta["default_feature_ids"] = default_features_for(target_id)
    meta["builtinTools"] = {**meta.get("builtinTools", {}), **builtin_tools_for(target_id)}

    c.execute(
        "INSERT OR REPLACE INTO model (id, user_id, base_model_id, name, params, meta, updated_at, created_at, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            target_id,
            user_id,
            None,  # NULL: workspace id matches the base router alias, so override it
            MODEL_NAMES.get(target_id, target_id),
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
        ("qwen-128k", "Planning/long-doc analysis — Qwen3.8-27B-UD-Q4_K_M (Dynamic v3.0), 128k context, q4_0 KV, draft-mtp, vision"),
        ("qwen-256k-batch", "Batch ingestion only — Qwen3.8-27B-UD-Q4_K_M (Dynamic v3.0), 256k context, q4_0 KV, no speculative decode, vision"),
    ]:
        exists = c.execute("SELECT 1 FROM model WHERE id = ?", (target,)).fetchone()
        if not exists:
            _clone_qwen_long(c, "qwen-long", target, desc)
            print(f"created model {target}")
        else:
            print(f"model {target} already exists")

    # 1. Update tool_server connection function_name_filter_list
    project_tools_conn = {
        "url": "http://127.0.0.1:8769",
        "config": {
            "enable": True,
            "function_name_filter_list": "propose_create_project,confirm_create_project,rollback_project",
        },
        "info": {"id": "project-tools", "name": "Project Tools"},
    }
    row = c.execute("SELECT value FROM config WHERE key='tool_server.connections'").fetchone()
    if row:
        conns = json.loads(row[0])
        # Ensure project-tools is present and local-tools is clean.
        urls = {conn_cfg.get('url', '') for conn_cfg in conns}
        if project_tools_conn['url'] not in urls:
            conns.append(project_tools_conn)
        for conn_cfg in conns:
            url = conn_cfg.get('url', '')
            if url == 'http://127.0.0.1:8766':
                conn_cfg['config'] = conn_cfg.get('config', {})
                conn_cfg['config']['enable'] = True
                conn_cfg['config']['function_name_filter_list'] = 'read_file,run_safe_command,get_status'
                conn_cfg['info'] = conn_cfg.get('info', {})
                conn_cfg['info']['id'] = 'local-tools'
                conn_cfg['info']['name'] = 'Local Tools'
            elif url == 'http://127.0.0.1:8769':
                conn_cfg['config'] = conn_cfg.get('config', {})
                conn_cfg['config']['enable'] = True
                conn_cfg['config']['function_name_filter_list'] = 'propose_create_project,confirm_create_project,rollback_project'
                conn_cfg['info'] = conn_cfg.get('info', {})
                conn_cfg['info']['id'] = 'project-tools'
                conn_cfg['info']['name'] = 'Project Tools'
            elif url in ('http://127.0.0.1:8767', 'http://127.0.0.1:8768'):
                # OaK tool servers are not running; keep them disabled to reduce bloat.
                conn_cfg.setdefault('config', {})['enable'] = False
                print(f"disabled tool server {conn_cfg.get('info', {}).get('id', url)}")
        c.execute(
            "UPDATE config SET value=?, updated_at=? WHERE key=?",
            (json.dumps(conns, ensure_ascii=False), now(), 'tool_server.connections'),
        )
        print('updated tool_server.connections')
    else:
        # Bootstrap tool_server.connections with the two active servers.
        c.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            (
                'tool_server.connections',
                json.dumps(
                    [
                        {
                            "url": "http://127.0.0.1:8766",
                            "config": {
                                "enable": True,
                                "function_name_filter_list": "read_file,run_safe_command,get_status",
                            },
                            "info": {"id": "local-tools", "name": "Local Tools"},
                        },
                        project_tools_conn,
                    ],
                    ensure_ascii=False,
                ),
                now(),
            ),
        )
        print('created tool_server.connections')

    # 1. Update per-model params + meta
    for mid in ALL_QWEN_DEVSTRAL:
        row = c.execute("SELECT params, meta FROM model WHERE id = ?", (mid,)).fetchone()
        if not row:
            print(f"skip {mid}: not in model table")
            continue

        params = json.loads(row[0]) if row[0] else {}
        meta = json.loads(row[1]) if row[1] else {}

        # Set per-model prompt, function_calling, and limits
        params["system"] = system_prompt_for(mid)
        params["function_calling"] = FUNCTION_CALLING_BY_MODEL.get(mid, "native")
        params["max_tokens"] = 4096
        # All Qwen/Devstral presets can use local tools; let them run read-only tools
        # without a per-call prompt, but web search and code interpreter still need the
        # user to toggle them on per chat.
        params["tool_approval_mode"] = "full"
        if mid in PRESENCE_PENALTY_BY_MODEL:
            params["presence_penalty"] = PRESENCE_PENALTY_BY_MODEL[mid]

        # Apply meta overrides
        meta["capabilities"] = {
            **meta.get("capabilities", {}),
            **META_CAPABILITIES,
            **({"vision": True} if mid in VISION_MODELS else {}),
        }
        meta["default_feature_ids"] = default_features_for(mid)
        meta["builtinTools"] = {**meta.get("builtinTools", {}), **builtin_tools_for(mid)}

        # Canonical use-case descriptions (selector subtitle / admin list)
        meta["description"] = MODEL_DESCRIPTIONS.get(mid) or meta.get(
            "description", f"Local model {mid}"
        )

        c.execute(
            "UPDATE model SET name = ?, params = ?, meta = ?, updated_at = ? WHERE id = ?",
            (
                MODEL_NAMES.get(mid, mid),
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
                    "server_tool_ids": "server:local-tools,server:project-tools",
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
    # Default for any new/fallback model: assistant-like, local tools on, web search/code interpreter OFF.
    default_meta = {
        "capabilities": META_CAPABILITIES,
        "default_feature_ids": META_DEFAULT_FEATURES,
        "builtinTools": META_BUILTINTOOLS,
    }
    default_params = {
        "function_calling": "native",
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

    # The standalone TTS server on :8765 was removed 2026-09-06; leave the
    # engine unset so Open-WebUI uses the browser Web Speech API instead of
    # probing a dead OpenAI-compatible endpoint (recurring "Server Connection
    # Error" toasts).
    c.execute(
        "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
        (json.dumps(""), now(), "audio.tts.engine"),
    )
    print("reset audio.tts.engine to default (Web Speech API)")

    # 5. Keep the UI model list and pinned list current.
    # Pin the everyday models; DSH variants are available but collapsed to reduce UI clutter.
    pinned = [
        "qwen-fast",
        "qwen-long",
        "qwen-128k",
        "qwen-think",
        "qwen-balanced",
        "qwen-quality",
        "devstral-coding",
        "qwen-dsh-medium",
        "qwen-256k-batch",
    ]
    c.execute(
        "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
        (json.dumps(pinned, ensure_ascii=False), now(), "ui.default_pinned_models"),
    )
    c.execute(
        "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
        (json.dumps(MODEL_ORDER, ensure_ascii=False), now(), "ui.model_order_list"),
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
