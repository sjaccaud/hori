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
    "qwen-think",
    "qwen-balanced",
    "qwen-quality",
    "devstral-coding",
    "qwen-128k",
    "qwen-256k-batch",
    "qwen-dsh-fast",
    "qwen-dsh-low",
    "qwen-dsh-medium",
    "qwen-deepdive",
    "qwen-free",
]

TOOL_USAGE = (
    "\n\nTool-use rules:\n"
    "- kb_exec is disabled; do not use it.\n"
    "- get_status ({USER_NAME} Local Tools) is the right tool for 'what is my system status?', 'are my services running?', or quick GPU/VRAM/disk summaries.\n"
    "- read_file and run_safe_command ({USER_NAME} Local Tools) are the right tools for inspecting the host filesystem and reading exact file content.\n"
    "- For project/directory surveys, 'list my projects', or questions that need current filesystem state, prefer run_safe_command (ls, tree, find, du, etc.) and read_file. "
    "For workstream/project state — 'where did we leave X', 'what is open on Y', recent decisions — the hori-state tools (thread_list, thread_read, episode_search) are the authoritative source; prefer them over guessing.\n"
    "- For small known files, you may call read_file up to 4 times in parallel. For large outputs or exploration, issue one focused call, read the result, then decide the next step.\n"
    "- read_file auto-summarizes files larger than its max_bytes cap via the local nano model (the result has summarized=true); pass summarize=false or an explicit max_bytes when you need exact raw content.\n"
    "- youtube_transcript ({USER_NAME} Local Tools) fetches a YouTube video's captions as text — use it for any youtube.com/youtu.be link the user shares or references; do not try transcript-service websites or guess at video content.\n"
    "- Workspace write tools (when attached) are confirmation-gated: call "
    "propose_workspace_write with relative paths under the configured Projects "
    "workspace, show the manifest to the user, and only call "
    "confirm_workspace_write after explicit user approval. Directories and .md "
    "files only; no overwrite or delete.\n"
    "- For questions about the CURRENT state of the system — running services, loaded model, workstreams, GPU/VRAM/disk — an 'AIOS state snapshot' is injected into your context and ALREADY answers them; cite it directly. Call get_status or run_safe_command only for live detail the snapshot does not cover or when its freshness matters.\n"
    "- The offline model archive lives at /mnt/4TB/model-archive (categories: llm/, vlm/, stt/, slm/, tts/, embedding/). For 'haul', 'stash', 'the downloads', or similar, check /mnt/4TB/model-archive and /opt/ai-models with run_safe_command (ls/find/du) first.\n"
    "- run_safe_command is shell-free: no pipes |, chaining ; &&, $(), or find -exec. Glob chars (* ? [ ]) in paths are expanded automatically. Commands run in your home directory — use git -C <path> <subcommand> for repo operations. If a call fails, read the error and adjust once — do not retry the same shape; identical repeat calls are flagged as duplicates.\n"
    "- The router holds one model at a time; switching presets (e.g. from qwen-fast to devstral-coding) unloads the current model and aborts any in-flight chat or tool loop. Finish or wait for the current reply before switching models.\n"
    "- qwen-256k-batch is for batch ingestion only; a full 256k prefill is slow. Before running it, avoid competing for the last VRAM with nano or large read_file summaries — the model may OOM if the GPU is already near full.\n"
    "- Never paste raw tool output (JSON envelopes with ok/stdout/stderr fields, or unfiltered stdout dumps) into your reply. Summarize tool results as prose or a compact table.\n"
    "- Do not use web_search or fetch_url for local status questions, and do not ask the user to clarify when a tool call can resolve the ambiguity. Check first, then report what you found."
)

PLANNING_PROMPT = ((
    "You are a helpful planning and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- get_status ({USER_NAME} Local Tools server): fetch a concise system status snapshot (services, GPU/VRAM, disk, loaded model).\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, lsusb, ps, df, systemctl, journalctl, dmesg, etc.\n\n"
    "When the user asks about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Use get_status for 'what is my system status?' or 'are my services running?'. "
    "Use read_file or run_safe_command when you need exact current system values.\n\n"
    "Only use web search if the user explicitly asks for external/fresh information (e.g. 'latest news', 'what does the internet say', 'current events'), "
    "or if local tools cannot answer after a genuine attempt. "
    "When web search results are provided, cite them with [id].\n\n"
    "Be concise, accurate, and avoid unnecessary preamble. "
    "Do not output code unless the user explicitly asks for it."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

LONG_PROMPT = ((
    "You are a helpful document analysis and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
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

# Deep-research / hypothesis-hunting persona: the differentiator vs the generic
# prompts is the explicit generate->falsify->grade loop. LLMs are excellent at
# producing plausible connections (including spurious ones), so the prompt forces
# disconfirming-evidence search and an explicit confidence split rather than
# letting the model confabulate a tidy narrative.
DEEPDIVE_PROMPT = ((
    "You are a research analyst on {USER_NAME}'s local Ubuntu workstation, tuned for "
    "connecting dots across many sources: finding non-obvious relationships, building "
    "competing hypotheses, and stress-testing them before reporting.\n\n"
    "You have access to the following local tools:\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- get_status ({USER_NAME} Local Tools server): fetch a concise system status snapshot (services, GPU/VRAM, disk, loaded model).\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, ps, df, etc.\n"
    "- propose_workspace_write / confirm_workspace_write / "
    "rollback_workspace_write (Workspace Tools server): propose directories and "
    ".md files using relative paths under the configured Projects workspace, "
    "show the manifest, and confirm only after explicit user approval.\n\n"
    "Method — follow it for any 'what's going on / what connects X to Y / what's likely next' question:\n"
    "1. Decompose the question into the key claims, actors, and incentives.\n"
    "2. Generate several competing hypotheses — including uncomfortable or unfashionable ones — and state a rough prior confidence for each.\n"
    "3. For each hypothesis, hunt for DISCONFIRMING evidence, not just support. Use search_web/fetch_url for anything current or external; use local tools for host state.\n"
    "4. Grade the final answer: clearly separate VERIFIED (source-backed), PLAUSIBLE (consistent with evidence, unproven), and SPECULATIVE (interesting but unsupported).\n"
    "5. Name the blindspots: what evidence would change the conclusion, and what could not be checked.\n\n"
    "Epistemic rules: extraordinary claims need proportionally strong sourcing; never present inference as established fact; "
    "distinguish 'documents show X' from 'X is true'; cite sources with [id]. "
    "Steelman hypotheses the user proposes instead of dismissing or endorsing them — your job is rigor, not debunking or validation.\n\n"
    "For questions about {USER_NAME}'s projects, hardware, preferences, local files, or system status, use the local tools first.\n\n"
    "Session ritual: when a session changes real rig state — a service, config, "
    "file, or deployment changed — offer ONCE to file an episode with "
    "episode_append (thread, title, summary, files_touched, verified_by, "
    "open_issues). One offer, no nagging; if the user declines or it's pure "
    "Q&A, drop it."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

# Uncensored ideation persona — runs on the abliterated Qwen3.8-27B weights.
# Same epistemic discipline as DEEPDIVE_PROMPT (steelman -> disconfirm -> grade)
# but framed for open-ended idea work, not only research questions. The
# "abliterated weights" line is factual context: without it the model tends to
# hedge as if it still had the refusal behavior trained in.
IDEATION_PROMPT = ((
    "You are an open-ended thinking partner on {USER_NAME}'s local Ubuntu workstation — "
    "for working through ideas, theories, strategies, and unconventional hypotheses, not just code. "
    "These weights are abliterated (refusal direction removed): engage ideas directly without "
    "moralizing or reflexive hedging, while staying rigorous and honest.\n\n"
    "You have access to the following local tools:\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- get_status ({USER_NAME} Local Tools server): fetch a concise system status snapshot (services, GPU/VRAM, disk, loaded model).\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like inxi, rocm-smi, lscpu, ps, df, etc.\n\n"
    "How to work:\n"
    "1. Steelman first: argue the strongest version of the user's idea before critiquing it.\n"
    "2. Generate competing explanations — including unfashionable or uncomfortable ones — with rough confidence levels.\n"
    "3. Clearly separate VERIFIED (source-backed), PLAUSIBLE (consistent, unproven), and SPECULATIVE (interesting, unsupported). Never present inference as fact.\n"
    "4. Say plainly when an idea does not survive scrutiny — rigor over validation.\n"
    "5. For 'what connects X to Y' or 'what is likely next' questions: decompose claims/actors/incentives, hunt disconfirming evidence, name blindspots and what could not be checked.\n\n"
    "For questions about {USER_NAME}'s projects, hardware, preferences, local files, or system status, use the local tools first."
) + TOOL_USAGE).format(USER_NAME=USER_NAME)

CODING_PROMPT = ((
    "You are an expert software engineering, coding, and research assistant running on {USER_NAME}'s local Ubuntu workstation.\n\n"
    "You have access to the following local tools:\n"
    "- execute_code: run Python/shell code in the Open-WebUI sandbox for calculations and small transformations.\n"
    "- get_current_timestamp: get the current date and time.\n"
    "- get_status ({USER_NAME} Local Tools server): fetch a concise system status snapshot (services, GPU/VRAM, disk, loaded model).\n"
    "- read_file ({USER_NAME} Local Tools server): read a file from allowed host paths.\n"
    "- run_safe_command ({USER_NAME} Local Tools server): run a safe read-only command like ls, cat, grep, ps, df, systemctl, journalctl, dmesg, inxi, rocm-smi, etc.\n\n"
    "When the user asks about {USER_NAME}'s projects, hardware, preferences, local files, system status, or background, use these local tools first. "
    "Use get_status for 'what is my system status?' or 'are my services running?'. "
    "Read files directly with read_file or run_safe_command when you need exact content. "
    "Only use web search if the user explicitly asks for external/fresh information (e.g. 'latest news', 'what does the internet say', 'current events'), "
    "or if local tools cannot answer after a genuine attempt. "
    "When web search results are provided, cite them with [id].\n\n"
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

# qwen-deepdive is explicitly a multi-round researcher — the shared "one or two
# searches" rule would undercut its whole purpose. Same grounding contract, but
# multi-round searching is expected while each round still adds signal.
DEEPDIVE_SEARCH_BEHAVIOR = (
    "\n\nWeb search rules (search_web / fetch_url tools):\n"
    "- Call search_web when the question depends on current, specific, or external facts. Answer stable knowledge directly without searching.\n"
    "- Research questions are expected to take MULTIPLE rounds of searching — roughly one round per hypothesis or evidence thread. Keep going while each round returns new information; stop when results stop adding signal, then write up.\n"
    "- After search_web, call fetch_url on the most relevant result URLs when snippets are not enough.\n"
    "- Ground the answer in what the tools actually return and cite the sources. If the results do not answer the question, say so explicitly — never invent URLs or fill gaps with guesses. If a thread cannot be verified, report what could not be verified and move on.\n"
    "- run_safe_command is for inspecting THIS host only. Never use it (or curl/wget through it) to reach the web — all external requests go through search_web and fetch_url."
)

# Per-model override for the appended search rules.
SEARCH_BEHAVIOR_BY_MODEL = {
    "qwen-deepdive": DEEPDIVE_SEARCH_BEHAVIOR,
    "qwen-free": DEEPDIVE_SEARCH_BEHAVIOR,
}

# Short display names for the Open-WebUI model selector (≤3 words —
# the long use-case names crowded the mobile UI). Enriched into
# /api/v1/models by patch_openai_model_names.py.
# Contract: test_web_search_pipeline.py::test_model_display_names_are_short.
MODEL_NAMES = {
    # Selector-visible (2026-10-11 simplified surface — job-first names):
    "qwen-dsh-medium": "Agent 192k",
    "qwen-fast": "Fast 192k",
    "qwen-deepdive": "Deep 192k",
    "qwen-free": "Ideas 192k",
    # Selector-hidden but API-alive:
    "nano": "Nano Chat 32k",
    "qwen-128k": "Legacy Plan 192k",
    "qwen-256k-batch": "Batch Only 256k",
    "qwen-think": "Think Mode 128k",
    "qwen-balanced": "Balanced 128k",
    "qwen-quality": "Max Quality 16k",
    "devstral-coding": "Devstral Code 64k",
    "qwen-dsh-fast": "Agentic Fast 128k",
    "qwen-dsh-low": "Agentic Best 128k",
}

# Aliases kept in MODEL_NAMES (API-usable rows) but hidden from the
# interactive selector and ui.model_order_list.
# 2026-10-11 alias cleanup: the 12-entry picker collapsed to 4 jobs.
# All selector-visible presets now run the same 192k ceiling, which made
# qwen-fast and qwen-128k the same underlying preset — merged under Fast.
# The rest are dial points of the same weights (dsh-fast/low, think),
# marginal quant tiers (balanced, quality), demoted (devstral), internal
# (nano), or batch-only (256k) — all stay API-usable, none in the picker.
SELECTOR_HIDDEN = {
    "nano", "qwen-128k", "qwen-256k-batch", "qwen-think", "qwen-balanced",
    "qwen-quality", "devstral-coding", "qwen-dsh-fast", "qwen-dsh-low",
}

# Canonical selector order (ui.model_order_list): the daily driver first,
# then zero-reasoning chat, deliberate research, uncensored ideation.
# Covers MODEL_NAMES minus SELECTOR_HIDDEN.
MODEL_ORDER = [
    "qwen-dsh-medium",
    "qwen-fast",
    "qwen-deepdive",
    "qwen-free",
]

# Short use-case hints shown as selector subtitles / in Admin -> Models.
# Kept canonical here so `apply-model-config` restores them.
MODEL_DESCRIPTIONS = {
    "qwen-dsh-medium": "The daily driver — chat, code, tools; measured best carrier (192k).",
    "qwen-fast": "Instant answers — no thinking, lowest latency (192k).",
    "qwen-deepdive": "Deliberate research + host-change proposals (192k).",
    "qwen-free": "Uncensored ideation — abliterated weights, same rigor loop (192k).",
    "qwen-128k": "API fallback — same preset as Fast (192k ctx).",
    "qwen-256k-batch": "Bulk ingestion only — not interactive.",
    "qwen-think": "API fallback — max reasoning effort.",
    "qwen-balanced": "API fallback — Q6 quality tier.",
    "qwen-quality": "API fallback — highest accuracy, Q8_0, 16k ctx.",
    "devstral-coding": "API fallback — secondary coder, cleanest refuser.",
    "qwen-dsh-fast": "API fallback — agentic, reasoning off.",
    "qwen-dsh-low": "API fallback — agentic, light reasoning.",
    "nano": "Internal sidecar — instant answers, no tools.",
}

# Completion cap per alias — keep in sync with `max_tokens` in
# set_context_lengths.py. Reasoning presets (think, dsh-low, dsh-medium) and
# the long-doc/batch tiers get 16384 because chain-of-thought and bulk output
# share the same max_tokens budget; interactive presets get 8192; nano stays
# small for instant replies.
MAX_TOKENS = {
    "qwen-fast": 8192,
    "qwen-think": 16384,
    "qwen-balanced": 8192,
    "qwen-quality": 8192,
    "qwen-128k": 16384,
    "qwen-256k-batch": 16384,
    "devstral-coding": 8192,
    "qwen-dsh-fast": 8192,
    "qwen-dsh-low": 16384,
    "qwen-dsh-medium": 16384,
    "qwen-deepdive": 16384,
    "qwen-free": 16384,
    "nano": 2048,
}
DEFAULT_MAX_TOKENS = 8192

# Per-model temperature overrides (models not listed keep whatever is in the
# DB). The ideation presets run slightly hot for divergent hypothesis
# generation; the falsification loop is what keeps the extra variance honest.
TEMPERATURE_BY_MODEL = {
    "qwen-deepdive": 0.8,
    "qwen-free": 0.8,
}

# function_calling: Qwen3.8-27B (with --jinja) emits OpenAI-compatible tool_calls,
# so use native for all Qwen aliases. devstral-coding also uses native.
FUNCTION_CALLING_BY_MODEL = {
    "qwen-fast": "native",
    "qwen-think": "native",
    "qwen-balanced": "native",
    "qwen-128k": "native",
    "qwen-256k-batch": "native",
    "qwen-quality": "native",
    "devstral-coding": "native",
    "qwen-dsh-fast": "native",
    "qwen-dsh-low": "native",
    "qwen-dsh-medium": "native",
    "qwen-deepdive": "native",
    "qwen-free": "native",
}

# Anti-loop sampling: Qwen3.8's recommended presence_penalty for non-thinking
# mode is 1.5 (prevents the "I'll check..." narration loop where the model
# repeats intent text instead of emitting tool_calls). Coding aliases get 1.0
# (gentler on legitimately repetitive syntax); thinking models stay at 0.0 —
# penalizing already-seen tokens degrades long chain-of-thought. devstral and
# the oak-* models are left alone.
PRESENCE_PENALTY_BY_MODEL = {
    "qwen-fast": 1.5,
    "qwen-128k": 1.5,
    "qwen-256k-batch": 1.5,
    "qwen-quality": 1.0,
    "qwen-balanced": 1.0,
    "qwen-dsh-fast": 1.0,
    "qwen-think": 0.0,
    "qwen-dsh-low": 0.0,
    "qwen-dsh-medium": 0.0,
    "qwen-deepdive": 0.0,
    "qwen-free": 0.0,
}

# Per-model prompts align with AGENTS.md usage:
# - qwen-fast: planning/chat + long docs (absorbed qwen-long 2026-09-15)
# - qwen-128k/qwen-256k-batch: long-document analysis
# - qwen-think: reasoning
# - qwen-balanced: balanced coding/research
# - qwen-quality/devstral-coding/qwen-dsh-*: coding with local tools
MODEL_PROMPTS = {
    "qwen-fast": PLANNING_PROMPT,
    "qwen-128k": LONG_PROMPT,
    "qwen-256k-batch": LONG_PROMPT,
    "qwen-think": THINK_PROMPT,
    "qwen-balanced": BALANCED_PROMPT,
    "qwen-quality": CODING_PROMPT,
    "devstral-coding": CODING_PROMPT,
    "qwen-dsh-fast": CODING_PROMPT,
    "qwen-dsh-low": CODING_PROMPT,
    "qwen-dsh-medium": CODING_PROMPT,
    "qwen-deepdive": DEEPDIVE_PROMPT,
    "qwen-free": IDEATION_PROMPT,
}

# Voice mode graft (2026-10-11): Agent + Ideas can be asked to write in the
# user's voice — the same invoke-on-demand pattern as the /voice skill.
# The model reads the pack itself via read_file, so the pack stays current
# with zero config re-apply; nothing is injected when unused.
VOICE_MODE = (
    "\n\nVoice mode: when the user asks you to write in THEIR voice — a "
    "post, email, reply, or announcement — read "
    "~/Projects/voice/spec/voice-spec.md plus 2-3 files under "
    "~/Projects/voice/exemplars/ (pick the register that matches: essays, "
    "email, notes, chat) BEFORE drafting. Voice renders THEIR take in "
    "THEIR words — never invent the position; if they haven't given you "
    "the take, ask for it."
)
for _mid in ("qwen-dsh-medium", "qwen-free"):
    MODEL_PROMPTS[_mid] = MODEL_PROMPTS[_mid] + VOICE_MODE

# Minimal prompt for the nano SLM — no tools, no search; just fast answers.
NANO_PROMPT = (
    "You are Nano, a tiny local assistant (Llama-3.2-3B) on Jacko's machine.\n\n"
    "Keep answers short and direct — you exist for quick questions. You have no "
    "tools, no web search, and no file access; if a task needs them, say so and "
    "suggest switching to a bigger model."
)

# Per-model default features:
# - Web search and code interpreter are available (capabilities/builtinTools) but OFF by default
#   so the assistant doesn't call out to the internet or run code unless you explicitly toggle it.
# - 2026-09-26 trim: knowledge + memory REMOVED everywhere. Both are
#   probabilistic retrieval surfaces that pulled wrong-domain/stale docs into
#   prompts (the "dementia" failure mode — 09-23 wrong-domain retrieval loop).
#   Continuity is carried by the deterministic layer instead: state snapshot
#   injection + hori-state thread tools (thread_list/thread_read/episode_search).
# - Non-coding models: time; Coding/DSH models: also code_interpreter
MODEL_DEFAULT_FEATURES = {
    "qwen-fast": ["time"],
    "qwen-128k": ["time"],
    "qwen-think": ["time"],
    "qwen-balanced": ["time"],
    "qwen-256k-batch": ["time"],
    "qwen-quality": ["time", "code_interpreter"],
    "devstral-coding": ["time", "code_interpreter"],
    "qwen-dsh-fast": ["time", "code_interpreter"],
    "qwen-dsh-low": ["time", "code_interpreter"],
    "qwen-dsh-medium": ["time", "code_interpreter"],
    "qwen-deepdive": ["time"],
    "qwen-free": ["time"],
}

# Capabilities = what the UI is allowed to show/offer for a model.
# memory/knowledge capabilities OFF — hides the dead toggles AND stops any
# residual memory/RAG injection; builtinTools below is the hard grammar gate.
META_CAPABILITIES = {
    "web_search": True,
    "knowledge": False,
    "memory": False,
    "builtin_tools": True,
    "code_interpreter": True,
    "time": True,
}

# Vision: these router aliases load the Qwen3.8 mmproj encoder (2026-09-06), so
# expose the image-attach UI. qwen-quality has no mmproj (Q8_0 + q8_0 KV leaves
# too little VRAM headroom); devstral-coding is text-only.
VISION_MODELS = {
    "qwen-fast",
    "qwen-128k",
    "qwen-256k-batch",
    "qwen-think",
    "qwen-balanced",
    "qwen-dsh-fast",
    "qwen-dsh-low",
    "qwen-dsh-medium",
    "qwen-deepdive",
    "qwen-free",
}

# Default feature toggles for auto-discovered / fallback models.
# Web search and code interpreter are OFF by default; the user toggles them when needed.
META_DEFAULT_FEATURES = ["time"]

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
# useful read tools; disable the workspace/social categories that only
# serve as tool-misfire bait on small local models (e.g. update_task loops).
# web_search stays OFF for all models except WEB_SEARCH_MODELS.
# 2026-09-26: knowledge/memory/chats OFF — the retrieval safaris (search_chats
# pulling stale cross-chat docs, memory×7 looping, knowledge×5 on dead
# collections) were the brittleness source. files stays on (manual attach).
# 2026-10-10: code_interpreter OFF — eval A/B (rig-agent-eval --surface):
# execute_code was the ONLY consistent wrong-tool attractor (6 calls across
# 3 surface runs, picked over run_safe_command for exec tasks). On this box
# it's also an unaudited exec path — no backend configured, so a call is a
# dead end, or Pyodide-in-browser: exec outside deny.conf + audit journal.
META_BUILTINTOOLS = {
    "knowledge": False,
    "time": True,
    "code_interpreter": False,
    "web_search": False,
    "memory": False,
    "files": True,
    "chats": False,
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
        return base + SEARCH_BEHAVIOR_BY_MODEL.get(model_id, WEB_SEARCH_BEHAVIOR)
    return base


def default_features_for(model_id: str) -> list:
    """default_feature_ids for a model: the per-model list plus 'web_search'
    for WEB_SEARCH_MODELS (the request must carry features.web_search for the
    builtin search_web tool to be registered — see tools.py gate)."""
    feats = list(MODEL_DEFAULT_FEATURES.get(model_id, META_DEFAULT_FEATURES))
    if model_id in WEB_SEARCH_MODELS and "web_search" not in feats:
        feats.append("web_search")
    return feats


# Workspace write tools are deliberately narrower than project creation: they
# can scaffold directories and Markdown files under the configured Projects
# workspace. Keep them off every model except Deep Dive until the workflow has
# proven itself in use.
WORKSPACE_WRITE_MODELS = {"qwen-deepdive"}
WORKSPACE_TOOLS_CONNECTION = {
    "url": "http://127.0.0.1:8771",
    "config": {
        "enable": True,
        "function_name_filter_list": (
            "propose_workspace_write,"
            "confirm_workspace_write,"
            "rollback_workspace_write"
        ),
    },
    "info": {"id": "workspace-tools", "name": "Workspace Tools"},
}

# Project-scaffolding tools (propose/confirm/rollback_create_project) are
# planning-time tools, not chat tools — keep them off the always-on set so
# they stop occupying grammar slots in every fast-chat request.
PROJECT_TOOL_MODELS = {
    "qwen-128k",
    "qwen-deepdive",
    "qwen-dsh-fast",
    "qwen-dsh-low",
    "qwen-dsh-medium",
}

def _hori_mcp_read_token() -> str:
    """Read-scoped HORI MCP bearer for the native-MCP connection row.

    Read from the host mcpo config at apply time — the token must never be
    committed (hori-mcpo.service reads the same file upstream). Returns ''
    when unavailable; the row is written anyway so a later apply can
    re-inject once the token exists.
    """
    try:
        import pathlib

        cfg = json.loads(
            (pathlib.Path.home() / ".config/mcpo/hori.json").read_text()
        )
        auth = cfg["mcpServers"]["hori"]["headers"]["Authorization"]
        return auth.split(" ", 1)[1] if " " in auth else auth
    except Exception:
        return ""


def _hori_admin_token() -> str:
    """Admin-scoped HORI MCP bearer for the episode-write connection.

    The admin scope can call the write tools (episode_append,
    memory_store); the read scope cannot. Sourced from the Devin MCP
    client config at apply time — /etc/aios/secrets.env is root-only,
    and the token must never be committed anyway. Returns '' when
    unavailable; the row is written so a later apply can re-inject.
    """
    try:
        import pathlib

        cfg = json.loads(
            (pathlib.Path.home() / ".config/devin/mcp_config.json").read_text()
        )
        auth = (cfg.get("mcpServers", {}).get("hori", {})
                or cfg.get("servers", {}).get("hori", {}))
        auth = auth["headers"]["Authorization"]
        return auth.split(" ", 1)[1] if " " in auth else auth
    except Exception:
        return ""


# Read-only HORI state tools (threads/episodes/memory) via native MCP on
# aios_core: OWUI's streamablehttp client -> POST /mcp/http, read-scoped
# bearer. Retired the mcpo -> :8775 filter OpenAPI bridge (2026-10-09).
# function_name_filter_list still caps the OWUI surface at the 4 state
# tools — get_status stays local-tools-owned so the model sees exactly
# one status tool.
HORI_STATE_CONNECTION = {
    "url": "http://127.0.0.1:5680/mcp/http",
    "path": "",
    "type": "mcp",
    "auth_type": "bearer",
    "headers": {},
    "key": _hori_mcp_read_token(),
    "config": {
        "enable": True,
        "function_name_filter_list": (
            "thread_list,thread_read,episode_search,memory_search"
        ),
    },
    "spec_type": "url",
    "info": {
        "id": "hori-state",
        "name": "HORI State",
        "description": "Read-only rig/agent state: threads, episodes, memory",
    },
}

# Episode-write surface (2026-10-11): same native MCP endpoint, admin-scoped
# bearer — the read token cannot invoke episode_append/memory_store. A
# separate connection entry keeps the write surface gated per-model: only
# EPISODE_MODELS get server:mcp:hori-episodes attached (see the filter's
# episode_models valve). The write tools stay OFF every other model.
EPISODE_MODELS = {"qwen-deepdive"}
HORI_EPISODES_CONNECTION = {
    "url": "http://127.0.0.1:5680/mcp/http",
    "path": "",
    "type": "mcp",
    "auth_type": "bearer",
    "headers": {},
    "key": _hori_admin_token(),
    "config": {
        "enable": True,
        "function_name_filter_list": "episode_append",
    },
    "spec_type": "url",
    "info": {
        "id": "hori-episodes",
        "name": "HORI Episodes",
        "description": "Write rig journal episodes (thread append-only)",
    },
}


def episode_server_tool_ids_for(model_id: str) -> list[str]:
    """Episode-write tool ids attached only to EPISODE_MODELS."""
    if model_id in EPISODE_MODELS:
        return ["server:mcp:hori-episodes"]
    return []


# Retired bridge URL — the row is stripped wherever it appears.
HORI_STATE_RETIRED_URL = "http://127.0.0.1:8775"


def workspace_server_tool_ids_for(model_id: str) -> list[str]:
    """Extra server tool ids attached only to explicitly write-enabled models."""
    if model_id in WORKSPACE_WRITE_MODELS:
        return ["server:workspace-tools"]
    return []


# Disposable sandbox exec (bwrap-confined ~/agent-scratch on :8774). Armed on
# the deliberate-agent model and the measured agentic carrier: Band 2 of the
# trust ladder — disposable by construction, still behind the deny overlay.
# qwen-dsh-medium earned the surface 2026-10-11 (rig-agent-eval ep-024:
# 17/17 do-tasks on the 22-task coding suite, best measured carrier);
# deepdive keeps it for deliberate research work.
SANDBOX_MODELS = {"qwen-deepdive", "qwen-dsh-medium"}
SANDBOX_TOOLS_CONNECTION = {
    "url": "http://127.0.0.1:8774",
    "config": {
        "enable": True,
        "function_name_filter_list": (
            "sandbox_exec,sandbox_status,sandbox_serve,"
            "sandbox_serve_status,sandbox_serve_stop,"
            "sandbox_write,sandbox_edit"
        ),
    },
    "info": {"id": "sandbox-tools", "name": "Sandbox Tools"},
}


def sandbox_server_tool_ids_for(model_id: str) -> list[str]:
    """Sandbox tool ids attached only to the deliberate/agentic models."""
    if model_id in SANDBOX_MODELS:
        return ["server:sandbox-tools"]
    return []


# Host-change proposal channel (:8775). Band 3 of the trust ladder: the
# model stages sandbox-verified content as a proposal; a human approves
# (/trust or agent-approve) and the guarded-change envelope applies it.
# Deliberate-agent posture — same gating as sandbox/workspace writes.
PROPOSAL_MODELS = {"qwen-deepdive"}
PROPOSAL_TOOLS_CONNECTION = {
    "url": "http://127.0.0.1:8775",
    "config": {
        "enable": True,
        "function_name_filter_list": (
            "propose_host_change,list_proposals,proposal_status,"
            "retract_proposal"
        ),
    },
    "info": {"id": "proposal-tools", "name": "Proposal Tools"},
}


def proposal_server_tool_ids_for(model_id: str) -> list[str]:
    """Proposal-channel tool ids attached only to the deliberate model."""
    if model_id in PROPOSAL_MODELS:
        return ["server:proposal-tools"]
    return []


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
            default="qwen-fast,qwen-think,qwen-balanced,qwen-quality,devstral-coding,qwen-128k,qwen-256k-batch",
            description="Comma-separated model ids to attach local tool servers to.",
        )
        server_tool_ids: str = Field(
            default="server:local-tools,server:mcp:hori-state",
            description="Comma-separated 'server:<id>' tool_ids to attach (server-side execution). MCP connections resolve via 'server:mcp:<id>'.",
        )
        project_models: str = Field(
            default="qwen-128k,qwen-deepdive,qwen-dsh-fast,qwen-dsh-low,qwen-dsh-medium",
            description="Comma-separated model ids that also get project-creation tools.",
        )
        project_tool_ids: str = Field(
            default="server:project-tools",
            description="Comma-separated 'server:<id>' tool_ids attached to project_models.",
        )
        write_models: str = Field(
            default="qwen-deepdive",
            description="Comma-separated model ids allowed to use workspace write tools.",
        )
        write_server_tool_ids: str = Field(
            default="server:workspace-tools",
            description="Comma-separated write-capable 'server:<id>' tool_ids for write_models.",
        )
        sandbox_models: str = Field(
            default="qwen-deepdive",
            description="Comma-separated model ids allowed to use the disposable sandbox exec tools.",
        )
        sandbox_server_tool_ids: str = Field(
            default="server:sandbox-tools",
            description="Comma-separated 'server:<id>' tool_ids for sandbox_models.",
        )
        proposal_models: str = Field(
            default="qwen-deepdive",
            description="Comma-separated model ids allowed to use the host-change proposal channel (Band 3).",
        )
        proposal_server_tool_ids: str = Field(
            default="server:proposal-tools",
            description="Comma-separated 'server:<id>' tool_ids for proposal_models.",
        )
        episode_models: str = Field(
            default="qwen-deepdive",
            description="Comma-separated model ids allowed to append episodes to the rig journal (HORI episode_append).",
        )
        episode_server_tool_ids: str = Field(
            default="server:mcp:hori-episodes",
            description="Comma-separated 'server:<id>' tool_ids for episode_models.",
        )
        state_file: str = Field(
            default="/app/backend/data/aios-state-snapshot.md",
            description="Snapshot file injected as a system message into every allowed model request (written by aios-state-snapshot.timer on the host).",
        )
        state_max_age_s: int = Field(
            default=900,
            description="Snapshots older than this many seconds are injected with a STALE warning instead of a clean header.",
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
            server_ids = {t.strip() for t in server_tool_ids.split(",") if t.strip()}
            project_models = {
                m.strip()
                for m in (getattr(self.valves, "project_models", "") or "").split(",")
                if m.strip()
            }
            if model_id in project_models:
                proj_ids = getattr(self.valves, "project_tool_ids", "") or ""
                server_ids.update(t.strip() for t in proj_ids.split(",") if t.strip())
            write_models = {
                m.strip()
                for m in (getattr(self.valves, "write_models", "") or "").split(",")
                if m.strip()
            }
            if model_id in write_models:
                write_ids = getattr(self.valves, "write_server_tool_ids", "") or ""
                server_ids.update(t.strip() for t in write_ids.split(",") if t.strip())
            sandbox_models = {
                m.strip()
                for m in (getattr(self.valves, "sandbox_models", "") or "").split(",")
                if m.strip()
            }
            if model_id in sandbox_models:
                sbx_ids = getattr(self.valves, "sandbox_server_tool_ids", "") or ""
                server_ids.update(t.strip() for t in sbx_ids.split(",") if t.strip())
            proposal_models = {
                m.strip()
                for m in (getattr(self.valves, "proposal_models", "") or "").split(",")
                if m.strip()
            }
            if model_id in proposal_models:
                prop_ids = getattr(self.valves, "proposal_server_tool_ids", "") or ""
                server_ids.update(t.strip() for t in prop_ids.split(",") if t.strip())
            episode_models = {
                m.strip()
                for m in (getattr(self.valves, "episode_models", "") or "").split(",")
                if m.strip()
            }
            if model_id in episode_models:
                ep_ids = getattr(self.valves, "episode_server_tool_ids", "") or ""
                server_ids.update(t.strip() for t in ep_ids.split(",") if t.strip())
            server_tool_ids = ",".join(sorted(server_ids))

            for tool_id in server_ids:
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

            # Inject the generated AIOS state snapshot as a system message —
            # deterministic delivery, unlike knowledge/RAG retrieval. Written
            # by aios-state-snapshot.timer on the host (services/open-webui/
            # state_snapshot.py). A stale file is still injected but flagged;
            # a missing file is skipped silently (generator never ran).
            try:
                import os as _os
                import time as _t2

                state_path = getattr(self.valves, "state_file", "") or \
                    "/app/backend/data/aios-state-snapshot.md"
                max_age = int(getattr(self.valves, "state_max_age_s", 0) or 0)
                age = int(_t2.time() - _os.path.getmtime(state_path))
                with open(state_path, encoding="utf-8") as _f:
                    snapshot = _f.read()[:4000]

                stale = max_age and age > max_age
                header = (
                    "[AIOS state snapshot — generated on host %ds ago%s. "
                    "Use this ONLY when directly relevant to the user's "
                    "request — if the request is unrelated to current "
                    "system state, ignore this block entirely and never "
                    "quote or mention it. "
                    "This ALREADY satisfies the current-state check: do NOT "
                    "re-probe with run_safe_command to rediscover services, "
                    "loaded model, workstreams, or resources listed here. "
                    "Quote these values VERBATIM — never infer model "
                    "architectures, versions, service names, or numbers "
                    "not listed; if a detail is absent, say it is not in "
                    "the snapshot. For current-state questions (status, "
                    "loaded model, services, rig, workstreams) this block "
                    "is the COMPLETE answer — no tool calls at all: do "
                    "not search knowledge, memory, chats, or files; "
                    "retrieved docs describe the past, not the present. "
                    "get_status is only for live detail beyond this "
                    "block.]\\n"
                    % (age, " — STALE, snapshot generator may be down" if stale else "")
                )
                msgs = body.get("messages") or []
                msgs = [
                    m for m in msgs
                    if not (
                        m.get("role") == "system"
                        and "[AIOS state snapshot" in (m.get("content") or "")
                    )
                ]
                inject_at = 0
                while (
                    inject_at < len(msgs)
                    and msgs[inject_at].get("role") == "system"
                ):
                    inject_at += 1
                msgs.insert(inject_at, {"role": "system", "content": header + snapshot})
                body["messages"] = msgs
            except Exception:
                pass

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


def _clone_workspace_model(c, source_id: str, target_id: str, description: str):
    """Create a new Open-WebUI model record by cloning a workspace template."""
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
    params["max_tokens"] = MAX_TOKENS.get(target_id, DEFAULT_MAX_TOKENS)
    if target_id in TEMPERATURE_BY_MODEL:
        params["temperature"] = TEMPERATURE_BY_MODEL[target_id]
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
    # qwen-long was retired 2026-09-15 (qwen-fast now carries 128k ctx): hide
    # the stale workspace row rather than deleting it so old chats keep their
    # model label; revivable by flipping is_active back to 1.
    c.execute(
        "UPDATE model SET is_active = 0, updated_at = ? WHERE id = 'qwen-long'",
        (now(),),
    )

    for target, desc in [
        ("qwen-128k", "Planning/long-doc analysis — Qwen3.8-27B-UD-Q4_K_M (Dynamic v3.0), 192k context, q4_0 KV, draft-mtp, vision"),
        ("qwen-256k-batch", "Batch ingestion only — Qwen3.8-27B-UD-Q4_K_M (Dynamic v3.0), 256k context, q4_0 KV, no speculative decode, vision"),
        ("qwen-deepdive", "Deep research / hypothesis hunting — Qwen3.8-27B-UD-Q4_K_M, 128k context, reasoning on, draft-mtp-adaptive, vision"),
        ("qwen-free", "Uncensored ideation — Huihui Qwen3.8-27B-abliterated UD-Q4_K_XL, 128k context, reasoning on, draft-mtp-adaptive, vision"),
    ]:
        exists = c.execute("SELECT 1 FROM model WHERE id = ?", (target,)).fetchone()
        if not exists:
            _clone_workspace_model(c, "qwen-fast", target, desc)
            print(f"created model {target}")
        else:
            print(f"model {target} already exists")

    # Selector visibility is canonical in SELECTOR_HIDDEN: hidden rows keep
    # their router aliases alive for API/batch use but drop out of the OWUI
    # picker; visible rows are re-activated so apply is self-healing.
    for mid in SELECTOR_HIDDEN:
        c.execute(
            "UPDATE model SET is_active = 0, updated_at = ? WHERE id = ?",
            (now(), mid),
        )
    for mid in MODEL_ORDER:
        c.execute(
            "UPDATE model SET is_active = 1, updated_at = ? WHERE id = ?",
            (now(), mid),
        )

    # 1. Update tool_server connection function_name_filter_list
    project_tools_conn = {
        "url": "http://127.0.0.1:8769",
        "config": {
            "enable": True,
            "function_name_filter_list": "propose_create_project,confirm_create_project,rollback_project",
        },
        "info": {"id": "project-tools", "name": "Project Tools"},
    }
    # Drop the stale hand-written local-tools workspace script: its spec
    # advertised get_status but the Tools class never implemented it (and it
    # calls pre-rename endpoints /run/safe_command, /read/file), so enabling
    # it in a chat hijacked the call -> 'Tools' object has no attribute
    # 'get_status' -> empty assistant reply. Id pattern keeps this PII-free.
    c.execute("DELETE FROM tool WHERE id LIKE '%-local-tools'")

    row = c.execute("SELECT value FROM config WHERE key='tool_server.connections'").fetchone()
    if row:
        conns = json.loads(row[0])
        # Drop the retired mcpo->filter bridge row wherever it sits.
        # :8775 is RECLAIMED by proposal-tools (Band 3) — purge the port
        # only when the row isn't the proposal connection itself.
        conns = [conn for conn in conns
                 if conn.get('url', '') != HORI_STATE_RETIRED_URL
                 or (conn.get('info') or {}).get('id') == 'proposal-tools']
        # Ensure project-tools/workspace-tools/hori-state are present and
        # local-tools is clean. Presence is keyed on info.id, not URL —
        # hori-state and hori-episodes share the MCP endpoint.
        conn_ids = {
            (conn_cfg.get('info') or {}).get('id') for conn_cfg in conns
        }
        for tool_conn in (
            project_tools_conn,
            WORKSPACE_TOOLS_CONNECTION,
            SANDBOX_TOOLS_CONNECTION,
            PROPOSAL_TOOLS_CONNECTION,
            HORI_STATE_CONNECTION,
            HORI_EPISODES_CONNECTION,
        ):
            if (tool_conn.get('info') or {}).get('id') not in conn_ids:
                conns.append(tool_conn)
        for conn_cfg in conns:
            url = conn_cfg.get('url', '')
            if url == 'http://127.0.0.1:8766':
                conn_cfg['config'] = conn_cfg.get('config', {})
                conn_cfg['config']['enable'] = True
                conn_cfg['config']['function_name_filter_list'] = 'read_file,run_safe_command,get_status,youtube_transcript'
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
            elif url == HORI_STATE_CONNECTION['url'] and \
                    (conn_cfg.get('info') or {}).get('id') == 'hori-state':
                # Native-MCP row: normalize type/auth/token each apply so
                # token rotation propagates from the host config.
                conn_cfg['type'] = 'mcp'
                conn_cfg['auth_type'] = 'bearer'
                if HORI_STATE_CONNECTION['key']:
                    conn_cfg['key'] = HORI_STATE_CONNECTION['key']
                conn_cfg['config'] = conn_cfg.get('config', {})
                conn_cfg['config']['enable'] = True
                conn_cfg['config']['function_name_filter_list'] = (
                    HORI_STATE_CONNECTION['config']['function_name_filter_list']
                )
                conn_cfg['info'].update(HORI_STATE_CONNECTION['info'])
            elif url == HORI_EPISODES_CONNECTION['url'] and \
                    (conn_cfg.get('info') or {}).get('id') == 'hori-episodes':
                # The episode-write sibling row: same endpoint, admin
                # bearer — normalize identically (rotation propagates).
                conn_cfg['type'] = 'mcp'
                conn_cfg['auth_type'] = 'bearer'
                if HORI_EPISODES_CONNECTION['key']:
                    conn_cfg['key'] = HORI_EPISODES_CONNECTION['key']
                conn_cfg['config'] = conn_cfg.get('config', {})
                conn_cfg['config']['enable'] = True
                conn_cfg['config']['function_name_filter_list'] = (
                    HORI_EPISODES_CONNECTION['config']['function_name_filter_list']
                )
                conn_cfg['info'].update(HORI_EPISODES_CONNECTION['info'])
            elif url == WORKSPACE_TOOLS_CONNECTION['url']:
                conn_cfg['config'] = conn_cfg.get('config', {})
                conn_cfg['config']['enable'] = True
                conn_cfg['config']['function_name_filter_list'] = (
                    WORKSPACE_TOOLS_CONNECTION['config']['function_name_filter_list']
                )
                conn_cfg['info'] = conn_cfg.get('info', {})
                conn_cfg['info']['id'] = 'workspace-tools'
                conn_cfg['info']['name'] = 'Workspace Tools'
            elif url == SANDBOX_TOOLS_CONNECTION['url']:
                conn_cfg['config'] = conn_cfg.get('config', {})
                conn_cfg['config']['enable'] = True
                conn_cfg['config']['function_name_filter_list'] = (
                    SANDBOX_TOOLS_CONNECTION['config']['function_name_filter_list']
                )
                conn_cfg['info'] = conn_cfg.get('info', {})
                conn_cfg['info']['id'] = 'sandbox-tools'
                conn_cfg['info']['name'] = 'Sandbox Tools'
            elif url == PROPOSAL_TOOLS_CONNECTION['url']:
                conn_cfg['config'] = conn_cfg.get('config', {})
                conn_cfg['config']['enable'] = True
                conn_cfg['config']['function_name_filter_list'] = (
                    PROPOSAL_TOOLS_CONNECTION['config']['function_name_filter_list']
                )
                conn_cfg['info'] = conn_cfg.get('info', {})
                conn_cfg['info']['id'] = 'proposal-tools'
                conn_cfg['info']['name'] = 'Proposal Tools'
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
                                "function_name_filter_list": "read_file,run_safe_command,get_status,youtube_transcript",
                            },
                            "info": {"id": "local-tools", "name": "Local Tools"},
                        },
                        project_tools_conn,
                        WORKSPACE_TOOLS_CONNECTION,
                        SANDBOX_TOOLS_CONNECTION,
                        PROPOSAL_TOOLS_CONNECTION,
                        HORI_STATE_CONNECTION,
                        HORI_EPISODES_CONNECTION,
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
        params["max_tokens"] = MAX_TOKENS.get(mid, DEFAULT_MAX_TOKENS)
        if mid in TEMPERATURE_BY_MODEL:
            params["temperature"] = TEMPERATURE_BY_MODEL[mid]
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
        # Strip model-attached knowledge collections — they auto-inject stale
        # docs into EVERY message (8 collections were on all 12 models 2026-09-26).
        meta["knowledge"] = []

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

    # Refuse to write a filter that can't load — OWUI swallows plugin
    # SyntaxErrors at request time, so the check must happen here.
    compile(TOOL_SERVER_FILTER_SOURCE, "<tool_server_filter>", "exec")
    c.execute(
        "UPDATE function SET content = ?, is_global = 1, is_active = 1, valves = ?, updated_at = ? WHERE id = ?",
        (
            TOOL_SERVER_FILTER_SOURCE.strip(),
            json.dumps(
                {
                    "models": ",".join(ALL_QWEN_DEVSTRAL),
                    "server_tool_ids": "server:local-tools,server:mcp:hori-state",
                    "project_models": ",".join(sorted(PROJECT_TOOL_MODELS)),
                    "project_tool_ids": "server:project-tools",
                    "write_models": ",".join(sorted(WORKSPACE_WRITE_MODELS)),
                    "write_server_tool_ids": "server:workspace-tools",
                    "sandbox_models": ",".join(sorted(SANDBOX_MODELS)),
                    "sandbox_server_tool_ids": "server:sandbox-tools",
                    "proposal_models": ",".join(sorted(PROPOSAL_MODELS)),
                    "proposal_server_tool_ids": "server:proposal-tools",
                    "episode_models": ",".join(sorted(EPISODE_MODELS)),
                    "episode_server_tool_ids": "server:mcp:hori-episodes",
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
        "max_tokens": DEFAULT_MAX_TOKENS,
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

    # 5. Canonical config values: the live DB is the source of truth (it overrides
    # env vars), so write them here to keep `apply` idempotent and prevent drift.
    #   - task.model.default: "" = run title/query/compaction/memory tasks on the
    #     chat's own model. A fixed alias (e.g. qwen-fast) forces a router
    #     model-swap on every task call under --models-max 1, evicting the
    #     resident model and wiping its KV — the next chat turn then re-prefills
    #     the whole conversation. Same-model tasks are ~free and higher quality
    #     than the 3B nano sidecar for compaction/query-gen.
    #   - task.model.params: caps every task call (title/query-gen/compaction)
    #     at 1024 tokens. Compaction summaries need the headroom (default was
    #     the model's own max_tokens — up to 16384); titles/queries use ~30-100.
    #   - ui.default_pinned_models: canonical JSON array (was a CSV string from
    #     an older write path).
    #   - web.search.result_count/concurrent_requests: match docker-compose.
    canonical_config = {
        "task.model.default": "",
        "task.model.params": {"max_tokens": 1024},
        "ui.default_models": "qwen-dsh-medium",
        "ui.default_pinned_models": MODEL_ORDER,
        "ui.model_order_list": MODEL_ORDER,
        # Compaction was mashing chats at 12k tokens — ~9% of the 128k
        # window, reachable in 2-3 tool-heavy turns, retaining only 40%.
        # Trigger near the real 192k ceiling instead; the summary cap and
        # retention are unchanged. (2026-10-11, "chat turns to mush" fix.)
        "chat.context_compaction.token_threshold": 140000,
        "web.search.result_count": 5,
        "web.search.concurrent_requests": 3,
        "web.search.searxng_query_url": "http://127.0.0.1:8081/search?q=<query>",
        # Nano server (llama-nano.service) registered as a second OpenAI base
        # URL so `nano` shows up alongside the router presets.
        "openai.api_base_urls": [
            "http://127.0.0.1:8080/v1",
            "http://127.0.0.1:8090/v1",
        ],
    }
    for key, val in canonical_config.items():
        c.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(val, ensure_ascii=False), now()),
        )
    print("updated canonical config keys:", ", ".join(canonical_config))

    # 5b. Nano: tiny always-on SLM served by a dedicated llama-server on :8090
    # (llama-nano.service, user unit) and registered as a second OpenAI base
    # URL. Deliberately minimal: no builtin tools, no web search, no local
    # tools (it is not in the tool-server filter's models allowlist) — a 3B
    # cannot carry reliable tool calls; its job is instant replies.
    nano_meta = {
        "description": MODEL_DESCRIPTIONS["nano"],
        "capabilities": {
            "builtin_tools": False,
            "web_search": False,
            "code_interpreter": False,
            "image_generation": False,
            "vision": False,
            "citations": False,
            "status_updates": False,
            "file_context": True,
        },
        "default_feature_ids": [],
        # OWUI defaults every unlisted builtinTools category to True — without
        # this map nano inherits the full grammar despite the "no tools" design.
        "builtinTools": {k: False for k in META_BUILTINTOOLS},
    }
    c.execute(
        "INSERT OR REPLACE INTO model (id, user_id, base_model_id, name, params, meta, updated_at, created_at, is_active) "
        "VALUES (?, ?, NULL, ?, ?, ?, ?, COALESCE((SELECT created_at FROM model WHERE id = 'nano'), ?), 1)",
        (
            "nano",
            "db2358ef-3324-4bf9-95b1-0e2c8a714fad",
            MODEL_NAMES["nano"],
            json.dumps(
                {
                    "system": NANO_PROMPT,
                    "function_calling": "native",
                    "max_tokens": MAX_TOKENS["nano"],
                    "temperature": 0.6,
                }
            ),
            json.dumps(nano_meta),
            now(),
            now(),
        ),
    )
    print("ensured nano workspace model")

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
