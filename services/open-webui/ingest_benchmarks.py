#!/usr/bin/env python3
"""Ingest benchmark files into an Open-WebUI knowledge collection."""

import csv
import json
import os
import requests
import shutil
import sqlite3
import time
import uuid

BASE = "http://127.0.0.1:3000"
EMAIL = "admin@localhost"
PASSWORD = "admin"
COLLECTION_ID = "200a4f3f-d7d7-40e2-ac27-2e8b2a85eec8"  # Project Decisions & Architecture

# Files to ingest
BENCHMARK_FILES = [
    "/tmp/benchmarks/qwen3/results_v2d.csv",
    "/tmp/benchmarks/qwen3/matrix_v2d.jsonl",
    "/tmp/benchmarks/qwen3/results_v2c.csv",
    "/tmp/benchmarks/qwen3/matrix_v2c.jsonl",
]


def signin():
    r = requests.post(f"{BASE}/api/v1/auths/signin", json={"email": EMAIL, "password": PASSWORD})
    r.raise_for_status()
    return r.json()["token"]


def upload_file(token, path):
    headers = {"Authorization": f"Bearer {token}"}
    with open(path, "rb") as f:
        r = requests.post(f"{BASE}/api/v1/files/", files={"file": f}, headers=headers)
    r.raise_for_status()
    return r.json()


def add_to_knowledge(token, knowledge_id, file_id):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(
        f"{BASE}/api/v1/knowledge/{knowledge_id}/file/add",
        json={"file_id": file_id},
        headers=headers,
    )
    if not r.ok:
        print("add error:", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


def process_file(token, file_id):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(
        f"{BASE}/api/v1/retrieval/process/file",
        json={"file_id": file_id},
        headers=headers,
    )
    r.raise_for_status()
    return r.json()


def prepare_files():
    """Convert JSONL to text and copy CSVs to a staging dir inside the container."""
    staging = "/tmp/benchmarks_ingest"
    os.makedirs(staging, exist_ok=True)
    out_paths = []

    for p in BENCHMARK_FILES:
        if not os.path.exists(p):
            print(f"Missing {p}, skipping")
            continue
        base = os.path.basename(p)
        if base.endswith(".jsonl"):
            # Convert JSONL to a readable text/Markdown file
            txt_path = os.path.join(staging, base.replace(".jsonl", ".txt"))
            with open(p, "r") as f, open(txt_path, "w") as out:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    obj = json.loads(line)
                    out.write(json.dumps(obj, indent=2))
                    out.write("\n---\n")
            out_paths.append(txt_path)
        elif base.endswith(".csv"):
            # Convert CSV to Markdown table for better RAG extraction
            md_path = os.path.join(staging, base.replace(".csv", ".md"))
            with open(p, "r", newline="") as f, open(md_path, "w") as out:
                reader = csv.reader(f)
                rows = list(reader)
                if rows:
                    out.write(f"# {base}\n\n")
                    out.write("| " + " | ".join(rows[0]) + " |\n")
                    out.write("| " + " | ".join(["---"] * len(rows[0])) + " |\n")
                    for row in rows[1:]:
                        out.write("| " + " | ".join(row) + " |\n")
            out_paths.append(md_path)
        else:
            dest = os.path.join(staging, base)
            shutil.copy2(p, dest)
            out_paths.append(dest)

    return out_paths


def main():
    token = signin()
    print("Signed in")

    paths = prepare_files()
    print(f"Prepared {len(paths)} files: {paths}")

    for path in paths:
        print(f"Uploading {path} ...")
        file_meta = upload_file(token, path)
        file_id = file_meta["id"]
        print(f"  file_id: {file_id}")

        print("  processing / extracting content ...")
        result = process_file(token, file_id)
        print(f"  process result: {result}")

        print("  adding to knowledge collection ...")
        add_to_knowledge(token, COLLECTION_ID, file_id)

    print("Ingestion complete.")


if __name__ == "__main__":
    main()
