"""Adversarial test suite for the AIOS safety spine (Tier 2F).

Per Manifesto Pillar VII (Engineering Discipline): for every safety property,
the test that attempts to break it is written BEFORE the implementation.
The test must FAIL (because the safety doesn't exist yet), then the safety
is built until the test PASSES. These tests become regression tests after
the spine is built.

Traces to: docs/roadmap.md Tier 2F.
Traces to: docs/tool_safety_redteam.md (attack vector coverage matrix).

Test status legend:
  - Tests that PASS already: the safety property is defended by the code
    built so far (2B: registry + validation).
  - Tests that FAIL (xfail): the safety property is not yet implemented.
    These are the TDD contract — they define what 2C/2D/2E must build.
    Once the safety is built, remove the xfail marker and the test must pass.
"""
