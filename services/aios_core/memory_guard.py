"""
Red-team fix #5: Memory Poisoning Guard

Tool result data cannot flow into store_memory without explicit
user-originated confirmation. This prevents a compromised LLM from
injecting false memories via tool results.

Traces to: docs/tool_safety_redteam.md Red-team fix #5.
Traces to: tests/adversarial/test_memory_poisoning.py

Design:
  - is_tool_originated_data() checks if a data dict came from a tool
    result (has "source": "tool_result" or a "tool" key).
  - can_store_memory() returns False if the data is tool-originated AND
    the user hasn't explicitly confirmed it. Returns True otherwise.
  - This is a single check at the memory write boundary, not full taint
    tracking (which is AIOS 2.0). It's the spine-level defense: even if
    the LLM tries to write "I found 847 files" as a memory, the guard
    blocks it unless the user said "remember this."

Why this exists (what it defends against):
  A compromised LLM could try to persist hallucinated or exfiltrated data
  into long-term memory. For example, after reading /etc/passwd via
  read_file, it could try to store "The user's system has 12 accounts"
  as a memory. On the next session, that "memory" would be retrieved and
  treated as truth — a persistent poisoning attack. The guard ensures
  tool results can only become memories if the user explicitly confirms.
"""
from __future__ import annotations

from typing import Any, Dict


def is_tool_originated_data(data: Dict[str, Any]) -> bool:
    """Check if a data dict originated from a tool result.

    Returns True if the data has a "source" key set to "tool_result" or
    has a "tool" key indicating which tool produced it. Returns False for
    normal user messages or assistant responses.

    This is a heuristic — the full taint tracking system (AIOS 2.0) will
    track data provenance through the pipeline. This guard is the simple
    boundary check: does this data LOOK like it came from a tool?
    """
    if not isinstance(data, dict):
        return False

    # Explicit source tag
    if data.get("source") == "tool_result":
        return True

    # Has a tool key (tool results include which tool produced them)
    if "tool" in data and data.get("source") != "user":
        return True

    return False


def can_store_memory(data: Dict[str, Any], user_confirmed: bool = False) -> bool:
    """Check if data can be stored to long-term memory.

    Returns False if the data is tool-originated AND the user hasn't
    explicitly confirmed it. Returns True otherwise.

    The memory write path in aios-core calls this before every
    store_memory() call. If it returns False, the write is rejected
    and logged.
    """
    if is_tool_originated_data(data) and not user_confirmed:
        return False
    return True
