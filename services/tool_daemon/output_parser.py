"""
PoC 15.3: Structured Output Enforcement

Parses LLM responses to extract structured tool calls. The LLM must output
{"tool_call": {"name": "...", "args": {...}}} — not free text like "I found
1,247 MIDI files." If the LLM produces a tool call, it's extracted and sent
to the validator (PoC 15.4). If not, the response is treated as normal text.

Traces to: docs/roadmap.md Tier 2B, PoC 15.3.
Traces to: docs/tool_safety.md "Layer 1: Structured Output".

Design:
  - parse_tool_call() scans an LLM response for a JSON tool_call object.
    It handles common LLM formatting issues: markdown code fences, extra
    text before/after the JSON, and nested JSON.
  - If a tool call is found, it's returned as a ParsedToolCall. The caller
    validates it (PoC 15.4) and, if valid, executes the tool (PoC 15.6).
  - If no tool call is found, the response is normal text — no tool is
    called. This is the key defense: the LLM can never fabricate results
    because it can only get data by emitting a valid tool call that gets
    executed and returns real data.

Why this exists (what it defends against):
  This is the direct defense against the "I already did it" hallucination
  attack. Before this layer, the LLM could say "I found 847 files" and the
  user had no way to know if it actually looked. With structured output,
  the LLM must either call count_files (which returns the real number) or
  admit it can't. It can never produce a fabricated count.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ParsedToolCall:
    """A tool call extracted from an LLM response.

    If found=True, name and args contain the parsed tool call. The caller
    should validate via validation.validate_tool_call() before executing.
    If found=False, the LLM response is normal text (no tool call).
    """

    found: bool
    name: str | None = None
    args: dict[str, Any] | None = None
    raw_json: str | None = None  # The exact JSON string that was parsed


# Regex to find a potential JSON object start near "tool_call".
# We use a brace-matching parser (below) to extract the full object,
# because regex can't handle arbitrarily nested JSON.
_TOOL_CALL_HINT = re.compile(r'"tool_call"\s*:', re.DOTALL)


def _extract_balanced_json(text: str, start: int) -> str | None:
    """Extract a balanced JSON object starting at position `start` (a `{`).

    Walks the string tracking brace depth. Returns the substring from the
    opening `{` to its matching `}`, or None if unbalanced.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None  # Unbalanced


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ``` or ``` ... ```)."""
    # Match ```json\n...\n``` or ```\n...\n```
    fence_pattern = re.compile(r'```(?:json)?\s*\n?(.*?)\n?```', re.DOTALL)
    match = fence_pattern.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _try_parse_json(text: str) -> dict | None:
    """Attempt to parse a string as JSON. Returns the dict or None."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _extract_tool_call_obj(parsed: dict) -> dict | None:
    """Extract the tool_call object from a parsed JSON dict.

    Handles both {"tool_call": {...}} and nested structures where the
    tool_call is one key among others.
    """
    if "tool_call" in parsed:
        tc = parsed["tool_call"]
        if isinstance(tc, dict):
            return tc
    return None


def parse_tool_call(llm_response: str) -> ParsedToolCall:
    """Parse an LLM response for a structured tool call.

    This is the main entry point for PoC 15.3. The chat pipeline calls
    this on every LLM response. If a tool call is found, it's validated
    and executed. If not, the response is normal text.

    The parser is deliberately permissive about formatting (code fences,
    extra text) but strict about structure: the tool_call must be a JSON
    object with "name" and "args" keys.

    IMPORTANT: A tool call is only recognized if the JSON is the primary
    content of the response. If the LLM writes prose with an example
    tool call embedded in it (e.g., "Here's how I'd call a tool:
    {"tool_call": ...}"), that is NOT a tool call — it's an example.
    The parser checks that the JSON object occupies most of the response
    (after stripping markdown code fences) to avoid false positives.

    Recognized formats (LLMs are inconsistent):
      1. {"tool_call": {"name": "...", "args": {...}}}  — canonical
      2. [tool_call {"name": "...", "path": "..."}]      — markdown wrapper
      3. {"name": "...", "args": {...}}                   — bare, no wrapper
      4. {"name": "...", "path": "..."}                   — bare, flat args
    """
    if not llm_response or not isinstance(llm_response, str):
        return ParsedToolCall(found=False)

    text = llm_response.strip()

    # Fast path: no "tool_call" string and no "name" key — definitely not
    if "tool_call" not in text and '"name"' not in text:
        return ParsedToolCall(found=False)

    # Try 1: Strip code fences and parse the whole thing as JSON.
    # BUT: if the original text had substantial content outside the code
    # fence, this is prose with an embedded example, not a real tool call.
    stripped = _strip_code_fences(text)
    parsed = _try_parse_json(stripped)
    if parsed:
        tc = _extract_tool_call_obj(parsed)
        if tc:
            # Check if the code fence was embedded in prose.
            fence_pattern = re.compile(r'```(?:json)?\s*\n?.*?\n?```', re.DOTALL)
            fence_match = fence_pattern.search(text)
            if fence_match:
                text_outside = (text[:fence_match.start()] + text[fence_match.end():]).strip()
                text_outside = text_outside.replace("```json", "").replace("```", "").strip()
                if len(text_outside) > 100:
                    return ParsedToolCall(found=False)
            return _build_result(tc, stripped)

    # Try 2: Find "tool_call" hint, then walk backwards to the enclosing
    # JSON object's opening brace and extract the balanced object.
    hint_match = _TOOL_CALL_HINT.search(text)
    if hint_match:
        hint_start = hint_match.start()
        brace_start = text.rfind("{", 0, hint_start + 1)
        if brace_start >= 0:
            json_str = _extract_balanced_json(text, brace_start)
            if json_str:
                parsed = _try_parse_json(json_str)
                if parsed:
                    tc = _extract_tool_call_obj(parsed)
                    if tc:
                        if _is_prose_with_example(text, brace_start, json_str):
                            return ParsedToolCall(found=False)
                        return _build_result(tc, json_str)

    # Try 3: [tool_call {"name": "...", ...}] — markdown wrapper format.
    # The LLM sometimes wraps the JSON in [tool_call ...] instead of using
    # the canonical {"tool_call": {...}} format. Extract the JSON inside.
    bracket_match = re.search(r'\[tool_call\s*\n?\s*(\{.*?\})\s*\n?\s*\]', text, re.DOTALL)
    if bracket_match:
        json_str = bracket_match.group(1)
        # Try to extract balanced JSON (the regex might not handle nesting)
        brace_start = json_str.find("{")
        if brace_start >= 0:
            balanced = _extract_balanced_json(json_str, brace_start)
            if balanced:
                json_str = balanced
        parsed = _try_parse_json(json_str)
        if parsed:
            tc = _extract_bare_tool_call(parsed)
            if tc:
                if _is_prose_with_example(text, bracket_match.start(), bracket_match.group(0)):
                    return ParsedToolCall(found=False)
                return _build_result(tc, json_str)

    # Try 4: Bare {"name": "...", "args": {...}} or {"name": "...", "path": ...}
    # The LLM emits the tool call directly without a "tool_call" wrapper.
    # Only match if "name" appears near the start of a JSON object and the
    # value looks like a tool name (lowercase, underscores).
    name_hint = re.compile(r'"name"\s*:\s*"([a-z_]+)"')
    for m in name_hint.finditer(text):
        # Walk backwards to find the opening brace
        brace_start = text.rfind("{", 0, m.start() + 1)
        if brace_start >= 0:
            json_str = _extract_balanced_json(text, brace_start)
            if json_str:
                parsed = _try_parse_json(json_str)
                if parsed:
                    tc = _extract_bare_tool_call(parsed)
                    if tc:
                        if _is_prose_with_example(text, brace_start, json_str):
                            return ParsedToolCall(found=False)
                        return _build_result(tc, json_str)

    return ParsedToolCall(found=False)


def _extract_bare_tool_call(parsed: dict) -> dict | None:
    """Extract a tool call from a bare JSON object without a 'tool_call' wrapper.

    Handles:
      - {"name": "list_dir", "args": {"path": "..."}}  — proper args nesting
      - {"name": "list_dir", "path": "..."}             — flat args (LLM shortcut)
    """
    if "name" not in parsed or not isinstance(parsed["name"], str):
        return None
    # Already has args as a dict
    if "args" in parsed and isinstance(parsed["args"], dict):
        return {"name": parsed["name"], "args": parsed["args"]}
    # Flat args: everything except "name" becomes args
    args = {k: v for k, v in parsed.items() if k != "name"}
    return {"name": parsed["name"], "args": args if args else {}}


def _is_prose_with_example(text: str, start: int, matched: str) -> bool:
    """Check if a matched JSON/tool-call is embedded in prose (an example)
    rather than being the primary content of the response.

    If there's >100 chars of text outside the match, treat it as prose.
    """
    text_before = text[:start].strip()
    text_after = text[start + len(matched):].strip()
    for fence in ("```json", "```", "[tool_call]", "[tool_call"):
        text_before = text_before.replace(fence, "").strip()
        text_after = text_after.replace(fence, "").strip()
    outside_len = len(text_before) + len(text_after)
    return outside_len > 100


def strip_tool_call_artifacts(text: str) -> str:
    """Remove any tool-call-like syntax that leaked into a response.

    This is a safety net for when the parser doesn't recognize a tool call
    format (the LLM is creative). It strips:
      - [tool_call ...] blocks
      - {"tool_call": {...}} JSON objects
      - Bare {"name": "...", "args": {...}} JSON objects
      - Leftover markdown code fences containing tool calls

    This does NOT execute the tool — it just cleans the text for display.
    The parser should catch real tool calls before this is called.
    """
    if not text:
        return text

    # Strip [tool_call ...] blocks (with or without JSON inside)
    text = re.sub(r'\[tool_call[^\]]*\]', '', text, flags=re.DOTALL)

    # Strip JSON objects that look like tool calls. We can't use a simple
    # regex because JSON objects can be nested (e.g. {"tool_call": {"args": {...}}}).
    # Instead, walk through the text finding balanced { ... } blocks and
    # check if each is a tool-call-like JSON object.
    result = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            # Try to extract a balanced JSON object starting here
            end = _find_balanced_brace(text, i)
            if end is not None:
                candidate = text[i:end + 1]
                if _is_tool_call_json(candidate):
                    # Skip the tool-call JSON
                    i = end + 1
                    continue
            # Not a tool call (or unbalanced) — keep the character
            result.append(text[i])
            i += 1
        else:
            result.append(text[i])
            i += 1
    text = ''.join(result)

    # Clean up extra whitespace left behind
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _find_balanced_brace(text: str, start: int) -> int | None:
    """Find the index of the closing brace that balances the opening brace
    at `start`. Returns None if no balanced match is found.

    Handles nested braces and string literals (ignores braces inside strings).
    """
    if start >= len(text) or text[start] != '{':
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i
    return None


def _is_tool_call_json(json_str: str) -> bool:
    """Check if a JSON string looks like a tool call.

    Returns True for:
      - {"tool_call": {...}}
      - {"name": "...", "args": {...}}
      - {"name": "...", "path": "..."}
      - {"name": "...", "query": "..."}
      - {"name": "...", "pattern": "..."}
    """
    try:
        obj = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(obj, dict):
        return False
    if "tool_call" in obj:
        return True
    if "name" in obj and isinstance(obj["name"], str):
        if any(k in obj for k in ("args", "path", "query", "pattern")):
            return True
    return False


def _build_result(tool_call_obj: dict, raw_json: str) -> ParsedToolCall:
    """Build a ParsedToolCall from the extracted tool_call dict.

    Validates that the tool_call has the required "name" and "args" keys.
    """
    name = tool_call_obj.get("name")
    args = tool_call_obj.get("args")

    if not name or not isinstance(name, str):
        return ParsedToolCall(found=False)

    if args is None:
        args = {}
    if not isinstance(args, dict):
        return ParsedToolCall(found=False)

    return ParsedToolCall(
        found=True,
        name=name,
        args=args,
        raw_json=raw_json,
    )
