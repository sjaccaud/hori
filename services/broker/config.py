"""Configuration constants for the system root broker.

These are the default runtime paths. The install script may override
socket/allowlist paths via environment variables or config files.
"""

from __future__ import annotations

import os
from pathlib import Path

RUN_DIR = Path(os.environ.get("BROKER_RUN_DIR", "/run/system-root-broker"))
ETC_DIR = Path(os.environ.get("BROKER_ETC_DIR", "/etc/system-root-broker"))
VAR_LOG_DIR = Path(os.environ.get("BROKER_LOG_DIR", "/var/log/system-root-broker"))

BROKER_SOCKET_PATH = RUN_DIR / "broker.sock"
BROKER_ALLOWLIST_PATH = ETC_DIR / "broker-allowlist.json"
BROKER_SECRETS_PATH = ETC_DIR / "broker-secrets.env"
BROKER_AUDIT_LOG_PATH = VAR_LOG_DIR / "audit.jsonl"

DEFAULT_TIMEOUT = float(os.environ.get("BROKER_TIMEOUT", "60.0"))
MAX_REQUEST_SIZE = int(os.environ.get("BROKER_MAX_REQUEST_SIZE", "65536"))

ALLOWED_SERVICES = frozenset(
    {
        "aios_core",
        "aios-sherpa",
        "aios-tool-daemon",
        "llamacpp",
    }
)
