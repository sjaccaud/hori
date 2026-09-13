"""Confirmation-gated Markdown/directory writes inside a workspace.

This module lets an Open-WebUI-facing tool server propose a small filesystem
manifest, then write it only after the user confirms the returned token. It is
intentionally narrower than general filesystem access: relative paths under a
configured workspace root, directories and ``.md`` files only, create-only, and
recorded in an audit log for rollback.

Traces to: docs/safety.md "Layer 3: Sandboxed Execution" and "Layer 4:
Human-in-the-Loop"; docs/manifesto.md Pillar VII (Engineering Discipline).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(
    os.environ.get("WORKSPACE_TOOLS_ROOT", os.path.expanduser("~/Projects"))
).expanduser().resolve()
AIOS_DATA_DIR = Path(
    os.environ.get("AIOS_DATA_DIR", os.path.expanduser("~/.local/share/aios"))
)
PENDING_FILE = AIOS_DATA_DIR / "pending_workspace_writes.json"
AUDIT_FILE = AIOS_DATA_DIR / "workspace_write_audit.jsonl"

PENDING_TTL = 600
ROLLBACK_WINDOW_SECONDS = 300
MAX_ENTRIES = 100
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024


def _workspace_root() -> Path:
    return Path(WORKSPACE_ROOT).expanduser().resolve()


def _data_dir() -> Path:
    Path(AIOS_DATA_DIR).mkdir(parents=True, exist_ok=True)
    return Path(AIOS_DATA_DIR)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to load %s; starting fresh.", path)
        return {}


def _save_json(path: Path, data: dict) -> None:
    _data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _generate_token() -> str:
    return secrets.token_urlsafe(12)


def _append_audit(entry: dict) -> None:
    _data_dir()
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _reject(reason: str) -> dict:
    return {"error": reason}


def _resolve_workspace_path(raw_path: Any) -> tuple[Path | None, Path | None, str]:
    """Resolve a model-supplied relative path and return (relative, absolute)."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, None, "entry path must be a non-empty relative path"
    if "\x00" in raw_path:
        return None, None, "entry path contains a NUL byte"

    rel = Path(raw_path)
    if rel.is_absolute():
        return None, None, "entry path must be relative to the workspace root"
    if any(part in ("", ".", "..") for part in rel.parts):
        return None, None, "entry path may not contain '.' or '..' components"
    if any(part.startswith("~") for part in rel.parts):
        return None, None, "entry path may not contain '~' components"

    root = _workspace_root()
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return None, None, f"symlink components are not allowed: {raw_path}"

    try:
        resolved = current.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as e:
        return None, None, f"invalid workspace path: {e}"
    return rel, resolved, ""


def _validate_entries(entries: Any) -> tuple[list[dict[str, Any]] | None, str]:
    root = _workspace_root()
    if not root.exists() or not root.is_dir():
        return None, f"workspace root is not a directory: {root}"
    if not isinstance(entries, list) or not entries:
        return None, "entries must be a non-empty list"
    if len(entries) > MAX_ENTRIES:
        return None, f"too many entries (max {MAX_ENTRIES})"

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    file_paths: set[str] = set()
    total_bytes = 0

    for entry in entries:
        if not isinstance(entry, dict):
            return None, "each entry must be an object"
        rel, resolved, error = _resolve_workspace_path(entry.get("path"))
        if error:
            return None, error
        assert rel is not None and resolved is not None
        rel_posix = rel.as_posix()
        if rel_posix in seen:
            return None, f"duplicate entry path: {rel_posix}"
        seen.add(rel_posix)

        is_dir = bool(entry.get("is_dir", False))
        if is_dir:
            if entry.get("content") not in (None, ""):
                return None, f"directory entries cannot carry content: {rel_posix}"
            if resolved.exists() and not resolved.is_dir():
                return None, f"directory path already exists as a file: {rel_posix}"
            normalized.append({"path": rel_posix, "is_dir": True})
            continue

        if resolved.suffix.lower() != ".md":
            return None, f"only Markdown (.md) files may be written: {rel_posix}"
        content = entry.get("content", "")
        if not isinstance(content, str):
            return None, f"content must be a string: {rel_posix}"
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            return None, f"file exceeds {MAX_FILE_BYTES} bytes: {rel_posix}"
        total_bytes += len(encoded)
        if total_bytes > MAX_TOTAL_BYTES:
            return None, f"manifest exceeds {MAX_TOTAL_BYTES} bytes"
        if resolved.exists():
            return None, f"file already exists and overwrite is disabled: {rel_posix}"
        file_paths.add(rel_posix)
        normalized.append(
            {
                "path": rel_posix,
                "is_dir": False,
                "content": content,
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )

    # A manifest cannot declare a file where another entry needs a directory.
    for item in normalized:
        rel = Path(item["path"])
        if item["is_dir"]:
            continue
        for parent in rel.parents:
            if str(parent) in (".", ""):
                continue
            if parent.as_posix() in file_paths:
                return None, f"file path conflicts with a file entry: {item['path']}"

    return normalized, ""


def propose_workspace_write(description: str, entries: list[dict[str, Any]]) -> dict:
    """Validate a workspace manifest and return a dry-run plus token.

    No filesystem writes occur here. `confirm_workspace_write` consumes the
    token after the user has reviewed the returned manifest.
    """
    if not isinstance(description, str) or not description.strip():
        return _reject("description must be a non-empty string")

    normalized, error = _validate_entries(entries)
    if error:
        return _reject(error)
    assert normalized is not None

    token = _generate_token()
    now = time.time()
    pending = _load_json(PENDING_FILE)
    pending = {
        k: v
        for k, v in pending.items()
        if now - v.get("created_at", 0) < PENDING_TTL
    }
    pending[token] = {
        "token": token,
        "description": description.strip(),
        "entries": normalized,
        "workspace_root": str(_workspace_root()),
        "created_at": now,
    }
    _save_json(PENDING_FILE, pending)

    return {
        "token": token,
        "description": description.strip(),
        "workspace_root": str(_workspace_root()),
        "manifest": [
            {k: v for k, v in item.items() if k != "content"} for item in normalized
        ],
        "dry_run": True,
        "requires_confirmation": True,
    }


def _ensure_dir(path: Path, created_dirs: list[str]) -> None:
    root = _workspace_root()
    rel = path.relative_to(root)
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise OSError(f"symlink component is not allowed: {part}")
        if not current.exists():
            current.mkdir()
            created_dirs.append(str(current))
        elif not current.is_dir():
            raise OSError(f"path component is not a directory: {current}")


def _cleanup_created(created_files: list[str], created_dirs: list[str]) -> None:
    for raw in reversed(created_files):
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            pass
    for raw in sorted(created_dirs, key=lambda p: len(Path(p).parts), reverse=True):
        try:
            Path(raw).rmdir()
        except OSError:
            pass


def confirm_workspace_write(token: str, confirmed: bool = False) -> dict:
    """Write a previously proposed manifest after explicit confirmation."""
    pending = _load_json(PENDING_FILE)
    data = pending.get(token)
    if not data:
        return _reject("No pending workspace write found for that token.")

    now = time.time()
    if now - data.get("created_at", 0) > PENDING_TTL:
        return _reject("Workspace write proposal has expired. Please propose again.")

    normalized, error = _validate_entries(data.get("entries"))
    if error:
        return _reject(f"workspace changed since proposal: {error}")
    assert normalized is not None

    if not confirmed:
        return {
            "token": token,
            "description": data["description"],
            "workspace_root": str(_workspace_root()),
            "manifest": [
                {k: v for k, v in item.items() if k != "content"} for item in normalized
            ],
            "dry_run": True,
            "requires_confirmation": True,
        }

    root = _workspace_root()
    created_dirs: list[str] = []
    created_files: list[str] = []
    try:
        for item in sorted(
            normalized,
            key=lambda e: (not e["is_dir"], len(Path(e["path"]).parts)),
        ):
            target = root / item["path"]
            if item["is_dir"]:
                _ensure_dir(target, created_dirs)
            else:
                _ensure_dir(target.parent, created_dirs)
                with open(target, "x", encoding="utf-8") as f:
                    f.write(item["content"])
                created_files.append(str(target))
    except OSError as e:
        _cleanup_created(created_files, created_dirs)
        return _reject(f"Failed to write workspace manifest: {e}")

    del pending[token]
    _save_json(PENDING_FILE, pending)
    _append_audit(
        {
            "action": "write",
            "token": token,
            "description": data["description"],
            "workspace_root": str(root),
            "created_at": now,
            "files": created_files,
            "directories": created_dirs,
            "entries": [
                {k: v for k, v in item.items() if k != "content"} for item in normalized
            ],
        }
    )
    return {
        "token": token,
        "description": data["description"],
        "workspace_root": str(root),
        "created_files": created_files,
        "created_directories": created_dirs,
        "created": True,
    }


def _recorded_workspace_path(raw: Any, root: Path) -> tuple[Path | None, str]:
    """Validate an audited absolute path without following it.

    Rollback records contain paths the service created, but the filesystem can
    change between write and rollback. Resolve nothing here: a swapped symlink
    must fail closed instead of unlinking its target.
    """
    if not isinstance(raw, str) or not raw:
        return None, "Rollback record contains an invalid path."
    p = Path(raw)
    if not p.is_absolute():
        return None, "Rollback record contains a non-absolute path."
    try:
        rel = p.relative_to(root)
    except ValueError:
        return None, "Rollback record contains a path outside the workspace."
    if not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        return None, "Rollback record contains an invalid workspace path."

    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return None, "Rollback path contains a symlink component."
    return p, ""


def rollback_workspace_write(token: str) -> dict:
    """Remove only paths recorded as created by a confirmed workspace write."""
    if not AUDIT_FILE.exists():
        return _reject("No workspace write audit log found.")

    matching = None
    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("token") == token and entry.get("action") == "write":
                matching = entry
                break
    if not matching:
        return _reject("No workspace write record found for that token.")
    if time.time() - matching.get("created_at", 0) > ROLLBACK_WINDOW_SECONDS:
        return _reject("Workspace write rollback window has expired.")

    root = _workspace_root()
    removed: list[str] = []
    for raw in matching.get("files", []):
        p, error = _recorded_workspace_path(raw, root)
        if error:
            return _reject(error)
        assert p is not None
        try:
            p.unlink()
            removed.append(str(p))
        except FileNotFoundError:
            pass
        except OSError as e:
            return _reject(f"Failed to remove created file: {e}")

    for raw in sorted(
        matching.get("directories", []),
        key=lambda p: len(Path(p).parts),
        reverse=True,
    ):
        p, error = _recorded_workspace_path(raw, root)
        if error:
            return _reject(error)
        assert p is not None
        try:
            p.rmdir()
            removed.append(str(p))
        except FileNotFoundError:
            pass
        except OSError:
            # A recorded directory may now contain user files; never force-remove it.
            pass

    _append_audit(
        {
            "action": "rollback",
            "token": token,
            "created_at": matching.get("created_at"),
            "rolled_back_at": time.time(),
            "removed": removed,
        }
    )
    return {"rolled_back": True, "removed": removed}
