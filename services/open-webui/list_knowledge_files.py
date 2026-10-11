#!/usr/bin/env python3
import sqlite3

DB = "/app/backend/data/webui.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

for kid in ['139e1d1b-d6e9-449e-9dea-42ac76c97055', '200a4f3f-d7d7-40e2-ac27-2e8b2a85eec8']:
    c.execute('SELECT name FROM knowledge WHERE id = ?', (kid,))
    print('===', c.fetchone()[0], '===')
    c.execute("""
        SELECT f.id, f.filename, f.path
        FROM file f
        JOIN knowledge_file kf ON kf.file_id = f.id
        WHERE kf.knowledge_id = ?
    """, (kid,))
    for row in c.fetchall():
        print(row)

conn.close()
