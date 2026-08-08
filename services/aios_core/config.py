"""aios-core configuration.

All configurable settings now live in hori.config (the top-level config
module that loads from ~/.config/hori/hori.yaml). This module re-exports
them for backward compatibility with existing imports.

Services should migrate to importing from hori.config directly. This
shim exists so we don't have to update every import in one slice.
"""
# Re-export everything from the new config module
from hori.config import (  # noqa: F401
    # LLM
    LLM_API_URL,
    LLM_MODEL,
    LLM_ENABLE_THINKING,
    # Embedding
    EMBED_URL,
    EMBED_MODEL,
    EMBED_DIM,
    # Memory
    QDRANT_URL,
    COLLECTION_WORKING,
    COLLECTION_PROJECT,
    COLLECTION_LONGTERM,
    # Service
    SERVICE_HOST,
    SERVICE_PORT,
    # Paths (project-derived, not user-configurable)
    PROJECT_ROOT,
    CORE_INTENT_DIR,
    CORE_STATE_DIR,
    DOCS_DIR,
    INTENT_SCHEMA_PATH,
    MANIFESTO_PATH,
    CHARTER_PATH,
    USER_MODEL_PATH,
    PROJECT_STATE_PATH,
    GOVERNANCE_PATH,
    # Non-configurable constants
    SHERPA_CAPABILITY_FILE,
    DESTRUCTIVE_PATTERNS,
)
