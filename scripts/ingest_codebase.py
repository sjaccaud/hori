"""
Ingest AIOS's own codebase into the aios_longterm memory tier.

This gives AIOS self-awareness: it can answer questions about its own architecture,
reason about changes, and understand how its components fit together.

Each Python file is chunked by function/class and stored with metadata about
its path, module, and key symbols. Markdown docs are chunked by section.

Usage:
    PYTHONPATH=. ./venv/bin/python3 scripts/ingest_codebase.py
"""
import ast
import hashlib
import logging
import os
from pathlib import Path
from typing import List

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
EMBED_URL = os.getenv("EMBED_URL", "http://localhost:8081/v1/embeddings")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text-v1.5.Q8_0")
COLLECTION = "aios_longterm"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Directories to ingest
SOURCE_DIRS = [
    PROJECT_ROOT / "services" / "aios_core",
    PROJECT_ROOT / "services" / "red_teaming",
    PROJECT_ROOT / "services" / "recovery",
    PROJECT_ROOT / "services" / "telemetry",
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "core",
    PROJECT_ROOT / "tests",
]

DOC_DIRS = [
    PROJECT_ROOT / "docs",
]

# Skip patterns
SKIP_FILES = {"__pycache__", ".pyc", "test_", "conftest", "__init__"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def get_embedding(text: str) -> List[float]:
    payload = {"model": EMBED_MODEL, "input": text}
    response = requests.post(EMBED_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def should_skip(path: Path) -> bool:
    name = path.name
    if "__pycache__" in str(path):
        return True
    if name.endswith(".pyc"):
        return True
    if name.startswith("test_") or name == "conftest.py":
        return True
    return False


def chunk_python_file(path: Path) -> List[dict]:
    """Parse a Python file and chunk it by function/class definitions."""
    try:
        source = path.read_text()
        tree = ast.parse(source)
    except Exception as e:
        logger.warning(f"Could not parse {path}: {e}")
        return []

    chunks = []
    rel_path = str(path.relative_to(PROJECT_ROOT))

    # Module-level docstring
    if (ast.get_docstring(tree) or "").strip():
        chunks.append({
            "content": f"# {rel_path}\n\nModule docstring:\n{ast.get_docstring(tree)}",
            "symbol": "__module__",
            "type": "module_doc",
        })

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol = node.name
            docstring = ast.get_docstring(node) or ""
            # Get the function signature and first few lines of body
            try:
                start_line = node.lineno - 1
                end_line = min(start_line + 30, len(source.splitlines()))
                lines = source.splitlines()[start_line:end_line]
                code = "\n".join(lines)
            except Exception:
                code = f"def {symbol}(...): ..."

            content = f"# {rel_path} :: {symbol}\n\n```python\n{code}\n```"
            if docstring:
                content += f"\n\nDocstring: {docstring}"
            chunks.append({"content": content, "symbol": symbol, "type": "function"})

        elif isinstance(node, ast.ClassDef):
            symbol = node.name
            docstring = ast.get_docstring(node) or ""
            # Get class definition and methods
            methods = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(child.name)

            try:
                start_line = node.lineno - 1
                end_line = min(start_line + 50, len(source.splitlines()))
                lines = source.splitlines()[start_line:end_line]
                code = "\n".join(lines)
            except Exception:
                code = f"class {symbol}: ..."

            content = f"# {rel_path} :: class {symbol}\n\n```python\n{code}\n```"
            if docstring:
                content += f"\n\nDocstring: {docstring}"
            if methods:
                content += f"\n\nMethods: {', '.join(methods)}"
            chunks.append({"content": content, "symbol": symbol, "type": "class"})

    return chunks


def chunk_markdown_file(path: Path) -> List[dict]:
    """Chunk a markdown file by section (## headers)."""
    text = path.read_text()
    rel_path = str(path.relative_to(PROJECT_ROOT))

    # Split by ## headers
    sections = []
    current_header = "Introduction"
    current_content = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_content:
                sections.append((current_header, "\n".join(current_content)))
            current_header = line[3:].strip()
            current_content = [f"# {rel_path} :: {current_header}", ""]
        else:
            current_content.append(line)

    if current_content:
        sections.append((current_header, "\n".join(current_content)))

    return [
        {"content": content, "symbol": header, "type": "doc_section"}
        for header, content in sections
        if len(content.strip()) > 20
    ]


def ingest():
    client = QdrantClient(url=QDRANT_URL)

    # Ensure collection exists
    try:
        client.get_collection(collection_name=COLLECTION)
    except Exception:
        logger.info(f"Creating collection: {COLLECTION}")
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE),
        )

    total_ingested = 0

    # Ingest Python source files
    for src_dir in SOURCE_DIRS:
        if not src_dir.exists():
            continue
        for py_file in src_dir.rglob("*.py"):
            if should_skip(py_file):
                continue
            chunks = chunk_python_file(py_file)
            if not chunks:
                continue
            logger.info(f"  {py_file.relative_to(PROJECT_ROOT)}: {len(chunks)} chunks")
            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(f"codebase_{py_file}_{i}".encode()).hexdigest()
                try:
                    vector = get_embedding(chunk["content"][:2000])
                except Exception as e:
                    logger.warning(f"Failed to embed: {e}")
                    continue
                payload = {
                    "content": chunk["content"][:2000],
                    "role": "system",
                    "conversation_id": "codebase",
                    "surface": "codebase_ingestion",
                    "tier": "longterm",
                    "source_file": str(py_file.relative_to(PROJECT_ROOT)),
                    "symbol": chunk["symbol"],
                    "symbol_type": chunk["type"],
                    "chunk_index": i,
                    "topics": ["codebase", py_file.stem],
                    "tone_tags": [],
                    "directionality": "",
                    "edges": [],
                }
                client.upsert(
                    collection_name=COLLECTION,
                    points=[models.PointStruct(id=doc_id, vector=vector, payload=payload)],
                )
                total_ingested += 1

    # Ingest markdown docs
    for doc_dir in DOC_DIRS:
        if not doc_dir.exists():
            continue
        for md_file in doc_dir.glob("*.md"):
            chunks = chunk_markdown_file(md_file)
            if not chunks:
                continue
            logger.info(f"  {md_file.relative_to(PROJECT_ROOT)}: {len(chunks)} chunks")
            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(f"codebase_{md_file}_{i}".encode()).hexdigest()
                try:
                    vector = get_embedding(chunk["content"][:2000])
                except Exception as e:
                    logger.warning(f"Failed to embed: {e}")
                    continue
                payload = {
                    "content": chunk["content"][:2000],
                    "role": "system",
                    "conversation_id": "codebase",
                    "surface": "codebase_ingestion",
                    "tier": "longterm",
                    "source_file": str(md_file.relative_to(PROJECT_ROOT)),
                    "symbol": chunk["symbol"],
                    "symbol_type": chunk["type"],
                    "chunk_index": i,
                    "topics": ["codebase", "docs", md_file.stem],
                    "tone_tags": [],
                    "directionality": "",
                    "edges": [],
                }
                client.upsert(
                    collection_name=COLLECTION,
                    points=[models.PointStruct(id=doc_id, vector=vector, payload=payload)],
                )
                total_ingested += 1

    logger.info(f"Codebase ingestion complete. {total_ingested} chunks stored in {COLLECTION}.")


if __name__ == "__main__":
    ingest()
