# HORI Documentation

> **New here?** Start with the [root README](../README.md) for a project
> overview and quickstart. This index lists all documentation with
> canonical status.

## Public Documentation

### Mission & Architecture
- **docs/manifesto.md** — The mission, 7 pillars, core values, experience
  vision. The "constitution" of HORI. Read this first for philosophy.
- **docs/operations.md** — Reference hardware, build/test commands,
  inference configuration, endpoints, benchmarks. THE source of truth
  for "what runs where and how."
- **docs/roadmap.md** — Development roadmap (Tiers 1-7). Tracks what's
  done, what's in progress, and what's planned.

### Safety
- **docs/safety.md** — Safety principles, the 5-layer defense
  architecture, the Sherpa behavioral guardian, and adversarial analysis
  (10 attack vectors, real-world incident synthesis, coverage matrix).
  Read this before implementing any tool access.

### Operational Notes
- **docs/apple_shortcut_setup.md** — iOS voice shortcut setup guide.
- **docs/competitive_landscape.md** — Strategic positioning against
  existing AI agent systems. Backs STRAT-1.

### Historical (context only, not authoritative)
- **docs/archive/architecture_analysis.md** — Early planning document.
  Read for design philosophy, not current configuration.
- **docs/archive/best_practice_research.md** — Research notes that
  informed the Devin skills. Kept for traceability.

## Internal Documentation (.devin/)

Not linked from public docs. Internal contributors know where these are.

- **.devin/AGENTS.md** — Working agreement, crash recovery, service
  operations, web surfaces, Devin skills.
- **.devin/SLICE_LOG.md** — Current work status (read first on resume).
- **.devin/uncertainty.md** — Open unknowns and genuine uncertainties.
- **.devin/ubiquitous_language.md** — Canonical glossary (DDD).
- **.devin/skills/** — Devin skills (`/grill-me`, `/tdd`, `/write-prd`,
  `/ubiquitous-language`).

## Conflict Resolution

When documents conflict, follow this priority:

1. **docs/operations.md** — for hardware, software, model config, and
   runtime operational details
2. **docs/roadmap.md** — for project status and planned work
3. **docs/safety.md** — for safety architecture and adversarial analysis
4. **docs/manifesto.md** — for mission and philosophy (not technical config)

If manifesto.md says "uncompressed KV cache" but operations.md says
"turbo4 KV cache", operations.md wins. The manifesto describes philosophy;
operations.md describes what's actually running.

## What Does NOT Belong in This Repo

This is a **public** repository. See [CONTRIBUTING.md](../CONTRIBUTING.md)
→ "Public Repo Hygiene" for the full policy. The short version: never
commit patents, secrets, personal information, or internal infrastructure
details. Use placeholders, not real values. The pre-push hook catches
common patterns.
