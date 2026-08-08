---
name: write-prd
description: Produce an AIOS work-order PRD (interface + test contract) for a feature, delegating implementation to /tdd
argument-hint: "[feature or work-order id]"
triggers:
  - user
  - model
allowed-tools:
  - read
  - grep
  - glob
  - write
---

Write a Product Requirements Document for an AIOS feature — but emit it in
AIOS's native intent-hierarchy shape, not a generic PRD. This skill exists so
the human retains high-level strategic control of the interface and tests
while delegating tactical implementation to the AI (Manifesto Pillar VII,
"Design the interface, delegate the implementation").

## Read first

1. `core/intent/schema.json` — the `work_order` and `charter` definitions.
   Your output must conform.
2. `core/intent/work_order.json` and `core/intent/charter.json` — style/ID
   examples.
3. `docs/manifesto.md` — confirm the PRD does not violate a pillar.
4. `docs/roadmap.md` — locate the matching tier/PoC, or mark off-roadmap.
5. The source modules the feature touches — enough to specify the interface
   boundary accurately.

## PRD sections (write all of them)

Produce a single markdown file at `docs/prd/<slug>.md` containing:

1. **Intent trace** — which manifesto pillar and roadmap PoC this satisfies,
   or an explicit "off-roadmap" justification.
2. **Interface contract** — the *simple* public interface (functions, HTTP
   routes, tool-call schema). This is the "gray box" boundary the human owns.
   Keep it small (Pillar VII: Simplicity as Security).
3. **Safety classification** — read-only / side-effect / destructive. If
   anything other than read-only, name the safety-spine layers involved
   (kernel, registry, validation, guard rails, Sherpa) and the fail-closed
   behavior.
4. **Test contract** — the unit test that proves it works AND the adversarial
   test in `tests/adversarial/` that proves it cannot be abused. Name both
   files. For safety properties the adversarial test is written FIRST and
   must fail before implementation (Pillar VII TDD).
5. **Module placement** — which service dir it lives in and why, with
   attention to deep-module boundaries (large behavior behind the simple
   interface from section 2).
6. **Out of scope** — explicit non-goals.
7. **Work-order link** — the `core/state/proposed_work_orders/<id>.json`
   this PRD corresponds to (create it if it doesn't exist, conforming to
   `schema.json`, `status: "backlog"`).

## Hard rules

- Do not write implementation code. Interface signatures and test *names*
  only.
- Do not specify internal implementation details beyond the module boundary.
  The internals are delegated to `/tdd`.
- If the feature touches the safety spine, the adversarial test name in
  section 4 is mandatory and must follow the `tests/adversarial/test_*.py`
  docstring pattern (state what it defends and that it must fail first).

When done, suggest the user run `/tdd` to implement against the test contract.
