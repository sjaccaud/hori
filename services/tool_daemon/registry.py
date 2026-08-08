"""
PoC 15.2: Tool Allowlist Registry

Central registry of approved tools. Each tool declares its schema, safety
level, allowed paths, and rate limits. Tools not in the registry cannot be
called — no exceptions.

Traces to: docs/roadmap.md Tier 2B, PoC 15.2.
Traces to: docs/tool_safety.md "Layer 1: Structured Output".

Design:
  - The registry is a singleton populated at import time with the approved
    tools. There is no dynamic registration — adding a tool requires a code
    change and a commit. This is intentional: the allowlist is static and
    auditable.
  - The spine only permits READ_ONLY tools (list_dir, read_file, count_files,
    search_files). Side-effects and destructive tools are AIOS 2.0.
  - Allowed paths are the same prefixes that Landlock (PoC 15.0b) enforces
    at the kernel level. The registry enforces them at the application level
    as defense in depth — if Landlock is misconfigured, the registry still
    blocks path traversal.

Why this exists (what it defends against):
  Without an allowlist, any tool name the LLM invents could potentially be
  executed. The registry ensures only explicitly approved tools can run.
  A prompt injection that says "call the delete_file tool" fails because
  delete_file is not in the registry — it doesn't exist.
"""
from __future__ import annotations

from pathlib import Path

from .schema import ParameterSchema, SafetyLevel, ToolSchema

# --- Path prefixes allowed for read-only filesystem tools ---
# These come from config (hori.yaml). The tool daemon self-restricts via
# Landlock to these paths; the registry enforces the same at the
# application level as defense in depth.
# Default: ~/Projects (read-only) + the workspace (read-write).
# Do NOT include ~ (home) — that would expose ~/.ssh, ~/.gnupg, etc.
from hori.config import WORKSPACE_PATH, ALLOWED_READ_PATHS

ALLOWED_PATH_PREFIXES = list(ALLOWED_READ_PATHS) + [WORKSPACE_PATH]


# --- Read-only filesystem tools (the only tools in the spine) ---

LIST_DIR = ToolSchema(
    name="list_dir",
    description=(
        "List the contents of a directory. Returns a list of file and "
        "subdirectory names. Does NOT recurse into subdirectories. "
        "The path must be within an allowed prefix."
    ),
    parameters=[
        ParameterSchema(
            name="path",
            type="string",
            description="Absolute path to the directory to list.",
            required=True,
        ),
    ],
    safety_level=SafetyLevel.READ_ONLY,
    allowed_paths=ALLOWED_PATH_PREFIXES,
)

READ_FILE = ToolSchema(
    name="read_file",
    description=(
        "Read the contents of a text file. Returns the file content as a "
        "string. Files larger than 100KB are truncated. Binary files are "
        "rejected. The path must be within an allowed prefix."
    ),
    parameters=[
        ParameterSchema(
            name="path",
            type="string",
            description="Absolute path to the file to read.",
            required=True,
        ),
        ParameterSchema(
            name="max_bytes",
            type="integer",
            description="Maximum bytes to read (default 102400 = 100KB).",
            required=False,
            default=102400,
            min_value=1,
            max_value=1048576,  # 1MB hard cap
        ),
    ],
    safety_level=SafetyLevel.READ_ONLY,
    allowed_paths=ALLOWED_PATH_PREFIXES,
)

COUNT_FILES = ToolSchema(
    name="count_files",
    description=(
        "Count files matching a pattern in a directory tree. Returns the "
        "count and a sample of matching paths (up to 20). Recurses into "
        "subdirectories. The path must be within an allowed prefix."
    ),
    parameters=[
        ParameterSchema(
            name="path",
            type="string",
            description="Absolute path to the directory to search in.",
            required=True,
        ),
        ParameterSchema(
            name="pattern",
            type="string",
            description=(
                "Glob pattern to match (e.g. '*.py', '*.midi'). "
                "Only the filename is matched, not the full path."
            ),
            required=True,
        ),
    ],
    safety_level=SafetyLevel.READ_ONLY,
    allowed_paths=ALLOWED_PATH_PREFIXES,
)

SEARCH_FILES = ToolSchema(
    name="search_files",
    description=(
        "Search for files by name pattern in a directory tree. Returns a "
        "list of matching paths (up to 50). Recurses into subdirectories. "
        "The path must be within an allowed prefix."
    ),
    parameters=[
        ParameterSchema(
            name="path",
            type="string",
            description="Absolute path to the directory to search in.",
            required=True,
        ),
        ParameterSchema(
            name="pattern",
            type="string",
            description=(
                "Glob pattern to match against filenames (e.g. '*.py', "
                "'config.*'). Only the filename is matched."
            ),
            required=True,
        ),
        ParameterSchema(
            name="max_results",
            type="integer",
            description="Maximum number of results to return (default 50).",
            required=False,
            default=50,
            min_value=1,
            max_value=200,
        ),
    ],
    safety_level=SafetyLevel.READ_ONLY,
    allowed_paths=ALLOWED_PATH_PREFIXES,
)


# --- The registry itself ---

_REGISTRY: dict[str, ToolSchema] = {}


def _register(tool: ToolSchema) -> None:
    """Register a tool. Called at import time to populate the registry."""
    if tool.name in _REGISTRY:
        raise ValueError(f"Tool '{tool.name}' is already registered")
    _REGISTRY[tool.name] = tool


# Populate the registry with the spine's read-only tools.
for _tool in (LIST_DIR, READ_FILE, COUNT_FILES, SEARCH_FILES):
    _register(_tool)


def get_tool(name: str) -> ToolSchema | None:
    """Look up a tool by name. Returns None if not in the registry.

    This is the single point of truth for whether a tool exists. If it's
    not in the registry, it cannot be called — no exceptions.
    """
    return _REGISTRY.get(name)


def list_tools() -> list[ToolSchema]:
    """Return all registered tools."""
    return list(_REGISTRY.values())


def list_tool_names() -> list[str]:
    """Return the names of all registered tools."""
    return list(_REGISTRY.keys())


def is_registered(name: str) -> bool:
    """Check if a tool name is in the registry."""
    return name in _REGISTRY


def get_tool_schemas_for_prompt() -> list[dict]:
    """Return all tool schemas as dicts, for injection into the LLM prompt.

    This is what PoC 15.3 (Structured Output Enforcement) uses to tell the
    LLM what tools are available and how to call them.
    """
    return [t.to_dict() for t in _REGISTRY.values()]


def get_tool_prompt_block() -> str:
    """Render all tools as a prompt block for the LLM system prompt.

    This replaces the 'WHAT YOU CANNOT DO' section of the system prompt
    when tools are available. The LLM is told: 'You can call these tools.
    Respond with a JSON tool call, not free text.'
    """
    tools_str = "\n".join(t.to_llm_prompt() for t in _REGISTRY.values())
    # Build the allowed paths list dynamically from config
    paths_list = "\n".join(f"  - {p}" for p in ALLOWED_PATH_PREFIXES)
    # The primary read path is the first allowed_read_path (default: ~/Projects)
    primary_path = ALLOWED_READ_PATHS[0] if ALLOWED_READ_PATHS else WORKSPACE_PATH
    return (
        "You have access to the following tools. To use a tool, respond "
        "with a JSON object in this exact format:\n"
        '  {"tool_call": {"name": "<tool_name>", "args": {<parameters>}}}\n\n'
        "Available tools:\n"
        f"{tools_str}\n\n"
        "ALLOWED PATHS: You can only access these directories:\n"
        f"{paths_list}\n"
        f"When the user asks about 'my files', 'my projects', 'how many X do I "
        f"have', use {primary_path} as the path. NEVER use /home/user, "
        "~/Projects, or any other path — only the ones listed above.\n\n"
        "If no tool is needed, respond normally with text. "
        "Never fabricate tool results — if you want to know something "
        "about the filesystem, you MUST call the appropriate tool."
    )
