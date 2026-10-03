# PRD — Decide Calibration Loop (WO-04)

## 1. Intent trace

- **Pillar I — Tiered Reasoning:** closes the loop on the S1+S2 cascade.
  The nano sidecar's probability estimates are only trustworthy if
  measured; this job measures them against the 27B tier as audit oracle
  and refits the abstain contract against real agreement data.
- **Pillar VII — Simplicity as Security:** offline batch job over append-
  only logs. No new daemons, no live-path changes — the /decide contract
  is untouched. Output is inert artifacts (report + refit params);
  nothing auto-applies.
- **Work order:** `core/state/proposed_work_orders/typed-decisions-04-calibration-loop.json`
  — "sample logged /decide decisions - confident AND borderline - through
  qwen-27B as audit oracle; refit isotonic calibration; auto-tune abstain
  thresholds against error budget; emit disagreement report + high-value
  exemplars for S1 prompt injection."
- **Prereq:** ≥100 logged pairs. Met: `~/.local/share/hori/decisions/`
  holds ~1500 records as of 2026-10-03.

## 2. Scope

Two slices:

- **Slice A (this PRD, implemented):** offline engine
  `services/aios_decide/calibrate.py` + CLI + tests. Loads decision logs,
  stratified-samples records, calls an injected oracle, computes agreement
  labels, fits isotonic calibration (PAVA), computes ECE, sweeps candidate
  `min_prob` thresholds against an error budget, writes the report +
  exemplars.
- **Slice B (follow-up):** systemd `calibrate.service`+`.timer` (nightly,
  sibling of aios-consolidation ~03:03), `ai-power-manager active --ttl`
  wrapper, gpu-lane classification (P2-compatible batch — router-mediated,
  low concurrency), Makefile install target.

## 3. Interface contract

### Module

```python
# services/aios_decide/calibrate.py

def load_records(log_dir: Path) -> list[dict]
    """Read decisions-*.jsonl; skip malformed lines; return records."""

def extract_samples(records: list[dict]) -> list[Sample]
    """One Sample per (record, decision) where result == 'answered' and
    probabilities is non-empty. Sample carries request_id, question id,
    type, chosen, prob, abstained, and the full probability map."""

def select_for_oracle(samples, n_borderline, n_confident, seed) -> list[Sample]
    """Deterministic stratified sample: borderline = prob in
    [min_prob - BAND, min_prob + BAND] non-abstained; confident = rest.
    Stable ordering for reproducibility (seed-shuffled, sorted ids)."""

def oracle_label(sample, oracle_answer) -> bool | None
    """Agreement rule: choice/noul -> exact match; score -> |Δ| <= 1.
    Oracle abstain/unknown/malformed -> None (unlabeled, not wrong)."""

def isotonic_fit(xs: list[float], ys: list[float]) -> list[tuple[float, float]]
    """PAVA isotonic regression; returns (threshold, fitted) breakpoints,
    non-decreasing. Empty input -> []."""

def isotonic_predict(fit, x) -> float
    """Piecewise-constant predict; clamps to fit range."""

def expected_calibration_error(labeled, buckets=10) -> float
    """ECE = Σ_b (|B_b|/N) |acc_b - conf_b|. Unlabeled excluded."""

def suggest_min_prob(labeled, error_budget) -> float | None
    """Sweep thresholds on oracle-labeled samples: smallest min_prob whose
    empirical error rate (oracle disagrees) at prob >= t is <= budget.
    None if data too thin (<MIN_LABELED) or no feasible threshold."""

def run(log_dir, oracle, out_dir, seed=42, oracle_limit=60) -> Report
    """End-to-end: load -> sample -> oracle -> label -> fit -> report.
    `oracle` is injected (tests never hit the network). Writes
    report.md + exemplars.jsonl + params.json under out_dir/YYYY-MM-DD/."""
```

### CLI

```
PYTHONPATH=. python3 -m services.aios_decide.calibrate \
  --log-dir ~/.local/share/hori/decisions \
  --out-dir ~/.local/share/hori/calibration \
  [--oracle-url http://127.0.0.1:8080] [--oracle-limit 60] \
  [--seed 42] [--error-budget 0.15] [--dry-run]
```

`--dry-run` reports sampling + stats without oracle calls. Artifacts are
additive: re-running the same day overwrites that day's report dir.

## 4. Safety contract

- **Fail-closed oracle:** transport error, malformed reply, or oracle
  abstain all produce label `None` — never a wrong label.
- **Advisory only:** `params.json` (suggested min_prob, fit breakpoints)
  is a proposal. Nothing here edits service config — a human applies it.
- **Deterministic:** same inputs + seed → same sample set and report.
- **Containment:** reads log dir read-only; writes resolve inside
  out_dir (same resolve-and-check pattern as `append_decision_log`).
- **No oracle in tests:** `oracle` is a callable `(text, question) ->
  answer_label | None`; injected fake in tests, `http_oracle()` in prod.

## 5. Test contract

- `load_records` tolerates truncated/malformed lines and empty dirs.
- `extract_samples` skips insufficient_context/no_answer/abstained-only
  rows correctly and preserves question ids.
- `select_for_oracle` is deterministic under a seed, respects the
  borderline band, and never exceeds oracle_limit.
- `oracle_label`: exact-match for choice/noul; ±1 tolerance for score;
  oracle unknown/abstain → None.
- `isotonic_fit`: monotone output on shuffled inputs; degenerate
  (all-same-x, empty) inputs don't crash.
- `suggest_min_prob`: returns a threshold meeting the error budget when
  one exists; None on thin data; honors the "smallest feasible" rule.
- `run` end-to-end with a fake oracle writes all three artifacts and the
  report contains ECE, agreement rate, and the suggested min_prob.
- Coverage: calibrate.py ≥80%.

## 6. Open questions

- Oracle prompt shape: re-ask the same typed question verbatim vs. an
  audit-framed prompt. v1 uses verbatim (same surface, comparable probs).
- Whether score-tolerance ±1 is the right agreement rule vs. distance-
  weighted — flag in the disagreement report.
- Slice B timing: aios-consolidation runs ~03:03; calibrate after it
  (~03:20) inside the maintenance window.
