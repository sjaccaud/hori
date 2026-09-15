"""File-backed workstream thread/episode store.

HORI's persistent workstream layer: each thread is a directory under the
threads root containing a curated `state.md` plus an append-only
`episodes/` history of structured Markdown records. Files are the
authoritative store — human-readable, grep-able, backup-friendly.

This module is the write path for the remote MCP surface, so all input is
treated as untrusted: thread names are validated slugs, episodes are
append-only (no overwrite/delete), sizes are capped, and every resolved
path is verified inside the threads root before a write occurs.

Defends against: path traversal and symlink escapes from model-supplied
names, oversized writes, and frontmatter corruption (values are emitted as
JSON-quoted scalars, which are valid YAML flow scalars).

Traces to: .devin/prd/episode-threads-mcp.md, docs/manifesto.md Pillars
II/IV/VI, docs/safety.md (untrusted-input boundary).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

THREADS_ROOT = Path(
    os.environ.get("HORI_THREADS_ROOT", "~/.local/share/hori/threads")
).expanduser()
REVIEW_ROOT = Path(
    os.environ.get("HORI_REVIEW_ROOT", "~/.local/share/hori/review")
).expanduser()

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_EPISODE_BYTES = 64 * 1024
MAX_EPISODES_PER_THREAD = 500
MAX_TITLE_CHARS = 200
MAX_LIST_ITEMS = 50
MAX_ITEM_CHARS = 500


def _resolve_root(root: Optional[Path]) -> Path:
    return Path(root) if root is not None else THREADS_ROOT


def _thread_dir(name: str, root: Path) -> Path:
    """Validate a thread name and return its directory, confined to root."""
    if not _SLUG.match(name or ""):
        raise ValueError(f"Invalid thread name: {name!r}")
    resolved_root = root.resolve()
    tdir = (resolved_root / name).resolve()
    if not tdir.is_relative_to(resolved_root):
        raise ValueError(f"Thread path escapes root: {name!r}")
    if not tdir.is_dir():
        raise FileNotFoundError(f"Unknown thread: {name!r}")
    if tdir.is_symlink():
        raise ValueError(f"Thread dir is a symlink: {name!r}")
    return tdir


def _check_scalar(value: str, field: str, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > max_chars:
        raise ValueError(f"{field} exceeds {max_chars} chars")
    if "\n" in value or "\r" in value or "---" in value:
        raise ValueError(f"{field} contains disallowed characters")
    return value


def _check_list(values: Optional[list], field: str) -> list:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_LIST_ITEMS:
        raise ValueError(f"{field} must be a list of at most {MAX_LIST_ITEMS}")
    return [_check_scalar(v, field, MAX_ITEM_CHARS) for v in values]


def _title_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:40] or "episode"


def _fm_scalar(key: str, value: str) -> str:
    # json.dumps produces a double-quoted string that is a valid YAML
    # flow scalar, so embedded quotes/unicode cannot break the frontmatter.
    return f"{key}: {json.dumps(value)}"


def _fm_list(key: str, values: list) -> str:
    return f"{key}: {json.dumps(values)}"


def append_episode(
    thread: str,
    title: str,
    summary: str,
    author: str,
    verified_by: Optional[str] = None,
    open_issues: Optional[list] = None,
    files_touched: Optional[list] = None,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """Append a structured episode to a thread. Create-only; no overwrite."""
    root = _resolve_root(root)
    tdir = _thread_dir(thread, root)

    title = _check_scalar(title, "title", MAX_TITLE_CHARS)
    author = _check_scalar(author, "author", MAX_TITLE_CHARS)
    if verified_by is not None:
        verified_by = _check_scalar(verified_by, "verified_by", MAX_TITLE_CHARS)
    open_issues = _check_list(open_issues, "open_issues")
    files_touched = _check_list(files_touched, "files_touched")
    if not isinstance(summary, str):
        raise ValueError("summary must be a string")
    if len(summary.encode("utf-8")) > MAX_EPISODE_BYTES:
        raise ValueError(f"summary exceeds {MAX_EPISODE_BYTES} bytes")

    ep_dir = tdir / "episodes"
    ep_dir.mkdir(exist_ok=True)
    existing = sorted(ep_dir.glob("*.md"))
    if len(existing) >= MAX_EPISODES_PER_THREAD:
        raise ValueError(f"thread has {MAX_EPISODES_PER_THREAD} episodes")

    next_n = max(
        (int(p.name[:3]) for p in existing if p.name[:3].isdigit()),
        default=0,
    ) + 1
    fname = f"{next_n:03d}-{_title_slug(title)}.md"

    fm = [
        "---",
        _fm_scalar("title", title),
        _fm_scalar("date", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        _fm_scalar("author", author),
        _fm_scalar("verified_by", verified_by or "unverified"),
        _fm_list("open_issues", open_issues),
        _fm_list("files_touched", files_touched),
        "---",
    ]
    parts = ["\n".join(fm), "", "## Summary", "", summary.rstrip(), ""]
    if open_issues:
        parts += ["## Open issues", ""] + [f"- {i}" for i in open_issues] + [""]
    if files_touched:
        parts += ["## Files touched", ""] + [f"- `{f}`" for f in files_touched] + [""]

    path = ep_dir / fname
    with open(path, "x", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return {"ok": True, "file": fname}


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Best-effort split of '---' frontmatter; scalars are JSON-quoted."""
    if not text.startswith("---"):
        return {}, text
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return {}, text
    fm: dict[str, Any] = {}
    for line in text[3:end].strip().splitlines():
        if ": " not in line:
            continue
        key, _, raw = line.partition(": ")
        try:
            fm[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            fm[key.strip()] = raw
    return fm, text[end + 4:].lstrip("\n")


def _file_verified(path: Path) -> bool:
    """True if the file's frontmatter has a real verified_by value."""
    fm, _ = _split_frontmatter(
        path.read_text(encoding="utf-8", errors="replace"))
    v = str(fm.get("verified_by", "")).strip('"').strip()
    return bool(v) and v != "unverified"


def read_thread(
    name: str, episodes: int = 1, root: Optional[Path] = None
) -> dict[str, Any]:
    """Return a thread's state.md plus its latest `episodes` records."""
    root = _resolve_root(root)
    tdir = _thread_dir(name, root)
    episodes = max(0, min(int(episodes), 10))

    state_file = tdir / "state.md"
    state = state_file.read_text(encoding="utf-8") if state_file.exists() else ""

    ep_dir = tdir / "episodes"
    files = sorted(ep_dir.glob("*.md")) if ep_dir.is_dir() else []
    out_eps = []
    for f in files[-episodes:] if episodes else []:
        fm, body = _split_frontmatter(f.read_text(encoding="utf-8"))
        out_eps.append({"file": f.name, "frontmatter": fm, "body": body})
    return {"name": name, "state": state, "episodes": out_eps}


def _pending_proposals(review_root: Path, thread: str) -> int:
    """Count pending review-queue items for a thread."""
    if not review_root.is_dir():
        return 0
    n = 0
    for f in review_root.glob(f"*-{thread}.md"):
        fm, _ = _split_frontmatter(
            f.read_text(encoding="utf-8", errors="replace"))
        if str(fm.get("status", "")).strip('"') == "pending":
            n += 1
    return n


def list_threads(
    root: Optional[Path] = None, review_root: Optional[Path] = None
) -> list[dict[str, Any]]:
    """List all threads with status/updated from state.md frontmatter."""
    root = _resolve_root(root)
    review_root = review_root if review_root is not None else REVIEW_ROOT
    if not root.is_dir():
        return []
    out = []
    for tdir in sorted(root.iterdir()):
        if not tdir.is_dir() or tdir.is_symlink() or not _SLUG.match(tdir.name):
            continue
        fm = {}
        state_file = tdir / "state.md"
        if state_file.exists():
            fm, _ = _split_frontmatter(state_file.read_text(encoding="utf-8"))
        ep_dir = tdir / "episodes"
        count = len(list(ep_dir.glob("*.md"))) if ep_dir.is_dir() else 0
        out.append({
            "name": tdir.name,
            "status": fm.get("status", "unknown"),
            "updated": fm.get("updated", ""),
            "episode_count": count,
            "pending_proposals": _pending_proposals(review_root, tdir.name),
        })
    return out


def search_episodes(
    query: str,
    thread: Optional[str] = None,
    limit: int = 10,
    root: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Case-insensitive substring search over state.md + episode files."""
    root = _resolve_root(root)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    limit = max(1, min(int(limit), 100))
    needle = query.lower()

    if thread is not None:
        dirs = [_thread_dir(thread, root)]
    else:
        dirs = [
            d for d in sorted(root.iterdir())
            if d.is_dir() and not d.is_symlink() and _SLUG.match(d.name)
        ] if root.is_dir() else []

    hits: list[dict[str, Any]] = []
    for tdir in dirs:
        candidates = [tdir / "state.md"]
        ep_dir = tdir / "episodes"
        if ep_dir.is_dir():
            candidates += sorted(ep_dir.glob("*.md"))
        for f in candidates:
            if not f.exists() or f.is_symlink():
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if needle in line.lower():
                        hits.append({
                            "file": str(f.relative_to(root)),
                            "line": i,
                            "context": line.strip()[:300],
                        })
                        if len(hits) >= limit * 4:
                            break
            except UnicodeDecodeError:
                continue

    verified_cache: dict[str, bool] = {}

    def _hit_verified(hit: dict) -> bool:
        rel = hit["file"]
        if rel not in verified_cache:
            f = root / rel
            verified_cache[rel] = (
                f.name != "state.md" and f.exists() and _file_verified(f)
            )
        return verified_cache[rel]

    # Stable sort: verified sources first (Pillar VII trust ordering).
    return sorted(hits, key=lambda h: not _hit_verified(h))[:limit]
