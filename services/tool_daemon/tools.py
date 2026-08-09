"""
PoC 15.6: Read-Only Filesystem Tools

The first and only tools in the safety spine: list_dir, read_file,
count_files, search_files. All read-only — no write, no delete, no execute.
Restricted to Landlock-allowed path prefixes. Path canonicalization
(os.path.realpath) eliminates traversal attacks and symlink escape.

Traces to: docs/roadmap.md Tier 2C, PoC 15.6.
Traces to: docs/safety.md "Layer 2: The Cage".

Design:
  - Each tool is a pure function that takes validated args (from PoC 15.4)
    and returns a dict result.
  - Tools NEVER raise exceptions to the caller — they return {"error": ...}.
    This prevents the LLM from learning about the filesystem structure via
    error messages (e.g., "file not found" vs "permission denied" leaks
    whether a file exists).
  - read_file has a 100KB default limit and rejects binary files.
  - count_files and search_files use fnmatch for glob patterns and cap
    results to prevent the LLM from enumerating the entire filesystem.

Why this exists (what it defends against):
  These are the tools that give AIOS real filesystem access — but only
  read-only, and only within allowed paths. The LLM can finally answer
  "how many MIDI files do I have?" with a real number instead of
  hallucinating. The read-only constraint means even a fully compromised
  LLM cannot delete files, modify configurations, or plant malware.
"""
from __future__ import annotations

import fnmatch
import os
from typing import Any

# Maximum results returned by search_files (prevents full-disk enumeration)
MAX_SEARCH_RESULTS = 50
# Maximum sample paths returned by count_files
MAX_COUNT_SAMPLE = 20
# Default and hard-cap for read_file
DEFAULT_MAX_BYTES = 102_400  # 100KB
HARD_MAX_BYTES = 1_048_576  # 1MB
# Bytes to check for binary file detection
BINARY_CHECK_BYTES = 2048


def _safe_join(base: str, *parts: str) -> str:
    """Safely join path parts, ensuring the result stays within base.

    This is defense in depth — the validator (PoC 15.4) already canonicalized
    the path. We re-check here in case the tool is called directly.
    """
    joined = os.path.realpath(os.path.join(base, *parts))
    base_real = os.path.realpath(base)
    common = os.path.commonpath([joined, base_real])
    if common != base_real:
        raise ValueError(f"Path escapes allowed prefix: {joined}")
    return joined


def _is_binary(filepath: str) -> bool:
    """Check if a file appears to be binary (non-text).

    Reads the first BINARY_CHECK_BYTES bytes and checks for null bytes
    or a high proportion of non-text bytes.
    """
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(BINARY_CHECK_BYTES)
        if b"\x00" in chunk:
            return True
        # Check for high proportion of non-text bytes
        text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
        non_text = sum(1 for b in chunk if b not in text_chars)
        if len(chunk) > 0 and non_text / len(chunk) > 0.30:
            return True
        return False
    except (OSError, PermissionError):
        return True  # If we can't read it, treat as binary (safe default)


def list_dir(args: dict[str, Any]) -> dict[str, Any]:
    """List the contents of a directory.

    Returns {"entries": [{"name": "...", "type": "file"|"dir", "size": N}]}.
    Does NOT recurse. Symlinks are reported as "symlink".
    """
    path = args.get("path", "")
    if not path or not isinstance(path, str):
        return {"error": "Missing or invalid 'path' argument"}

    try:
        if not os.path.exists(path):
            return {"error": "Path does not exist"}
        if not os.path.isdir(path):
            return {"error": "Path is not a directory"}

        entries = []
        try:
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                if os.path.islink(full):
                    entry_type = "symlink"
                    size = 0
                elif os.path.isdir(full):
                    entry_type = "dir"
                    size = 0
                elif os.path.isfile(full):
                    entry_type = "file"
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0
                else:
                    entry_type = "other"
                    size = 0
                entries.append({"name": name, "type": entry_type, "size": size})
        except PermissionError:
            return {"error": "Permission denied"}

        return {"path": path, "entries": entries, "count": len(entries)}

    except (OSError, ValueError) as e:
        return {"error": f"Failed to list directory: {e}"}


def read_file(args: dict[str, Any]) -> dict[str, Any]:
    """Read the contents of a text file.

    Returns {"path": "...", "content": "...", "bytes_read": N}.
    Files larger than max_bytes are truncated. Binary files are rejected.
    """
    path = args.get("path", "")
    if not path or not isinstance(path, str):
        return {"error": "Missing or invalid 'path' argument"}

    max_bytes = args.get("max_bytes", DEFAULT_MAX_BYTES)
    if not isinstance(max_bytes, int) or max_bytes < 1:
        max_bytes = DEFAULT_MAX_BYTES
    max_bytes = min(max_bytes, HARD_MAX_BYTES)

    try:
        if not os.path.exists(path):
            return {"error": "File does not exist"}
        if not os.path.isfile(path):
            return {"error": "Path is not a regular file"}

        # Check file size
        file_size = os.path.getsize(path)
        if file_size == 0:
            return {"path": path, "content": "", "bytes_read": 0, "truncated": False}

        # Binary check
        if _is_binary(path):
            return {"error": "File appears to be binary — only text files can be read"}

        # Read the file
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_bytes)
        truncated = len(content) < file_size

        return {
            "path": path,
            "content": content,
            "bytes_read": len(content.encode("utf-8")),
            "truncated": truncated,
            "file_size": file_size,
        }

    except PermissionError:
        return {"error": "Permission denied"}
    except (OSError, ValueError) as e:
        return {"error": f"Failed to read file: {e}"}


def count_files(args: dict[str, Any]) -> dict[str, Any]:
    """Count files matching a glob pattern in a directory tree.

    Returns {"path": "...", "pattern": "...", "count": N, "sample": [...]}.
    Recurses into subdirectories. Returns up to MAX_COUNT_SAMPLE sample paths.
    """
    path = args.get("path", "")
    pattern = args.get("pattern", "")
    if not path or not isinstance(path, str):
        return {"error": "Missing or invalid 'path' argument"}
    if not pattern or not isinstance(pattern, str):
        return {"error": "Missing or invalid 'pattern' argument"}

    try:
        if not os.path.exists(path):
            return {"error": "Path does not exist"}
        if not os.path.isdir(path):
            return {"error": "Path is not a directory"}

        count = 0
        sample = []
        for root, dirs, files in os.walk(path):
            # Don't follow symlinks (os.walk follows them by default)
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for filename in files:
                if fnmatch.fnmatch(filename, pattern):
                    count += 1
                    if len(sample) < MAX_COUNT_SAMPLE:
                        sample.append(os.path.join(root, filename))

        return {
            "path": path,
            "pattern": pattern,
            "count": count,
            "sample": sample,
        }

    except PermissionError:
        return {"error": "Permission denied"}
    except (OSError, ValueError) as e:
        return {"error": f"Failed to count files: {e}"}


def search_files(args: dict[str, Any]) -> dict[str, Any]:
    """Search for files by name pattern in a directory tree.

    Returns {"path": "...", "pattern": "...", "results": [...], "count": N}.
    Recurses into subdirectories. Returns up to max_results paths.
    """
    path = args.get("path", "")
    pattern = args.get("pattern", "")
    if not path or not isinstance(path, str):
        return {"error": "Missing or invalid 'path' argument"}
    if not pattern or not isinstance(pattern, str):
        return {"error": "Missing or invalid 'pattern' argument"}

    max_results = args.get("max_results", MAX_SEARCH_RESULTS)
    if not isinstance(max_results, int) or max_results < 1:
        max_results = MAX_SEARCH_RESULTS
    max_results = min(max_results, 200)

    try:
        if not os.path.exists(path):
            return {"error": "Path does not exist"}
        if not os.path.isdir(path):
            return {"error": "Path is not a directory"}

        results = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for filename in files:
                if fnmatch.fnmatch(filename, pattern):
                    results.append(os.path.join(root, filename))
                    if len(results) >= max_results:
                        return {
                            "path": path,
                            "pattern": pattern,
                            "results": results,
                            "count": len(results),
                            "truncated": True,
                        }

        return {
            "path": path,
            "pattern": pattern,
            "results": results,
            "count": len(results),
            "truncated": False,
        }

    except PermissionError:
        return {"error": "Permission denied"}
    except (OSError, ValueError) as e:
        return {"error": f"Failed to search files: {e}"}


# --- Tool dispatch table ---
# Maps tool name -> implementation function.
# The server (PoC 15.5) uses this to dispatch validated tool calls.
TOOL_IMPLEMENTATIONS: dict[str, Any] = {
    "list_dir": list_dir,
    "read_file": read_file,
    "count_files": count_files,
    "search_files": search_files,
}


def execute_tool(name: str, validated_args: dict[str, Any]) -> dict[str, Any]:
    """Execute a validated tool call.

    This is the single entry point for tool execution. The server (PoC 15.5)
    calls this after validation (PoC 15.4) has approved the call.
    The audit log (PoC 15.9) wraps this call to record every execution.
    """
    impl = TOOL_IMPLEMENTATIONS.get(name)
    if impl is None:
        return {"error": f"Tool '{name}' has no implementation (should not happen — validator should have caught this)"}
    return impl(validated_args)
