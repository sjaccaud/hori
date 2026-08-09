"""
PoC 15.4: Tool Call Validation

Validates every tool call against its schema before execution. Wrong types,
missing params, out-of-range values, and path traversal attempts are rejected
with an error message back to the LLM. The LLM can retry with a corrected
call (max 3 retries, enforced by the caller).

Traces to: docs/roadmap.md Tier 2B, PoC 15.4.
Traces to: docs/safety.md "Layer 1: Structured Output".

Design:
  - validate_tool_call() is the single entry point. It checks:
    1. Tool is in the registry (PoC 15.2)
    2. All required parameters are present
    3. Parameter types match the schema
    4. Values are within bounds (min/max, enum, pattern)
    5. Path parameters are within allowed prefixes (anti-traversal)
  - Path canonicalization (os.path.realpath) eliminates .., ., and symlinks.
    This is defense in depth on top of Landlock (PoC 15.0b).
  - Returns a ValidationResult with either valid args or an error message
    suitable for sending back to the LLM.

Why this exists (what it defends against):
  Without validation, the LLM could pass arbitrary arguments — a path like
  "../../etc/passwd", a negative number for max_bytes, or a missing required
  parameter. The validator catches all of these before the tool executes.
  Path traversal is the most critical: it prevents the LLM from escaping
  the allowed path prefixes via ../ sequences or symlinks.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .registry import get_tool
from .schema import ParameterSchema, ToolSchema


@dataclass
class ValidationResult:
    """Result of validating a tool call.

    If valid=True, validated_args contains the type-checked, canonicalized
    arguments ready for execution. If valid=False, error contains a
    human-readable error message suitable for sending back to the LLM.
    """

    valid: bool
    tool_name: str
    validated_args: dict[str, Any] | None = None
    error: str | None = None


def _check_type(value: Any, expected_type: str, param_name: str) -> str | None:
    """Check that a value matches the expected JSON Schema type. Returns error msg or None."""
    if expected_type == "string":
        if not isinstance(value, str):
            return f"Parameter '{param_name}' must be a string, got {type(value).__name__}"
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return f"Parameter '{param_name}' must be an integer, got {type(value).__name__}"
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            return f"Parameter '{param_name}' must be a boolean, got {type(value).__name__}"
    elif expected_type == "array":
        if not isinstance(value, list):
            return f"Parameter '{param_name}' must be an array, got {type(value).__name__}"
    else:
        return f"Unknown type '{expected_type}' for parameter '{param_name}'"
    return None


def _check_bounds(
    value: Any, param: ParameterSchema, param_name: str
) -> str | None:
    """Check min/max constraints. Returns error msg or None."""
    if param.min_value is not None:
        if isinstance(value, (int, float)) and value < param.min_value:
            return f"Parameter '{param_name}' must be >= {param.min_value}, got {value}"
        if isinstance(value, list) and len(value) < param.min_value:
            return f"Parameter '{param_name}' must have >= {param.min_value} items, got {len(value)}"
    if param.max_value is not None:
        if isinstance(value, (int, float)) and value > param.max_value:
            return f"Parameter '{param_name}' must be <= {param.max_value}, got {value}"
        if isinstance(value, list) and len(value) > param.max_value:
            return f"Parameter '{param_name}' must have <= {param.max_value} items, got {len(value)}"
    return None


def _check_enum(value: Any, param: ParameterSchema, param_name: str) -> str | None:
    """Check enum constraint. Returns error msg or None."""
    if param.enum is not None and value not in param.enum:
        allowed = ", ".join(repr(v) for v in param.enum)
        return f"Parameter '{param_name}' must be one of [{allowed}], got {value!r}"
    return None


def _check_pattern(value: Any, param: ParameterSchema, param_name: str) -> str | None:
    """Check regex pattern constraint. Returns error msg or None."""
    if param.pattern is not None and isinstance(value, str):
        if not re.match(param.pattern, value):
            return f"Parameter '{param_name}' does not match required pattern"
    return None


def _validate_path(path: str, allowed_prefixes: list[str]) -> str | None:
    """Validate that a path is within an allowed prefix.

    Uses os.path.realpath() to canonicalize the path, eliminating .., .,
    and symlinks. This is defense in depth on top of Landlock (PoC 15.0b)
    — even if Landlock is misconfigured, the validator blocks traversal.

    Returns error message if the path is not allowed, None if it's safe.
    """
    if not isinstance(path, str) or not path:
        return "Path must be a non-empty string"

    # Reject paths with embedded null bytes — os.path.realpath raises
    # ValueError on them, and they're a known injection vector (C APIs
    # treat null as string terminator, potentially truncating the path).
    if "\x00" in path:
        return "Path contains null byte — rejected for safety"

    # Canonicalize: resolve .., ., and symlinks.
    # We check the realpath against allowed prefixes, not the raw input.
    # This prevents: the user home directory/../../etc/passwd
    try:
        canonical = os.path.realpath(path)
    except (ValueError, OSError) as e:
        return f"Path canonicalization failed: {e}"

    # The canonical path must start with one of the allowed prefixes.
    # We use os.path.commonpath to avoid prefix-matching bugs (e.g.,
    # the user home directory-evil should NOT match the user home directory).
    for prefix in allowed_prefixes:
        canonical_prefix = os.path.realpath(prefix)
        try:
            common = os.path.commonpath([canonical, canonical_prefix])
            if common == canonical_prefix:
                return None  # Path is within this allowed prefix
        except ValueError:
            # commonpath raises on different drives (Windows) or mixed
            # absolute/relative paths. On Linux this shouldn't happen.
            continue

    return (
        f"Path '{path}' is outside allowed prefixes. "
        f"Allowed: {', '.join(allowed_prefixes)}"
    )


def validate_tool_call(
    tool_name: str, args: dict[str, Any] | None
) -> ValidationResult:
    """Validate a tool call against the registry and schema.

    This is the single entry point for validation. The tool daemon calls
    this before executing any tool. If validation fails, the error message
    is sent back to the LLM, which can retry (max 3 retries).

    Returns a ValidationResult. If valid, validated_args is ready for
    execution (paths are canonicalized, defaults are filled in).
    """
    # 1. Check the tool is in the registry
    tool = get_tool(tool_name)
    if tool is None:
        return ValidationResult(
            valid=False,
            tool_name=tool_name,
            error=f"Tool '{tool_name}' is not registered. Available tools: "
            + ", ".join(sorted(t.name for t in __import__(
                "services.tool_daemon.registry", fromlist=["list_tools"
            ]).list_tools())),
        )

    if args is None:
        args = {}
    if not isinstance(args, dict):
        return ValidationResult(
            valid=False,
            tool_name=tool_name,
            error=f"Tool arguments must be a JSON object, got {type(args).__name__}",
        )

    validated: dict[str, Any] = {}

    # 2. Check each parameter
    for param in tool.parameters:
        name = param.name
        value = args.get(name, param.default)

        # Check required params are present
        if param.required and name not in args:
            return ValidationResult(
                valid=False,
                tool_name=tool_name,
                error=f"Missing required parameter '{name}'",
            )

        # Skip optional params that aren't provided
        if name not in args and param.default is None:
            continue

        # Type check
        type_err = _check_type(value, param.type, name)
        if type_err:
            return ValidationResult(valid=False, tool_name=tool_name, error=type_err)

        # Bounds check
        bounds_err = _check_bounds(value, param, name)
        if bounds_err:
            return ValidationResult(valid=False, tool_name=tool_name, error=bounds_err)

        # Enum check
        enum_err = _check_enum(value, param, name)
        if enum_err:
            return ValidationResult(valid=False, tool_name=tool_name, error=enum_err)

        # Pattern check
        pattern_err = _check_pattern(value, param, name)
        if pattern_err:
            return ValidationResult(valid=False, tool_name=tool_name, error=pattern_err)

        # Path validation (for path-type parameters — checked by name convention)
        if name == "path" and tool.allowed_paths:
            path_err = _validate_path(value, tool.allowed_paths)
            if path_err:
                return ValidationResult(valid=False, tool_name=tool_name, error=path_err)
            # Store the canonical path, not the raw input
            validated[name] = os.path.realpath(value)
        else:
            validated[name] = value

    # 3. Check for unexpected parameters (not in schema)
    valid_param_names = {p.name for p in tool.parameters}
    unexpected = set(args.keys()) - valid_param_names
    if unexpected:
        return ValidationResult(
            valid=False,
            tool_name=tool_name,
            error=f"Unexpected parameters: {', '.join(sorted(unexpected))}",
        )

    return ValidationResult(valid=True, tool_name=tool_name, validated_args=validated)
