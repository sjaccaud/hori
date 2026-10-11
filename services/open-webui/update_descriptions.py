#!/usr/bin/env python3
"""Update Open-WebUI model display names and descriptions to use-case-focused labels.

Technical model IDs (qwen-fast, etc.) are preserved; only the UI name and
description text change. Run this after fix_tooling.py if you only need to
refresh display text without touching prompts or feature toggles.
"""
import json
import sqlite3
import sys
import time

DB = sys.argv[1] if len(sys.argv) > 1 else "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

# Display names mapped by use case, not raw quant/ctx jargon.
names = {
    'qwen-fast': 'Quick Chat & Planning',
    'qwen-long': 'Long Document Analysis (65k)',
    'qwen-128k': 'Deep Strategy & Backlogs (128k)',
    'qwen-256k-batch': 'Batch Ingestion (256k)',
    'qwen-think': 'Step-by-Step Reasoning',
    'qwen-balanced': 'Balanced Coding & Research',
    'qwen-quality': 'High-Stakes Coding',
    'devstral-coding': 'Code & Execute (Jacko)',
    'qwen-dsh-fast': 'DSH Coding (no thinking)',
    'qwen-dsh-low': 'DSH Coding (low reasoning)',
    'qwen-dsh-medium': 'DSH Coding (medium reasoning)',
}

descriptions = {
    'qwen-fast': 'Fast default chat, planning, and quick answers with local-tool access.',
    'qwen-long': 'Long-document analysis and summarization up to 65k context.',
    'qwen-128k': 'Deep strategy, roadmap/backlog work, and multi-turn planning up to 128k context.',
    'qwen-256k-batch': 'Batch document ingestion only. Not for interactive chat.',
    'qwen-think': 'Explicit chain-of-thought reasoning and problem deconstruction.',
    'qwen-balanced': 'Balanced quality/VRAM for coding and research tasks.',
    'qwen-quality': 'Highest-quality Qwen for high-stakes coding and reasoning.',
    'devstral-coding': 'Fast coding assistant with safe command execution on Jacko\'s machine.',
    'qwen-dsh-fast': 'Qwen3.8 coding preset with reasoning off and MTP-2 speculative decode.',
    'qwen-dsh-low': 'Qwen3.8 coding preset with low reasoning effort (best output quality).',
    'qwen-dsh-medium': 'Qwen3.8 coding preset with medium reasoning effort (fastest).',
}

def now():
    return int(time.time())

for mid in names:
    c.execute('SELECT name, meta FROM model WHERE id = ?', (mid,))
    row = c.fetchone()
    if not row:
        continue
    name, meta_raw = row
    meta = json.loads(meta_raw) if meta_raw else {}
    meta['description'] = descriptions[mid]
    c.execute('UPDATE model SET name = ?, meta = ?, updated_at = ? WHERE id = ?',
              (names[mid], json.dumps(meta, ensure_ascii=False), now(), mid))
    print(f'updated {mid} -> {names[mid]}')

conn.commit()
conn.close()
