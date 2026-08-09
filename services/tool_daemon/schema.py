"""
PoC 15.1: Tool Schema Framework

Defines tools as structured JSON schemas. The LLM outputs structured tool
calls (not free text), and every call is validated against its schema before
execution. This is the skeleton that prevents the LLM from fabricating results.

Traces to: docs/roadmap.md Tier 2B, PoC 15.1.
Traces to: docs/safety.md "Layer 1: Structured Output".

Design:
  - Each tool is defined as a ToolSchema with name, description, parameters,
    required fields, safety level, and allowed path prefixes.
  - Parameters use JSON Schema types (string, integer, boolean, array).
  - Safety levels: read_only (spine), side_effects (AIOS 2.0), destructive
    (AIOS 2.0). The spine only permits read_only tools.
  - Allowed paths restrict where filesystem tools can operate. The Landlock
    configuration (PoC 15.0b) enforces this at the kernel level; the schema
    enforces it at the application level as defense in depth.

Why this exists (what it defends against):
  Without structured schemas, the LLM can output free-form text that looks
  like a tool result ("I found 1,247 MIDI files") without ever calling a
  tool. This is the exact hallucination attack that motivated AIOS. By
  requiring structured output, the LLM must either emit a valid tool call
  (which gets executed and returns real data) or produce a normal text
  response. It can never fabricate tool results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SafetyLevel(str, Enum):
    """Safety classification for tools.

    The safety spine (Tier 2) only permits READ_ONLY tools. Side-effects
    and destructive tools are AIOS 2.0 and require additional safety layers
    (human-in-the-loop confirmation, taint tracking, etc.).
    """

    READ_ONLY = "read_only"
    SIDE_EFFECTS = "side_effects"
    DESTRUCTIVE = "destructive"


@dataclass
class ParameterSchema:
    """Schema for a single tool parameter.

    Uses a subset of JSON Schema types. The validator (PoC 15.4) checks
    that LLM-provided arguments match this schema before the tool executes.
    """

    name: str
    type: str  # "string", "integer", "boolean", "array"
    description: str
    required: bool = True
    default: Any = None
    enum: list[str] | None = None  # Allowed values (for string params)
    min_value: int | float | None = None  # Minimum for integer/array length
    max_value: int | float | None = None  # Maximum for integer/array length
    pattern: str | None = None  # Regex pattern for string params

    def to_dict(self) -> dict:
        """Serialize to a dict for the LLM's tool schema prompt."""
        d: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
        }
        if self.default is not None:
            d["default"] = self.default
        if self.enum is not None:
            d["enum"] = self.enum
        if self.min_value is not None:
            d["min"] = self.min_value
        if self.max_value is not None:
            d["max"] = self.max_value
        if self.pattern is not None:
            d["pattern"] = self.pattern
        return d


@dataclass
class ToolSchema:
    """Complete schema for a tool.

    Each tool in the registry (PoC 15.2) is defined by a ToolSchema. The
    schema is used to:
      1. Generate the tool description prompt for the LLM
      2. Validate LLM-provided arguments (PoC 15.4)
      3. Enforce safety level and path restrictions
    """

    name: str
    description: str
    parameters: list[ParameterSchema]
    safety_level: SafetyLevel = SafetyLevel.READ_ONLY
    allowed_paths: list[str] = field(default_factory=list)
    # Maximum calls per conversation turn (PoC 15.12 rate limiting).
    # Default is generous; the global rate limiter enforces the aggregate.
    rate_limit_per_turn: int = 10

    @property
    def required_params(self) -> list[str]:
        """Names of parameters that are required."""
        return [p.name for p in self.parameters if p.required]

    def to_dict(self) -> dict:
        """Serialize to a dict for the LLM's tool schema prompt.

        This is the format injected into the system prompt so the LLM knows
        what tools are available and how to call them.
        """
        return {
            "name": self.name,
            "description": self.description,
            "safety_level": self.safety_level.value,
            "parameters": [p.to_dict() for p in self.parameters],
            "allowed_paths": self.allowed_paths,
        }

    def to_llm_prompt(self) -> str:
        """Render as a human-readable description for the LLM system prompt.

        The LLM sees this and knows: 'I can call this tool with these args.'
        The structured output enforcement (PoC 15.3) requires the LLM to
        respond with JSON matching this schema, not free text.
        """
        params_str = "\n".join(
            f"    - {p.name} ({p.type}): {p.description}"
            + (" [required]" if p.required else " [optional]")
            + (f" [one of: {', '.join(p.enum)}]" if p.enum else "")
            for p in self.parameters
        )
        return (
            f"  {self.name}: {self.description}\n"
            f"    Parameters:\n{params_str}\n"
            f"    Safety: {self.safety_level.value}"
        )
