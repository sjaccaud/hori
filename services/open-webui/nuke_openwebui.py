#!/usr/bin/env python3
"""Hard nuke Open-WebUI config/chats and clean up model/derived state."""

import json
import sqlite3
import time
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

# 1. Remove all UI/user config and chat state
tables_to_clear = ['config', 'chat', 'message', 'prompt_history', 'chat_message', 'chat_message_reaction']
for tbl in tables_to_clear:
    try:
        c.execute(f'DELETE FROM {tbl}')
        print(f'cleared {tbl}: {c.rowcount} rows')
    except sqlite3.OperationalError as e:
        print(f'skip {tbl}: {e}')

# 2. Remove broken/derived models
for mid in ['quick-plan', 'writer', 'rocm-support', 'review']:
    c.execute('DELETE FROM model WHERE id = ?', (mid,))
    print(f'deleted model {mid}: {c.rowcount}')

# 3. Deduplicate knowledge_file (one link per file/knowledge)
c.execute('''
    DELETE FROM knowledge_file
    WHERE rowid NOT IN (
        SELECT MIN(rowid)
        FROM knowledge_file
        GROUP BY knowledge_id, file_id
    )
''')
print(f'deduped knowledge_file: {c.rowcount} removed')

# 4. Ensure remaining core models are correctly configured
legacy_prompt = (
    "You are an expert software engineering and research assistant. "
    "When knowledge from the attached collections is provided in the prompt, use it as the authoritative source and cite it with [id]. "
    "When the user asks about Jacko's projects, hardware, preferences, background, or benchmarks, use the attached knowledge collections and cite sources with [id]. "
    "Prefer concise, accurate, and well-reasoned answers. If a task is complex, think step by step before answering. "
    "Do not preface or qualify answers with meta-commentary. Just answer directly. "
    "Be concise: prefer short answers and avoid unnecessary lists or preamble."
)

native_prompt = (
    "You are an expert software engineering, coding, and research assistant. "
    "When the user asks about Jacko's projects, hardware, preferences, background, or benchmarks, first use the query_knowledge_files tool to search the attached knowledge collections and cite sources with [id]. "
    "If the knowledge collections do not contain the answer, then you may use web search (only if enabled). "
    "When web search results or memory context are provided, use them and cite with [id]. "
    "Be precise and avoid unnecessary preamble."
)

legacy_models = ['qwen-fast', 'qwen-long', 'qwen-think', 'qwen-balanced']
native_models = ['qwen-quality', 'devstral-coding']

for mid in legacy_models:
    c.execute('SELECT params, meta FROM model WHERE id = ?', (mid,))
    row = c.fetchone()
    if not row:
        continue
    params = json.loads(row[0])
    meta = json.loads(row[1])
    params['system'] = legacy_prompt
    params['function_calling'] = 'legacy'
    if 'max_tokens' not in params:
        params['max_tokens'] = 4096
    meta['default_feature_ids'] = ['knowledge', 'memory']
    meta['capabilities'] = {'web_search': False, 'knowledge': True, 'memory': True, 'builtin_tools': True}
    meta['builtinTools'] = {'knowledge': True, 'time': True}
    c.execute('UPDATE model SET params = ?, meta = ?, updated_at = ? WHERE id = ?',
              (json.dumps(params, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), now(), mid))
    print(f'updated legacy {mid}')

for mid in native_models:
    c.execute('SELECT params, meta FROM model WHERE id = ?', (mid,))
    row = c.fetchone()
    if not row:
        continue
    params = json.loads(row[0])
    meta = json.loads(row[1])
    params['system'] = native_prompt
    params['function_calling'] = 'native'
    if 'max_tokens' not in params:
        params['max_tokens'] = 4096
    meta['default_feature_ids'] = ['knowledge', 'web_search', 'memory']
    meta['capabilities'] = {'web_search': True, 'knowledge': True, 'memory': True, 'builtin_tools': True}
    meta['builtinTools'] = {'knowledge': True, 'time': True}
    c.execute('UPDATE model SET params = ?, meta = ?, updated_at = ? WHERE id = ?',
              (json.dumps(params, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), now(), mid))
    print(f'updated native {mid}')

conn.commit()
conn.close()
print('Nuke complete.')
