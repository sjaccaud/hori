"""Sync curated stable documents into Open-WebUI's knowledge base.

Only the project-decisions path remains: it renders curated architecture/
roadmap/decision sources into a Markdown document uploaded to the
"Project Decisions & Architecture" knowledge collection — stable
reference material where RAG retrieval is appropriate.

RETIRED (2026-09-23): the system-state path (render_system_state_md /
sync into "Local Benchmarks") — a synced document can never be trusted
for "what's our status" questions: nothing automated the sync (docs went
stale immediately) and retrieval is probabilistic. Current state is now
delivered by services/open-webui/state_snapshot.py (5-min timer) whose
output the tool-server inlet filter injects into every request — see the
open-webui rule file. Do not re-add a knowledge-based state mechanism.

Traces to: .devin/prd/system-state-kb-sync.md (historical)
"""

import io
from pathlib import Path
from typing import Iterable, Optional

import requests


def _default_project_decisions_path() -> Path:
    return Path.home() / "Projects/aios/.devin/project-decisions.md"


def _default_project_decisions_sources() -> list[Path]:
    home = Path.home()
    return [
        home / "Projects/aios/docs/manifesto.md",
        home / "Projects/aios/docs/safety.md",
        home / "Projects/aios/docs/roadmap.md",
        _default_project_decisions_path(),
    ]


def _default_project_decisions_allow_list() -> list[Path]:
    home = Path.home()
    return [
        home / "Projects/aios/docs",
        home / "Projects/aios/.devin",
    ]


def _validate_path(path: Path, allow_list: Iterable[Path]) -> None:
    """Raise ValueError if path is outside the configured allow-list.

    Symlinks and relative path tricks (e.g. /etc/../etc/shadow) are resolved
    before the prefix check, so the comparison is against the canonical
    filesystem location.
    """
    resolved = path.resolve()
    for allowed in allow_list:
        allowed_resolved = allowed.resolve()
        if resolved == allowed_resolved:
            return
        if allowed_resolved.is_dir() and resolved.is_relative_to(allowed_resolved):
            return
    raise ValueError(f"Path {path} is outside the configured allow-list.")


def render_project_decisions_md(
    sources: list[Path],
    allow_list: Optional[Iterable[Path]] = None,
) -> str:
    """Return a markdown synthesis of architecture/roadmap/decision sources.

    Each source is included under a '## Source: <path>' heading. Raises
    ValueError if any source is outside the configured allow-list.
    """
    if allow_list is None:
        allow_list = _default_project_decisions_allow_list()

    for source in sources:
        _validate_path(source, allow_list)

    parts = ["# Project Decisions\n\n"]
    for source in sources:
        if not source.exists():
            raise FileNotFoundError(f"Project-decisions source not found: {source}")
        # Use a path relative to the user's home directory to avoid
        # embedding PII/usernames in the generated Open-WebUI document.
        display = source
        try:
            display = source.relative_to(Path.home())
        except ValueError:
            pass
        parts.append(f"## Source: {display}\n\n")
        parts.append(source.read_text(encoding="utf-8", errors="replace"))
        parts.append("\n\n")

    return "".join(parts)


def _signin(base_url: str, admin_email: str, admin_password: str) -> str:
    r = requests.post(
        f"{base_url}/api/v1/auths/signin",
        json={"email": admin_email, "password": admin_password},
    )
    r.raise_for_status()
    return r.json()["token"]


def _find_knowledge(token: str, base_url: str, collection_name: str) -> Optional[str]:
    r = requests.get(
        f"{base_url}/api/v1/knowledge/", headers={"Authorization": f"Bearer {token}"}
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        items = data.get("items") or data.get("data") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    for k in items:
        if k.get("name") == collection_name:
            return k["id"]
    return None


def _create_knowledge(token: str, base_url: str, collection_name: str) -> str:
    r = requests.post(
        f"{base_url}/api/v1/knowledge/create",
        json={"name": collection_name, "description": "", "data": {"status": True}},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return r.json()["id"]


def _find_file_in_collection(
    token: str, base_url: str, knowledge_id: str, filename: str
) -> Optional[str]:
    r = requests.get(
        f"{base_url}/api/v1/knowledge/{knowledge_id}/files",
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        files = data.get("items") or data.get("files") or data.get("data") or []
    elif isinstance(data, list):
        files = data
    else:
        files = []
    for f in files:
        if f.get("filename") == filename:
            return f["id"]
    return None


def _delete_file(token: str, base_url: str, file_id: str) -> None:
    r = requests.delete(
        f"{base_url}/api/v1/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()


def _upload_file(token: str, base_url: str, filename: str, markdown: str) -> dict:
    r = requests.post(
        f"{base_url}/api/v1/files/",
        files={
            "file": (filename, io.BytesIO(markdown.encode("utf-8")), "text/markdown")
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return r.json()


def _process_file(token: str, base_url: str, file_id: str) -> None:
    r = requests.post(
        f"{base_url}/api/v1/retrieval/process/file",
        json={"file_id": file_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()


def _add_file_to_knowledge(
    token: str, base_url: str, knowledge_id: str, file_id: str
) -> None:
    r = requests.post(
        f"{base_url}/api/v1/knowledge/{knowledge_id}/file/add",
        json={"file_id": file_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()


def upsert_knowledge_file(
    markdown: str,
    filename: str = "aios-system-state.md",
    collection_name: str = "Local Benchmarks",
    base_url: str = "http://127.0.0.1:3000",
    admin_email: str = "admin@localhost",
    admin_password: str = "admin",
) -> dict:
    """Upload or replace a knowledge file and return file metadata."""
    token = _signin(base_url, admin_email, admin_password)

    knowledge_id = _find_knowledge(token, base_url, collection_name)
    if not knowledge_id:
        knowledge_id = _create_knowledge(token, base_url, collection_name)

    existing_file_id = _find_file_in_collection(token, base_url, knowledge_id, filename)
    if existing_file_id:
        _delete_file(token, base_url, existing_file_id)

    file_meta = _upload_file(token, base_url, filename, markdown)
    file_id = file_meta["id"]

    _process_file(token, base_url, file_id)
    _add_file_to_knowledge(token, base_url, knowledge_id, file_id)

    return file_meta


def sync_project_decisions() -> str:
    """Render and upsert the curated project-decisions file."""
    sources = _default_project_decisions_sources()
    markdown = render_project_decisions_md(sources)
    file_meta = upsert_knowledge_file(
        markdown,
        filename="project-decisions.md",
        collection_name="Project Decisions & Architecture",
    )
    return file_meta["id"]
