#!/usr/bin/env python3
"""Increase RAG top_k and disable relevance threshold so benchmark chunks are included."""

import json
import sqlite3
import time

DB = "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

def now():
    return int(time.time())

c.execute("SELECT value FROM config WHERE key = 'rag.top_k'")
val = c.fetchone()[0]
print(f"old rag.top_k: {val}")

c.execute("SELECT value FROM config WHERE key = 'rag.relevance_threshold'")
val = c.fetchone()[0]
print(f"old rag.relevance_threshold: {val}")

c.execute("UPDATE config SET value = ?, updated_at = ? WHERE key = 'rag.top_k'", (10, now()))
c.execute("UPDATE config SET value = ?, updated_at = ? WHERE key = 'rag.relevance_threshold'", (0.0, now()))
c.execute("UPDATE config SET value = ?, updated_at = ? WHERE key = 'rag.top_k_reranker'", (10, now()))

conn.commit()
conn.close()
print("RAG config updated: top_k=10, relevance_threshold=0.0, top_k_reranker=10")
