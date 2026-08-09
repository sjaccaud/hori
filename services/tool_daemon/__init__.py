"""
AIOS Tool Daemon — Safety Spine (Tier 2)

This package implements the tool safety architecture for AIOS. It is the
"spine" — the irreducible minimum safety that makes read-only tools safe.

Traces to:
  - docs/roadmap.md Tier 2 (AIOS 1.5 — The Safety Spine)
  - docs/safety.md (full architecture)
  - docs/safety.md (adversarial analysis)

Design principle (from docs/manifesto.md Pillar VII):
  The LLM is untrusted. The architecture is trusted.
  The LLM proposes; the architecture executes; the user confirms.
  The Sherpa watches the pattern.

Sub-modules:
  - schema.py:    Tool schema definitions (PoC 15.1)
  - registry.py:  Tool allowlist registry (PoC 15.2)
  - validation.py: Tool call validation against schema (PoC 15.4)
  - tools.py:     Read-only filesystem tool implementations (PoC 15.6)
  - audit.py:     Append-only audit log with permission separation (PoC 15.9)
  - server.py:    Unix domain socket server (PoC 15.5)
"""
