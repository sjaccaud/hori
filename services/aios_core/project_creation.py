"""Safe project creation for the HORI assistant.

This module gives `aios_core` the ability to create a new project skeleton
under a configured parent directory (default `~/Projects`). It is deliberately
NOT a tool in the read-only tool_daemon spine; it is a high-level workflow
that:

- Generates a dry-run manifest for user review.
- Requires explicit confirmation before writing anything.
- Confines all writes to the allowed parent directory.
- Keeps an audit log.
- Provides a short rollback window.

Traces to: docs/manifesto.md Pillar VII (Engineering Discipline),
           docs/safety.md "The user is always in the loop."
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Default parent for all project creation. Can be overridden per-request.
DEFAULT_PARENT = os.environ.get("AIOS_PROJECTS_DIR", os.path.expanduser("~/Projects"))

# How long a pending proposal is valid, and how long after creation a rollback
# is allowed (seconds).
PENDING_TTL = 600
ROLLBACK_WINDOW_SECONDS = 300

# Persistent state for pending proposals and audit trail.
AIOS_DATA_DIR = Path(os.environ.get("AIOS_DATA_DIR", os.path.expanduser("~/.local/share/aios")))
PENDING_FILE = AIOS_DATA_DIR / "pending_projects.json"
AUDIT_FILE = AIOS_DATA_DIR / "project_creation_audit.jsonl"


@dataclass
class ProjectFile:
    """A single file or directory in the project manifest."""
    path: str  # relative to project root
    content: str = ""
    is_dir: bool = False


@dataclass
class ProjectProposal:
    """A pending project creation proposal."""
    token: str
    name: str
    parent_dir: str
    description: str
    template: str
    manifest: List[ProjectFile] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


def _data_dir() -> Path:
    """Ensure the AIOS data directory exists and return it."""
    AIOS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return AIOS_DATA_DIR


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning(f"Failed to load {path}; starting fresh.")
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _load_pending() -> dict:
    return _load_json(PENDING_FILE)


def _save_pending(pending: dict) -> None:
    _data_dir()
    _save_json(PENDING_FILE, pending)


def _generate_token() -> str:
    """Return a short, URL-safe token."""
    return secrets.token_urlsafe(12)


def _resolve_parent_dir(parent_dir: Optional[str]) -> Path:
    """Resolve and canonicalize the parent directory."""
    raw = parent_dir or DEFAULT_PARENT
    # Expand ~ and environment variables
    expanded = os.path.expanduser(os.path.expandvars(raw))
    return Path(expanded).resolve()


def _is_allowed_parent(parent: Path, allowed: Path) -> bool:
    """Return True if `parent` is `allowed` or a subdirectory of it."""
    try:
        parent.relative_to(allowed)
        return True
    except ValueError:
        return False


def _safe_name(name: str) -> str:
    """Sanitize a project name into a filesystem-safe directory name."""
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9_\- ]+", "", name)
    name = re.sub(r"\s+", "-", name).strip("-")
    if not name:
        raise ValueError("Project name is empty after sanitization")
    return name


def _generate_manifest(name: str, description: str, template: str) -> List[ProjectFile]:
    """Generate a manifest of files for the requested template."""
    manifest: List[ProjectFile] = []

    if template == "empty":
        manifest.append(ProjectFile(path="README.md", content=_readme(name, description)))

    elif template == "python-cli":
        pkg = name.replace("-", "_")
        manifest = [
            ProjectFile(path="README.md", content=_readme(name, description)),
            ProjectFile(path=".gitignore", content=_gitignore()),
            ProjectFile(path="pyproject.toml", content=_pyproject_toml(name, pkg, description)),
            ProjectFile(path=f"src/{pkg}/__init__.py", content="""__version__ = "0.1.0"\n"""),
            ProjectFile(path=f"src/{pkg}/cli.py", content=_python_cli(pkg)),
            ProjectFile(path="tests/__init__.py", content=""),
            ProjectFile(path="tests/test_basic.py", content=_python_test(pkg)),
        ]

    elif template == "python-package":
        pkg = name.replace("-", "_")
        manifest = [
            ProjectFile(path="README.md", content=_readme(name, description)),
            ProjectFile(path=".gitignore", content=_gitignore()),
            ProjectFile(path="pyproject.toml", content=_pyproject_toml(name, pkg, description)),
            ProjectFile(path=f"src/{pkg}/__init__.py", content="""__version__ = "0.1.0"\n"""),
            ProjectFile(path=f"src/{pkg}/core.py", content="""# Core module for {pkg}\n"""),
            ProjectFile(path="tests/__init__.py", content=""),
            ProjectFile(path="tests/test_basic.py", content=_python_test(pkg)),
        ]

    else:
        raise ValueError(f"Unknown template: {template}")

    # Prepend directory entries for each directory that contains files.
    dirs = sorted({str(Path(f.path).parent) for f in manifest if not f.is_dir})
    for d in dirs:
        if d != "." and d not in {f.path for f in manifest}:
            manifest.insert(0, ProjectFile(path=d, is_dir=True))

    return manifest


def _readme(name: str, description: str) -> str:
    return f"""# {name}

{description}

## Getting started

See the project files for setup and usage instructions.
"""


def _gitignore() -> str:
    return """# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/

# IDEs
.vscode/
.idea/

# Build artifacts
build/
dist/

# OS
.DS_Store
Thumbs.db
"""


def _pyproject_toml(name: str, pkg: str, description: str) -> str:
    return f"""[project]
name = "{name}"
description = "{description}"
version = "0.1.0"
readme = "README.md"
requires-python = ">=3.10"

[project.scripts]
{name} = "{pkg}.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


def _python_cli(pkg: str) -> str:
    return f"""import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="{pkg} CLI")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    args = parser.parse_args()
    print("Hello from {pkg}!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def _python_test(pkg: str) -> str:
    return f"""from {pkg}.cli import main


def test_main_smoke():
    assert main() == 0
"""


def _proposal_to_dict(proposal: ProjectProposal) -> dict:
    return {
        "token": proposal.token,
        "name": proposal.name,
        "parent_dir": proposal.parent_dir,
        "description": proposal.description,
        "template": proposal.template,
        "manifest": [
            {"path": f.path, "content": f.content, "is_dir": f.is_dir}
            for f in proposal.manifest
        ],
        "created_at": proposal.created_at,
    }


def _dict_to_proposal(data: dict) -> ProjectProposal:
    return ProjectProposal(
        token=data["token"],
        name=data["name"],
        parent_dir=data["parent_dir"],
        description=data["description"],
        template=data["template"],
        manifest=[
            ProjectFile(path=f["path"], content=f.get("content", ""), is_dir=f.get("is_dir", False))
            for f in data["manifest"]
        ],
        created_at=data["created_at"],
    )


def propose_project(
    name: str,
    description: str,
    parent_dir: Optional[str] = None,
    template: str = "python-cli",
) -> dict:
    """Propose a new project. Returns a manifest and a confirmation token.

    No files are written. The token can be passed to `create_project`.
    """
    allowed_parent = _resolve_parent_dir(DEFAULT_PARENT)
    requested_parent = _resolve_parent_dir(parent_dir)

    if not _is_allowed_parent(requested_parent, allowed_parent):
        return {"error": f"Project parent must be under {allowed_parent}."}

    try:
        safe_name = _safe_name(name)
    except ValueError as e:
        return {"error": str(e)}

    project_root = requested_parent / safe_name
    if project_root.exists():
        return {
            "error": f"Project path already exists: {project_root}",
            "dry_run": True,
        }

    manifest = _generate_manifest(safe_name, description, template)

    proposal = ProjectProposal(
        token=_generate_token(),
        name=safe_name,
        parent_dir=str(requested_parent),
        description=description,
        template=template,
        manifest=manifest,
    )

    pending = _load_pending()
    # Reap stale pending proposals
    now = time.time()
    pending = {k: v for k, v in pending.items() if now - v["created_at"] < PENDING_TTL}
    pending[proposal.token] = _proposal_to_dict(proposal)
    _save_pending(pending)

    return {
        "token": proposal.token,
        "name": proposal.name,
        "parent_dir": proposal.parent_dir,
        "template": proposal.template,
        "project_root": str(project_root),
        "manifest": [
            {"path": f.path, "content": f.content, "is_dir": f.is_dir}
            for f in manifest
        ],
        "dry_run": True,
        "requires_confirmation": True,
    }


def _append_audit(entry: dict) -> None:
    _data_dir()
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def create_project(token: str, confirmed: bool = False) -> dict:
    """Create a project from a previously generated proposal.

    If `confirmed` is False, the call is treated as a dry-run and no files
    are written.
    """
    pending = _load_pending()
    data = pending.get(token)
    if not data:
        return {"error": "No pending project found for that token."}

    now = time.time()
    if now - data["created_at"] > PENDING_TTL:
        return {"error": "Project proposal has expired. Please propose again."}

    proposal = _dict_to_proposal(data)
    project_root = Path(proposal.parent_dir) / proposal.name

    if not _is_allowed_parent(project_root.parent, _resolve_parent_dir(DEFAULT_PARENT)):
        return {"error": "Project path is outside the allowed parent directory."}

    if project_root.exists():
        return {"error": f"Project path already exists: {project_root}"}

    if not confirmed:
        return {
            "token": proposal.token,
            "name": proposal.name,
            "project_root": str(project_root),
            "manifest": [
                {"path": f.path, "content": f.content, "is_dir": f.is_dir}
                for f in proposal.manifest
            ],
            "dry_run": True,
            "requires_confirmation": True,
        }

    # Write the project
    created_files: List[str] = []
    try:
        for f in proposal.manifest:
            full = project_root / f.path
            if f.is_dir:
                full.mkdir(parents=True, exist_ok=True)
            else:
                full.parent.mkdir(parents=True, exist_ok=True)
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(f.content)
            created_files.append(str(full))
    except OSError as e:
        # Attempt to clean up partial writes
        for path in created_files:
            try:
                p = Path(path)
                if p.is_file():
                    p.unlink()
                elif p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
            except OSError:
                pass
        return {"error": f"Failed to create project: {e}"}

    # Remove from pending
    del pending[token]
    _save_pending(pending)

    # Audit: include the project root explicitly so rollback can remove it.
    entry = {
        "action": "create",
        "token": proposal.token,
        "name": proposal.name,
        "project_root": str(project_root),
        "created_at": time.time(),
        "files": [str(project_root)] + created_files,
    }
    _append_audit(entry)

    return {
        "token": proposal.token,
        "name": proposal.name,
        "project_root": str(project_root),
        "created_files": created_files,
        "created": True,
    }


def rollback_project(token: str) -> dict:
    """Roll back a recently created project. Only allowed within the rollback
    window and only if the audit log confirms it was created by this module.
    """
    if not AUDIT_FILE.exists():
        return {"error": "No audit log found."}

    matching = None
    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("token") == token and entry.get("action") == "create":
                    matching = entry
                    break
            except json.JSONDecodeError:
                continue

    if not matching:
        return {"error": "No creation record found for that token."}

    now = time.time()
    if now - matching["created_at"] > ROLLBACK_WINDOW_SECONDS:
        return {"error": "Rollback window has expired."}

    project_root = Path(matching["project_root"])
    allowed_parent = _resolve_parent_dir(DEFAULT_PARENT)

    if not _is_allowed_parent(project_root, allowed_parent):
        return {"error": "Project root is outside the allowed parent directory."}

    if not project_root.exists():
        return {"error": "Project root does not exist; nothing to roll back."}

    try:
        shutil.rmtree(project_root)
    except OSError as e:
        return {"error": f"Failed to roll back project: {e}"}

    _append_audit({
        "action": "rollback",
        "token": token,
        "created_at": matching["created_at"],
        "rolled_back_at": time.time(),
        "removed": [str(project_root)] + matching.get("files", []),
    })

    return {"rolled_back": True, "removed": [str(project_root)]}


def detect_creation_intent(text: str) -> dict | None:
    """Lightweight keyword/intent detection for project-creation requests.

    Returns a dict with `name`, `description`, `parent_dir`, and `template`
    if the message looks like a project creation request, otherwise None.
    """
    lower = text.lower()
    if not any(phrase in lower for phrase in [
        "create a new project", "create project", "start a new project",
        "start project", "new project",
    ]):
        return None

    # Try to extract a project name from quotes, then from "for a/an ...",
    # "called ...", or "named ...".
    m = re.search(r'"([^"]{1,80})"', text)
    if m:
        name = m.group(1)
    else:
        for pattern in [
            r"(?:for\s+(?:a|an))\s+(.{3,80}?)(?:\s+(?:in\s+|called\s+|named\s+)|$)",
            r"(?:called|named)\s+(.{3,40}?)(?:\s+(?:in\s+)|$)",
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                name = m.group(1).strip(".,; ")
                break
        else:
            name = "new-project"

    # Default description is the rest of the text without the creation phrase
    description = text
    for phrase in [
        "create a new project", "create project", "start a new project",
        "start project", "new project",
    ]:
        description = re.sub(re.escape(phrase), "", description, flags=re.IGNORECASE)
    description = re.sub(r'"[^"]+"', "", description)
    # Remove the parent-dir clause
    description = re.sub(r"\bin\s+[~/A-Za-z0-9_/.\-]+", "", description)
    description = re.sub(r"\s+", " ", description).strip(",. ")
    if not description:
        description = f"Project {name}"

    # Template detection
    template = "empty"
    if "python cli" in lower or "command-line" in lower or "cli" in lower:
        template = "python-cli"
    elif "python" in lower or "package" in lower:
        template = "python-package"

    # Parent dir
    parent = None
    m = re.search(r"in\s+([~/]?[A-Za-z0-9_/.\-]+)", text)
    if m:
        candidate = m.group(1)
        if candidate.startswith("~") or candidate.startswith("/"):
            parent = candidate

    return {
        "name": name,
        "description": description,
        "parent_dir": parent,
        "template": template,
    }


def detect_confirmation(text: str, token: Optional[str] = None) -> bool:
    """Return True if the user message confirms a pending action."""
    lower = text.lower().strip(".! ")
    return (
        lower in {"yes", "yeah", "yep", "y", "ok", "okay", "go", "go ahead", "proceed", "do it", "create it"}
        or (token is not None and token.lower() in lower)
    )
