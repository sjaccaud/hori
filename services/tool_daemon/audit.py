"""
PoC 15.9: Audit Log (with Permission Separation)

Every tool call is logged: timestamp, tool name, args, result, LLM reasoning,
and data taint status. The log is append-only JSONL at logs/tool_audit.jsonl.

Traces to: docs/roadmap.md Tier 2C, PoC 15.9.
Traces to: docs/tool_safety.md "Layer 3: The Audit Trail".

Permission separation (red-team fix #2):
  The log file is owned by root:aios-worker, mode 0620. aios-worker can
  append but cannot read or truncate. The Sherpa (Tier 2E) reads the log
  as root. This prevents a compromised tool daemon from crafting misleading
  audit entries for the Sherpa.

  In production, the file permissions are set by the install script. In
  development/tests, we use a regular file with normal permissions. The
  AuditLogger class works regardless of the underlying permissions.

Discrepancy detection:
  The audit log records what the LLM claimed vs what the tool returned.
  If the LLM claims "I found 847 files" but count_files returned 23, the
  Sherpa can detect this mismatch from the log. Full discrepancy detection
  is implemented in the response verification layer (PoC 15.14, Tier 2D).

Why this exists (what it defends against):
  Without an audit log, there's no way to know what the LLM actually did
  with its tools. A compromised LLM could read sensitive files and the
  user would never know. The audit log is the forensic record — every
  file read, every directory listed, every search performed is recorded
  for review by the user and the Sherpa.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AuditEntry:
    """A single audit log entry.

    Every field is recorded for forensic review. The Sherpa (PoC 15.50)
    reads these entries to detect behavioral anomalies.
    """

    timestamp: float  # Unix epoch (time.time())
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any]
    # Whether the tool call succeeded (no "error" key in result)
    success: bool
    # The LLM's stated reasoning for the call (if available from the chat pipeline)
    llm_reasoning: str | None = None
    # Data taint: whether the result contains data that could influence
    # the LLM's behavior (e.g., file content that might contain prompt
    # injection). Full taint tracking is AIOS 2.0; the spine just records
    # the flag for the Sherpa to use.
    data_tainted: bool = False
    # Conversation and turn ID for correlating with the chat pipeline
    conversation_id: str | None = None
    turn_id: str | None = None

    def to_jsonl(self) -> str:
        """Serialize to a single JSON line for the append-only log."""
        return json.dumps({
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "args": self.args,
            "result": self.result,
            "success": self.success,
            "llm_reasoning": self.llm_reasoning,
            "data_tainted": self.data_tainted,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
        }, ensure_ascii=False)


class AuditLogger:
    """Append-only audit logger for tool calls.

    The log file is opened in append mode only. In production, the file is
    owned by root:aios-worker with mode 0620 (append-only for aios-worker,
    read for root). The class itself doesn't enforce permissions — that's
    the install script's job. The class just appends entries.
    """

    def __init__(self, log_path: str | Path):
        self.log_path = Path(log_path)
        # Ensure the directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Create the file if it doesn't exist
        if not self.log_path.exists():
            self.log_path.touch()

    def log(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        *,
        llm_reasoning: str | None = None,
        data_tainted: bool = False,
        conversation_id: str | None = None,
        turn_id: str | None = None,
    ) -> AuditEntry:
        """Log a tool call. Returns the AuditEntry that was written.

        This is the only method that writes to the log. It's called by the
        tool server (PoC 15.5) after every tool execution, whether the tool
        succeeded or failed.
        """
        entry = AuditEntry(
            timestamp=time.time(),
            tool_name=tool_name,
            args=self._sanitize_args(args),
            result=self._sanitize_result(result),
            success="error" not in result,
            llm_reasoning=llm_reasoning,
            data_tainted=data_tainted,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry.to_jsonl() + "\n")

        return entry

    def _sanitize_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Sanitize args for logging.

        Truncates long file content in args to prevent the audit log from
        growing unboundedly. Paths are kept in full (they're short and
        forensically important).
        """
        sanitized = {}
        for key, value in args.items():
            if isinstance(value, str) and len(value) > 1000:
                sanitized[key] = value[:1000] + "...[truncated]"
            else:
                sanitized[key] = value
        return sanitized

    def _sanitize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Sanitize result for logging.

        Truncates file content in read_file results to prevent the audit
        log from containing full file contents (which could be large and
        sensitive). The first 500 chars are kept for forensic review.
        """
        sanitized = {}
        for key, value in result.items():
            if key == "content" and isinstance(value, str) and len(value) > 500:
                sanitized[key] = value[:500] + "...[truncated for audit log]"
            elif isinstance(value, list) and len(value) > 50:
                sanitized[key] = value[:50] + [f"...[{len(value) - 50} more entries truncated]"]
            else:
                sanitized[key] = value
        return sanitized

    def read_entries(self) -> list[dict[str, Any]]:
        """Read all entries from the audit log.

        In production, this is called by the Sherpa (which runs as root and
        can read the file). aios-worker cannot read the file (mode 0620 =
        write-only for group). In development, the file has normal
        permissions so this works.
        """
        entries = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue  # Skip malformed lines
        except (OSError, PermissionError):
            pass  # Can't read — return empty list
        return entries

    def get_entry_count(self) -> int:
        """Count the number of entries in the audit log."""
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except (OSError, PermissionError):
            return 0
