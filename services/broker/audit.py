"""Append-only audit log for the system root broker.

Mirrors the permission-separation pattern in services/tool_daemon/audit.py:
the log is owned root:root-broker with mode 0640 so the broker can append
but cannot read or truncate it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BrokerAuditEntry:
    """A single privileged-operation audit entry."""

    timestamp: float
    request_id: str
    caller_uid: int
    caller_exe: str | None
    operation: str
    params: dict[str, Any]
    allowed: bool
    confirmed_by: str | None
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None
    sandbox_profile: str
    revert_id: str | None
    # Internal sequence number for monotonic ordering / hash chaining
    sequence: int = field(default_factory=lambda: int(time.time() * 1_000_000))

    def to_jsonl(self) -> str:
        """Serialize to a single JSON line for the append-only log."""
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "request_id": self.request_id,
                "caller_uid": self.caller_uid,
                "caller_exe": self.caller_exe,
                "operation": self.operation,
                "params": self._sanitize(self.params),
                "allowed": self.allowed,
                "confirmed_by": self._redact_token(self.confirmed_by),
                "exit_code": self.exit_code,
                "stdout_hash": self._hash(self.stdout),
                "stderr_hash": self._hash(self.stderr),
                "error": self.error,
                "sandbox_profile": self.sandbox_profile,
                "revert_id": self.revert_id,
                "sequence": self.sequence,
            },
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _hash(text: str) -> str:
        """Return a short SHA-256 hash of the output for integrity."""
        import hashlib

        return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _redact_token(token: str | None) -> str | None:
        """Never log raw tokens."""
        if token is None:
            return None
        if len(token) <= 8:
            return "***"
        return f"{token[:4]}...{token[-4:]}"

    @staticmethod
    def _sanitize(value: Any) -> Any:
        """Truncate long values in the audit log."""
        if isinstance(value, str) and len(value) > 2000:
            return value[:2000] + "...[truncated]"
        if isinstance(value, dict):
            return {k: BrokerAuditEntry._sanitize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [BrokerAuditEntry._sanitize(v) for v in value]
        return value


class BrokerAuditLogger:
    """Append-only broker audit logger."""

    def __init__(self, log_path: str | Path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.touch()

    def log(self, entry: BrokerAuditEntry) -> None:
        """Append an entry to the audit log."""
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry.to_jsonl() + "\n")

    def read(self) -> list[dict[str, Any]]:
        """Read the entire audit log.

        This exists only for testing and root forensics. The broker service
        itself never reads the log.
        """
        if not self.log_path.exists():
            return []
        entries = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
