---
name: ubiquitous-language
description: Scan the codebase + docs and maintain docs/ubiquitous_language.md — the canonical AIOS glossary
argument-hint: "[optional: term to define or refresh]"
subagent: true
agent: subagent_explore
triggers:
  - user
  - model
allowed-tools:
  - read
  - grep
  - glob
---

Build and maintain the AIOS ubiquitous-language glossary
(`docs/ubiquitous_language.md`). This fills a real gap: AIOS has rich domain
vocabulary scattered across `docs/manifesto.md`, `docs/roadmap.md`,
`docs/tool_safety.md`, `core/intent/schema.json`, and code docstrings, with
no single canonical reference. Per Domain-Driven Design, a shared vocabulary
keeps AI and human communication precise and low-entropy (Manifesto Pillar
VI).

## Run as a read-only subagent

This skill runs as a `subagent_explore` subagent — read-only, no edits. It
produces a **report** proposing glossary additions/changes. The main agent
(or user) then applies the edits to `docs/ubiquitous_language.md`. This keeps
the glossary update reviewable and avoids an unattended subagent rewriting a
canonical doc.

## Scan these sources

1. `docs/manifesto.md` — the 7 pillars and core values (highest authority for
   philosophical terms).
2. `docs/roadmap.md` — tier names, PoC identifiers, phase names.
3. `docs/tool_safety.md` and `docs/tool_safety_redteam.md` — safety-spine
   vocabulary (spine, layers, Sherpa, fail-closed, etc.).
4. `docs/operations.md` and `docs/stack.md` — operational/runtime terms.
5. `core/intent/schema.json` — intent-hierarchy terms (manifesto, charter,
   work order) and their field names.
6. `docs/architecture_analysis.md` — Low Entropy Protocol, alignment engine,
   intentional friction, temporal layers.
7. Code docstrings in `services/` — terms defined in code (e.g. "Sherpa",
   "guard rails", "response verification layer"). Grep for module
   docstrings starting with `"""`.

## For each term, capture

- **Term** (canonical form).
- **Definition** — one or two sentences, in the project's own words.
- **Source of truth** — the doc/file/line where it is authoritatively
  defined (per Manifesto Pillar VII: "Documentation as Architecture").
- **Aliases / variants** — other spellings or contracted forms used in the
  codebase (e.g. "fail-closed" vs "fail closed"). Mark the canonical one.
- **Pillar trace** — if the term relates to a manifesto pillar, name it.

## Conflict resolution

If sources disagree on a term's meaning, flag it explicitly in the report
rather than silently picking one. Per `docs/README.md`, `docs/stack.md` and
`docs/operations.md` win for technical configuration; `docs/manifesto.md` is
philosophy. Surface the conflict so the human can fix one source or the
other (code-doc divergence is a bug, Pillar VII).

## Output

Return a structured report:

1. **New terms** to add to `docs/ubiquitous_language.md` (with the fields
   above).
2. **Existing terms to update** — where the glossary is stale vs the code.
3. **Conflicts** — terms where sources disagree, with each source quoted.
4. **Stale entries** — glossary terms no longer found in the codebase.

Do not edit `docs/ubiquitous_language.md` yourself (read-only subagent). The
caller applies the changes after review.
