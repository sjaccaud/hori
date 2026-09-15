#!/usr/bin/env python3
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

DB = sys.argv[1] if len(sys.argv) > 1 else "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

# Context lengths are read from the llama-server router.ini so there is
# one source of truth. Measured 2026-09-07 on the R9700 (31.9 GiB): qwen35
# hybrid KV costs ~25.3 KB/token at q4_0, draft-mtp reserves ~1.8 GiB,
# Devstral f16 KV ~160 KB/token, and the GPU-resident nano server
# (llama-nano.service, 32k ctx) adds a fixed ~3 GiB alongside whichever
# preset is loaded. qwen-quality is pinned at 16k: Q8_0 weights + q4_0 KV,
# no spec, ~26.5 GiB.

ROUTER_INI = Path.home() / "llama-builds/mainline-adaptive-mtp-10/router.ini"

# The local nano server is not in router.ini, so keep a tiny fallback table
# for aliases that live outside the router.
FALLBACK_CONTEXTS = {
    "nano": 32768,
}


def _parse_router_contexts(router_ini: Path) -> dict[str, int]:
    """Return {alias: ctx_size} from a llama-server router/preset INI.

    If a section has both a section name and an `alias` key, the alias wins
    so it maps cleanly to Open-WebUI model ids.
    """
    contexts: dict[str, int] = {}
    section: str | None = None
    alias: str | None = None
    ctx: int | None = None

    with router_ini.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            section_match = re.match(r"^\[(.+?)\]$", line)
            if section_match:
                # Flush previous section before moving on.
                if section and ctx is not None:
                    contexts[alias or section] = ctx
                section = section_match.group(1)
                alias = None
                ctx = None
                continue

            if section is None or "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            # Strip inline comments.
            value = value.split(";")[0].strip()

            if key == "alias":
                alias = value
            elif key == "c":
                try:
                    ctx = int(value)
                except ValueError:
                    ctx = None

    if section and ctx is not None:
        contexts[alias or section] = ctx

    return contexts


if ROUTER_INI.exists():
    contexts = _parse_router_contexts(ROUTER_INI)
else:
    contexts = {}

contexts.update(FALLBACK_CONTEXTS)

# Completion cap per alias. Reasoning presets and the long-doc/batch tiers get
# 16384 because chain-of-thought tokens share the same max_tokens budget;
# interactive presets get 8192; nano stays small for instant replies.
max_tokens = {
    'qwen-fast': 8192,
    'qwen-think': 16384,
    'qwen-balanced': 8192,
    'qwen-quality': 8192,
    'qwen-long': 8192,
    'qwen-128k': 16384,
    'qwen-256k-batch': 16384,
    'devstral-coding': 8192,
    'qwen-dsh-fast': 8192,
    'qwen-dsh-low': 16384,
    'qwen-dsh-medium': 16384,
    'qwen-deepdive': 16384,
    'qwen-free': 16384,
    'nano': 2048,
}

for mid, ctx in contexts.items():
    c.execute('SELECT params FROM model WHERE id = ?', (mid,))
    row = c.fetchone()
    if not row:
        continue
    params = json.loads(row[0])
    params['context_length'] = ctx
    params['num_ctx'] = ctx
    params['max_tokens'] = max_tokens.get(mid, 4096)
    c.execute('UPDATE model SET params = ?, updated_at = ? WHERE id = ?',
              (json.dumps(params, ensure_ascii=False), now(), mid))
    print(f'updated {mid} context_length={ctx} num_ctx={ctx} max_tokens={params["max_tokens"]}')

conn.commit()
conn.close()
