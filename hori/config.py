"""HORI configuration loader.

Loads settings from ~/.config/hori/hori.yaml with env var overrides.
On first run, creates the config file with defaults from
hori/config.reference.yaml.

Env var naming: HORI_<SECTION>_<KEY>, uppercased with underscores.
For example, llm.api_url → HORI_LLM_API_URL.

This module is the single source of truth for all configurable settings.
Services import from here instead of defining their own config constants.
"""
import os
import copy
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# --- Config file location (XDG-compliant) ---
XDG_CONFIG_HOME = Path(os.getenv("XDG_CONFIG_HOME", "~/.config")).expanduser()
CONFIG_DIR = XDG_CONFIG_HOME / "hori"
CONFIG_FILE = CONFIG_DIR / "hori.yaml"

# Reference schema — the defaults
REFERENCE_PATH = Path(__file__).parent / "config.reference.yaml"


def _load_reference() -> dict[str, Any]:
    """Load the reference YAML (defaults)."""
    with open(REFERENCE_PATH) as f:
        return yaml.safe_load(f)


def _expand_paths(data: dict[str, Any]) -> dict[str, Any]:
    """Expand ~ in all string values (path expansion)."""
    result = copy.deepcopy(data)
    for key, val in result.items():
        if isinstance(val, str) and val.startswith("~"):
            result[key] = str(Path(val).expanduser())
        elif isinstance(val, dict):
            result[key] = _expand_paths(val)
        elif isinstance(val, list):
            result[key] = [
                str(Path(v).expanduser()) if isinstance(v, str) and v.startswith("~") else v
                for v in val
            ]
    return result


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply env var overrides on top of YAML values.

    Env var format: HORI_<SECTION>_<KEY>
    Example: HORI_LLM_API_URL overrides data["llm"]["api_url"]
    """
    result = copy.deepcopy(data)
    env_map = {
        "HORI_LLM_API_URL": ("llm", "api_url"),
        "HORI_LLM_MODEL": ("llm", "model"),
        "HORI_LLM_ENABLE_THINKING": ("llm", "enable_thinking"),
        "HORI_EMBED_URL": ("embedding", "api_url"),
        "HORI_EMBED_MODEL": ("embedding", "model"),
        "HORI_EMBED_DIM": ("embedding", "dim"),
        "HORI_QDRANT_URL": ("memory", "qdrant_url"),
        "HORI_MEMORY_BACKEND": ("memory", "backend"),
        "HORI_TTS_BACKEND": ("tts", "backend"),
        "HORI_KOKORO_MODEL_PATH": ("tts", "kokoro", "model_path"),
        "HORI_KOKORO_VOICES_PATH": ("tts", "kokoro", "voices_path"),
        "HORI_KOKORO_VOICE": ("tts", "kokoro", "default_voice"),
        "HORI_KOKORO_LANG": ("tts", "kokoro", "lang"),
        "HORI_PIPER_VOICE_DIR": ("tts", "piper", "voice_dir"),
        "HORI_PIPER_VOICE": ("tts", "piper", "default_voice"),
        "HORI_SERVICE_HOST": ("service", "host"),
        "HORI_SERVICE_PORT": ("service", "port"),
        "HORI_WORKSPACE": ("paths", "workspace"),
        "HORI_AUDIT_LOG": ("paths", "audit_log"),
        "HORI_SAFETY_EVENTS_LOG": ("paths", "safety_events_log"),
        "HORI_TOOL_SOCKET": ("paths", "tool_socket"),
        "HORI_ADMIN_TOKEN": ("admin", "token"),
        # Legacy env var compatibility (existing systemd services use these)
        "LLM_API_URL": ("llm", "api_url"),
        "LLM_MODEL": ("llm", "model"),
        "LLM_ENABLE_THINKING": ("llm", "enable_thinking"),
        "EMBED_URL": ("embedding", "api_url"),
        "EMBED_MODEL": ("embedding", "model"),
        "EMBED_DIM": ("embedding", "dim"),
        "QDRANT_URL": ("memory", "qdrant_url"),
        "AIOS_TTS_BACKEND": ("tts", "backend"),
        "KOKORO_MODEL_PATH": ("tts", "kokoro", "model_path"),
        "KOKORO_VOICES_PATH": ("tts", "kokoro", "voices_path"),
        "KOKORO_VOICE": ("tts", "kokoro", "default_voice"),
        "KOKORO_LANG": ("tts", "kokoro", "lang"),
        "PIPER_VOICE_DIR": ("tts", "piper", "voice_dir"),
        "PIPER_VOICE": ("tts", "piper", "default_voice"),
        "AIOS_CORE_HOST": ("service", "host"),
        "AIOS_CORE_PORT": ("service", "port"),
        "AIOS_AUDIT_LOG": ("paths", "audit_log"),
        "AIOS_SAFETY_EVENTS_LOG": ("paths", "safety_events_log"),
        "AIOS_ADMIN_TOKEN": ("admin", "token"),
    }

    for env_key, path in env_map.items():
        env_val = os.getenv(env_key)
        if env_val is None:
            continue
        # Navigate to the nested key
        d = result
        for key in path[:-1]:
            d = d[key]
        final_key = path[-1]
        # Type-convert: try to match the existing value's type
        current = d.get(final_key)
        if isinstance(current, bool):
            d[final_key] = env_val.lower() in ("true", "1", "yes")
        elif isinstance(current, int):
            d[final_key] = int(env_val)
        else:
            d[final_key] = env_val

    return result


def _ensure_config_exists() -> None:
    """Create the config file with defaults if it doesn't exist."""
    if CONFIG_FILE.exists():
        return
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        reference = _load_reference()
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(reference, f, default_flow_style=False, sort_keys=True)
        logger.info("Created default config at %s", CONFIG_FILE)
    except (OSError, PermissionError) as e:
        # Running as a restricted user (e.g. aios-worker with home=/nonexistent)
        # that can't create the config dir. Fall back to defaults — the caller
        # can override via env vars (XDG_CONFIG_HOME, AIOS_*).
        logger.warning("Cannot create config dir %s: %s — using defaults", CONFIG_DIR, e)


def load_config() -> dict[str, Any]:
    """Load config from YAML, apply env overrides, expand paths.

    Returns the full config dict. This is called once at import time
    and cached as the module-level `config` variable.
    """
    _ensure_config_exists()
    try:
        with open(CONFIG_FILE) as f:
            data = yaml.safe_load(f) or {}
    except (OSError, PermissionError, FileNotFoundError) as e:
        logger.warning("Cannot read config %s: %s — using defaults", CONFIG_FILE, e)
        data = _load_reference()
    data = _apply_env_overrides(data)
    data = _expand_paths(data)
    return data


# Load once at import time
config = load_config()


# --- Convenience accessors (what services import) ---
# LLM
LLM_API_URL = config["llm"]["api_url"]
LLM_MODEL = config["llm"]["model"]
LLM_ENABLE_THINKING = config["llm"]["enable_thinking"]

# Embedding
EMBED_URL = config["embedding"]["api_url"]
EMBED_MODEL = config["embedding"]["model"]
EMBED_DIM = config["embedding"]["dim"]

# Memory
MEMORY_BACKEND = config["memory"].get("backend", "qdrant")
QDRANT_URL = config["memory"]["qdrant_url"]
COLLECTION_WORKING = config["memory"]["collection_working"]
COLLECTION_PROJECT = config["memory"]["collection_project"]
COLLECTION_LONGTERM = config["memory"]["collection_longterm"]

# TTS
TTS_BACKEND = config["tts"]["backend"]
KOKORO_MODEL_PATH = config["tts"]["kokoro"]["model_path"]
KOKORO_VOICES_PATH = config["tts"]["kokoro"]["voices_path"]
KOKORO_DEFAULT_VOICE = config["tts"]["kokoro"]["default_voice"]
KOKORO_LANG = config["tts"]["kokoro"]["lang"]
PIPER_VOICE_DIR = Path(config["tts"]["piper"]["voice_dir"])
PIPER_DEFAULT_VOICE = config["tts"]["piper"]["default_voice"]

# Service
SERVICE_HOST = config["service"]["host"]
SERVICE_PORT = config["service"]["port"]

# Paths
WORKSPACE_PATH = config["paths"]["workspace"]
AUDIT_LOG_PATH = config["paths"]["audit_log"]
SAFETY_EVENTS_LOG = config["paths"]["safety_events_log"]
TOOL_SOCKET_PATH = config["paths"]["tool_socket"]
SOCKET_DIR = str(Path(TOOL_SOCKET_PATH).parent)
# Allowed read paths for filesystem tools. These are the directories the
# LLM can read via list_dir/read_file/count_files/search_files. Default:
# ~/Projects. Do NOT include ~ (home) — that would expose ~/.ssh etc.
ALLOWED_READ_PATHS = config["paths"].get("allowed_read_paths", [])

# Admin
ADMIN_TOKEN = config["admin"]["token"]

# Notifications
TELEGRAM_BOT_TOKEN = config["notifications"]["telegram_bot_token"]
TELEGRAM_CHAT_ID = config["notifications"]["telegram_chat_id"]
NTFY_TOPIC = config["notifications"]["ntfy_topic"]
NTFY_SERVER = config["notifications"]["ntfy_server"]
HASS_URL = config["notifications"]["hass_url"]
HASS_TOKEN = config["notifications"]["hass_token"]
HASS_NOTIFY_SERVICE = config["notifications"]["hass_notify_service"]

# --- Paths that are derived from the project root (not user-configurable) ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_INTENT_DIR = PROJECT_ROOT / "core" / "intent"
CORE_STATE_DIR = PROJECT_ROOT / "core" / "state"
DOCS_DIR = PROJECT_ROOT / "docs"
INTENT_SCHEMA_PATH = CORE_INTENT_DIR / "schema.json"
MANIFESTO_PATH = CORE_INTENT_DIR / "manifesto.json"
CHARTER_PATH = CORE_INTENT_DIR / "charter.json"
USER_MODEL_PATH = CORE_STATE_DIR / "user_model.json"
PROJECT_STATE_PATH = CORE_STATE_DIR / "project_state.json"
GOVERNANCE_PATH = DOCS_DIR / "governance_safety.md"

# --- Non-configurable constants ---
# The Sherpa capability file is a well-known IPC path between the Sherpa
# (Go binary) and the tool daemon. It is NOT user-configurable because both
# processes must agree on the location.
SHERPA_CAPABILITY_FILE = "/run/sherpa/capability_level"

# Destructive patterns that trigger the red-team gate.
# These are intentionally specific to avoid false positives on normal text.
DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-rf\b", r"\bmkfs\b", r"\bfdisk\b",
    r"\bsudo\s+rm\b", r"\bchmod\s+777\b",
    r"\bdelete\s+(all|everything|entire|the|old|log|files|database|records)\b",
    r"\bwipe\s+(the|all|disk|drive)\b",
    r"\btruncate\s+(table|database)\b",
    r"\bdrop\s+(table|schema|database)\b",
    r"\bformat\s+(c:|disk|drive|partition)\b",
    r"\bpayment\s+(to|send|process)\b", r"\bsend\s+email\s+to\b",
    r"\bpost\s+to\s+social\b",
    r"\bgit\s+push\s+--force\b", r"\bgit\s+reset\s+--hard\b",
]
