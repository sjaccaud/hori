#!/usr/bin/env python3
"""Devin SessionStart hook: inject HORI thread context.

Prints a hookSpecificOutput JSON object on stdout whose additionalContext
contains (a) a compact index of all workstream threads and (b) the full
state.md + latest episode of the thread matching the current directory,
if one exists. Reads the files directly — no network, no auth, must never
hang or crash the session. All failures degrade to empty output.

Mapping: ~/Projects/<dir> -> thread slug (basename, lowercase, _ -> -),
with an explicit table for names that don't normalize cleanly.

Traces to: .devin/prd/episode-threads-mcp.md, system-memory thread.
"""

import json
import os
import sys
from pathlib import Path

THREADS_ROOT = Path.home() / ".local/share/hori/threads"
MAX_CONTEXT_BYTES = 4096

CWD_TO_THREAD = {
    "aios": "hori",
    "content_factory": "content-factory",
}


def _index() -> str:
    lines = []
    for tdir in sorted(THREADS_ROOT.iterdir()):
        if not tdir.is_dir() or tdir.is_symlink():
            continue
        state = tdir / "state.md"
        status = updated = ""
        if state.exists():
            head = state.read_text(encoding="utf-8", errors="replace")[:2000]
            for line in head.splitlines():
                if line.startswith("status:"):
                    status = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("updated:"):
                    updated = line.split(":", 1)[1].strip().strip('"')
        ep_dir = tdir / "episodes"
        n_eps = len(list(ep_dir.glob("*.md"))) if ep_dir.is_dir() else 0
        lines.append(
            f"- {tdir.name} [{status or '?'}] "
            f"updated={updated or '?'} episodes={n_eps}"
        )
    return "\n".join(lines)


def _thread_context(slug: str) -> str:
    tdir = THREADS_ROOT / slug
    state = tdir / "state.md"
    out = []
    if state.exists():
        out.append(state.read_text(encoding="utf-8", errors="replace").strip())
    ep_dir = tdir / "episodes"
    if ep_dir.is_dir():
        eps = sorted(ep_dir.glob("*.md"))
        if eps:
            out.append("LATEST EPISODE "
                       + eps[-1].name + ":\n"
                       + eps[-1].read_text(encoding="utf-8", errors="replace").strip())
    return "\n\n".join(out)


def main() -> None:
    cwd = Path(os.getcwd())
    slug = CWD_TO_THREAD.get(cwd.name) or cwd.name.lower().replace("_", "-")

    parts = [
        "HORI workstream threads (read via thread_read/episode_search "
        "MCP tools, or files under ~/.local/share/hori/threads/):",
        _index(),
    ]
    if (THREADS_ROOT / slug).is_dir():
        parts.append(
            f"\nCURRENT THREAD '{slug}' (cwd match):\n" + _thread_context(slug)
        )

    ctx = "\n".join(parts)[:MAX_CONTEXT_BYTES]
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # A hook must never break session start.
        sys.exit(0)
