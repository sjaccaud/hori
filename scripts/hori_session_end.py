#!/usr/bin/env python3
"""Devin SessionEnd hook: journal the session for observability.

Appends one JSON line to ~/.local/share/hori/sessions.jsonl recording the
session id, end reason, and cwd. This gives future consolidation jobs a
cheap signal for "sessions happened here without episodes" — staleness
detection without intercepting the agent. Never blocks, never fails.

Traces to: system-memory thread (Slice B — session lifecycle plumbing).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path.home() / ".local/share/hori/sessions.jsonl"


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": "devin-cli",
            "session_id": payload.get("session_id"),
            "reason": payload.get("reason"),
            "cwd": str(Path.cwd()),
        }) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
