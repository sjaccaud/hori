# PRD — /decide Shadow Triage (WO-02, part of SLICE-18)

## Status — 2026-10-02 (WO-03 resolution)

Distilled model trained, evaluated, **deployed as the production decide
backend**. The `:8096` llama-decide service now serves the r8 QLoRA
adapter (`idea_factory/distill/`); `:8773` aios-decide unchanged.

Holdout (248 items, fresh replay vs qwen teacher + vs the prior
Sep-28 prod backend):

| metric | old prod backend | r8 distill |
|---|---|---|
| hard_no agreement | 13% (54% false-veto) | 97% (0.8% FP) |
| hard_no recall | 56% | 44% (4/9 — thin sample) |
| score abstain | 88–94% | 4–23% |
| verdict agreement | 37% | 84% |
| fit spearman | +0.13 | +0.74 |

WO-03 landed in **advisory** form, not gate-replacement: `decide_verdict`
columns on `items`/`candidates` record the sidecar verdict per triaged
item; the digest renders it (`decide:` chip, ⚠ on promote↔reject/hard-no
disagreements, floor marker where decide says `promote` on a killed
item). Soak posture: observe the flags for a few weeks before any
call-site migration — veto recall is still the watch item.

## 1. Intent trace

- **Pillar I — Tiered Reasoning:** this is the experiment that proves or
  kills the S1-filter premise: nano typed decisions run *alongside* the
  27B verdicts on the same items, logging both.
- **Pillar VII:** shadow output is observational — it can never change a
  triage outcome (the measurement cannot perturb the measured).
- **Roadmap:** off-roadmap; gate for WO-03 (parser deletion) — numbers
  decide whether migration is justified.
- **Work order:**
  `core/state/proposed_work_orders/typed-decisions-02-shadow-triage.json`
  (depends on WO-01, done 2026-09-19).

## 2. Interface contract

New module `idea_factory/shadow.py` (in the idea_factory repo — the
consumer side; nothing new lands in aios):

```python
DECIDE_URL    # env IDEA_DECIDE_URL, default http://127.0.0.1:8773
SHADOW_DIR    # STATE_DIR / "shadow"

def build_questions() -> list[dict]      # the 5 typed questions below
def item_text(profile: str, row) -> str  # "OWNER PROFILE:\n..\n\nITEM:\n{title}\n{summary[:800]}"
def run_shadow(text: str, questions: list, timeout: float = 20.0) -> dict
    # POST DECIDE_URL/decide -> response JSON (raises on transport/HTTP error)
def log_pair(row, qwen_side: dict, nano_resp: dict) -> Path
    # append one JSONL line to SHADOW_DIR/shadow-<YYYYMMDD>.jsonl
def shadow_item(profile: str, row, qwen_side: dict) -> None
    # orchestrates text->decide->log; ALL exceptions swallowed + logged
```

`triage.py` hook (additive only): inside the per-item loop, after the
existing triage write/candidate insert, one guarded call
`shadow_item(profile, r, qwen_side)`. qwen_side captures the scores dict +
derived outcome (`candidate|hard-no|below-floor|unscored`). Disable via
`--no-shadow` or `IDEA_SHADOW=0`; default ON.

Typed questions per item (the nano side of every pair):

| id | type | question |
|---|---|---|
| `hard_no` | noul | "Does this item violate an explicit 'Hard no' rule in the owner profile?" |
| `fit` | score 1–5 | "How well does this item fit the owner's stated interests?" |
| `lucrative` | score 1–5 | "How likely could this realistically make money for a solo ~4h/week operator?" |
| `actionable` | score 1–5 | "How quickly could a first concrete step finish? 1=months 5=within a day" |
| `verdict` | choice promote/borderline/reject | "Overall verdict for the digest?" |

Pair record `{ts, item_id, title, source_id, qwen:{...scores,outcome},
nano:{request_id, decisions}}` — the agreement-analysis dataset.
(/decide's own log stays as the raw nano-side record.)

`idea_factory/shadow_report.py` (stdlib): reads `SHADOW_DIR/*.jsonl` and
prints the acceptance report — total pairs, abstain rate per question
(alarm >30%), per-type agreement: noul confusion matrix, per-score
Spearman rho + mean |Δ| vs qwen (nano 1–5 scaled to 0–1), verdict
agreement rate.

## 3. Safety classification

**Side-effect, minimal:** appends JSONL under `STATE_DIR/shadow/` only;
POSTs to loopback :8773; no DB writes, no candidate changes.

Enforced invariants:

- **Observational only:** `shadow_item` catches everything — a /decide
  outage, a malformed reply, a slow call can never alter, delay, or drop
  a triage outcome. Behavior change = bug.
- **Cost bound:** 5 nano calls/item (~100 ms each measured live) →
  ~+4 s worst case on a 40-item batch; event-driven, inside the existing
  05:00 batch window (no new timers, no polling).
- **Confinement:** pair log path is server-derived (date) under
  SHADOW_DIR; no request field reaches it.

## 4. Test contract

idea_factory has no test harness — contract is verified live:

- `test_shadow.py` (stdlib unittest): `test_build_questions_shape`,
  `test_item_text_contains_profile_and_item`, `test_log_pair_jsonl`,
  `test_shadow_item_swallows_decide_outage` (decide URL dead → triage
  path unaffected), `test_shadow_disabled_flag`.
- **Live verification:** real `triage --limit` run produces pair lines;
  `shadow_report.py` prints the agreement table.

## 5. Module placement

`idea_factory/` package beside `triage.py` — the shadow is a property of
the consumer, not the service. Report script same dir. Shadow log under
`state/shadow/` (gitignored state, like digests/).

## 6. Out of scope

- Any triage behavior change, flag-gated or not (that is WO-03).
- Re-scoring historical items (per-dimension qwen scores aren't stored
  for non-candidates — pairs can only form going forward).
- Calibration fitting (WO-04) — the report computes agreement, it does
  not tune min_prob.
- Running shadow on `--endpoint` variants other than the production
  batch (shadow always talks to DECIDE_URL).

## 7. Work-order link

`core/state/proposed_work_orders/typed-decisions-02-shadow-triage.json`
