---
name: tdd
description: Test-driven implementation loop — write a failing test, make it pass, refactor. Small steps only.
argument-hint: "[feature, PRD path, or work-order id]"
triggers:
  - user
  - model
allowed-tools:
  - read
  - grep
  - glob
  - edit
  - write
  - exec
---

Implement a feature using strict TDD. This generalizes AIOS's existing
adversarial-TDD discipline (Manifesto Pillar VII) from safety-only code to
all code. The canonical example of the pattern is
`tests/adversarial/test_hallucination_claim.py` — a test written FIRST, marked
`xfail`, that defines the contract before the safety exists.

## The loop — never skip a step

Repeat until the feature is done:

1. **Red:** Write exactly one test that captures the next smallest slice of
   behavior. Run it. It MUST fail (and fail for the right reason — an
   assertion failure or import of not-yet-written code, not a syntax error
   in the test). For safety properties, place the test in
   `tests/adversarial/` and mark it `xfail` until the safety is built.
2. **Green:** Write the minimum code to make the test pass. No more. No
   "while I'm here" refactors. No extra features the test does not demand.
3. **Refactor:** Only after green. Restructure without changing behavior.
   Re-run the test after every refactor step. Stop refactoring the moment
   the test is green again.

This is the opposite of "outrunning your headlights" (*The Pragmatic
Programmer*). If you find yourself writing more than ~50 lines between test
runs, stop — the step is too big. Back out and write a smaller test.

## AIOS-specific rules

- **Test placement:** safety properties → `tests/adversarial/` (xfail-first,
  see the existing files for the docstring pattern: state what it defends and
  which PoC it traces to). General behavior → unit tests colocated with the
  service (e.g. `services/<svc>/test_*.py`). Cross-service →
  `services/integration_tests/`. Long-running → `tests/stress/`.
- **Run commands:** use the Makefile targets — `make test-unit`,
  `make test-adversarial`, `make test-integration`, `make test-regression`,
  `make test-stress`. Never invoke pytest directly with ad-hoc flags; the
  Makefile is the source of truth for how tests run.
- **Safety spine:** if the feature touches the tool registry, kernel
  (Landlock/seccomp), validation, guard rails, or Sherpa, the adversarial
  test is non-negotiable and must exist and fail BEFORE you write the
  implementation. No exceptions. This is Pillar VII.
- **Documentation sync:** every new module needs a docstring explaining
  *why* it exists, *what* it defends against (if safety), and *which
  source-of-truth doc* it traces to. Code-doc divergence is a bug.
- **Simplicity:** Pillar VII "Simplicity as Security" — each layer must
  justify itself. If the green step added a layer, be ready to argue why it
  is irreducible.

## Before starting

If a PRD exists at `docs/prd/<slug>.md` or a work order at
`core/state/proposed_work_orders/<id>.json`, read it first and implement
against its test contract (section 4 of the PRD). If neither exists for a
non-trivial feature, stop and suggest the user run `/grill-me` or
`/write-prd` first — do not implement a non-trivial feature with no design
artifact.

## When done

Run the full relevant suite (`make test` if broadly scoped, or the specific
target if narrowly scoped). Report which tests went red→green and confirm
the adversarial test (if any) now passes without `xfail`.
