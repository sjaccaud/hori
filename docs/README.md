# HORI Documentation Index

> **New here?** Start with the [root README](../README.md) for a project
> overview, quickstart, and architecture diagram. This index is the
> detailed reference for all documentation.

This is the single source of truth for HORI documentation. All docs are
listed here with their purpose and canonical status. If a doc is marked
SUPERSEDED, do not follow its guidance - use the replacement.

## Canonical Documents (follow these)

### Mission & Architecture
- **docs/manifesto.md** - The foundational mission, seven pillars (incl.
  Engineering Discipline: TDD + documentation sync), core values, and the
  experience vision (north star). The "constitution" of HORI. Read this
  first for philosophy.
- **docs/stack.md** - Current hardware, networking, software, and model
  configuration. THE source of truth for what runs where and how.
- **docs/roadmap.md** - Development roadmap with 6 tiers, 156 PoCs, and 11
  STRAT items (99 complete). Tracks what's done, what's in progress, and
  what's planned. Tier 1 (Core Stabilization), Tier 2 (Safety Spine, HORI
  1.5), Tier 3 (First Contact, HORI 1.6), Tier 3.5 (Soak Period Strategic
  & Soak-Safe Work), Tier 4 (Progressive Capabilities, HORI 2.0), Tier 5
  (Deep Backlog), Tier 6 (Experience Vision). Includes the Sherpa
  behavioral guardian (PoC 15.50) and adversarial test suite (TDD).

### Safety & Governance
- **docs/governance_safety.md** - High-level safety protocol (the "No-Wipe"
  rule, anti-lockout, financial/identity safety). The original safety
  constitution. Still authoritative for current operations.
- **docs/tool_safety.md** - Detailed tool safety architecture. The 5-layer
  defense model (structured output, taint tracking, sandbox, human-in-the-
  loop, audit) plus the Sherpa behavioral guardian. Read this before
  implementing any tool access.
- **docs/tool_safety_redteam.md** - Adversarial analysis: 10 attack vectors
  from GLM-5.2's red team, 5 blind spots from Gemini Pro's review, and a
  real-world incident synthesis (Hugging Face intrusion, PocketOS, nono CVE,
  Sandlock, agentpen, AISI benchmarks) with 14 incident-driven PoCs
  (15.37-15.50). Includes an attack vector coverage matrix with spine vs
  HORI 2.0 tier annotations. Read this to understand what the safety
  architecture defends against.
- **docs/system_security_audit.md** - Investigation of the actual host
  system's attack surface: what sensitive data is accessible, what services
  are network-reachable, and what hardening is needed before the safety
  spine. Read this before implementing any tool access.

### Operational Notes
- **docs/operations.md** - Runtime operational details: build commands, service
  configuration, ports, endpoints, inference optimizations, benchmark
  results. THE source of truth for "how do I run/build/test this?"
- **docs/elastic_context_window.md** - Design and rationale for the elastic
  context window: semantic retrieval of past turns from Qdrant, ranked by
  relevance to the current prompt. The mechanism that makes the "10K
  guarantee" real. Includes competitive moat analysis, 500-turn test
  baseline data, deployed results (PoC 13.7 deflection mitigation),
  failed approaches, and recommendations for next steps.
- **docs/apple_shortcut_setup.md** - Current iOS voice shortcut setup guide.
  Direct aios-core HTTP, no n8n needed.

### Intent Hierarchy (JSON)
- **core/intent/manifesto.json** - Machine-readable manifesto (ingested into
  RAG by the red-team engine)
- **core/intent/charter.json** - Machine-readable project charter
- **core/intent/schema.json** - JSON schema for work orders
- **core/intent/work_order.json** - Template work order

## Superseded Documents (do NOT follow)

- **docs/poc_apple_interaction.md** - SUPERSEDED by docs/apple_shortcut_setup.md.
  Describes the old n8n-based voice flow. The current flow is direct
  aios-core HTTP. Kept for historical reference only.

## Historical Documents (context only, not authoritative)

- **docs/architecture_analysis.md** - Early planning document from project
  inception. Contains stale references (Ollama, "32GB VRAM", software
  suggestions that have since been deployed). Read for design philosophy
  and the Low Entropy Protocol concept, not for current configuration.
  For current config, see docs/stack.md and docs/operations.md.

## Document Hierarchy (conflict resolution)

When documents conflict, follow this priority:

1. **docs/stack.md** - for hardware, software, and model configuration
2. **docs/operations.md** - for runtime operational details and commands
3. **docs/roadmap.md** - for project status and planned work
4. **docs/tool_safety.md** + **docs/tool_safety_redteam.md** - for safety
   architecture (Phase 15+)
5. **docs/governance_safety.md** - for high-level safety principles
6. **docs/manifesto.md** - for mission and philosophy (not technical config)
7. **docs/apple_shortcut_setup.md** - for voice setup

If manifesto.md says "uncompressed KV cache" but stack.md says "turbo4 KV
cache", stack.md wins. The manifesto describes philosophy; stack.md
describes what's actually running.

## What's NOT in the docs (lives in code)

- Service implementation: `services/aios_core/`, `services/red_teaming/`,
  `services/proactive_agent/`, `services/recovery/`, `services/sherpa/` (planned)
- Test suite: `services/aios_core/test_*.py`, `tests/stress/`,
  `services/integration_tests/`, `tests/adversarial/` (planned, TDD)
- Scripts: `scripts/` (ingestion, consolidation, testing)
- State: `core/state/` (user_model.json, project_state.json, incidents.json)
- Intent: `core/intent/` (manifesto, charter, schema, work order)
