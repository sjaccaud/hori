# PRD — Typed-Decision Service /decide (SLICE-18, WO-01)

## 1. Intent trace

- **Pillar I — Tiered Reasoning:** this is the S1 layer of the S1+S2
  cascade: high-speed local judgment (nano 3B, single forward pass) for
  immediate typed decisions, escalating to the 27B tier only where
  judgment quality demands it. Compute optimization in the literal sense —
  one forward pass instead of a generate-then-parse loop.
- **Pillar VII — Simplicity as Security:** the interface is one POST route
  returning data. It deletes the ad-hoc generate-then-parse judgment paths
  (WO-03) instead of adding a smarter one.
- **Pillar VII — LLM proposes, architecture executes:** /decide output is
  inert data. Consumers decide what to do with it; nothing here executes.
- **Roadmap:** off-roadmap PoC — infrastructure for WO-02 shadow
  validation and WO-03 parser-deletion; justifies itself if shadow data
  (WO-02) shows the S1 filter premise holds.
- **Work order:** `core/state/proposed_work_orders/typed-decisions-01.json`
  (priority high; series designed ep-009, mechanics verified live ep-008/010,
  baseline gate WO-00 passed 2026-09-19).

## 2. Interface contract

New service `aios-decide` (FastAPI, loopback `127.0.0.1:8773`, systemd
unit `services/systemd/aios-decide.service`).

```
POST /decide
{
  "text": "<subject under judgment>",
  "questions": [
    {"id": "verdict",  "type": "choice", "question": "What should happen to this item?",
     "options": ["keep", "drop", "defer"], "min_prob": 0.5},
    {"id": "relevance","type": "score",  "options": ["1","2","3","4","5"]},
    {"id": "transcribe","type": "noul"}                    // fixed {yes,no,unknown}
  ]
}

200 →
{
  "request_id": "<uuid>",
  "model": "nano",
  "decisions": [
    {"id": "verdict", "type": "choice",
     "probabilities": {"keep": 0.62, "drop": 0.25, "defer": 0.08, "unknown": 0.05},
     "chosen": "keep", "prob": 0.62, "abstained": false}
  ]
}
```

Contract rules:

- **Question types:** `choice` (caller options), `score` (caller options,
  ordinal), `noul` (fixed options `["yes","no","unknown"]`, caller supplies
  none). Unknown types → 422.
- **Letter encoding:** every question's options map to single-token
  letters A, B, C, …; the service always appends an `"unknown"` option.
  Caller options are capped at 25 (A–Y + Z=unknown).
- **One forward pass per question:** for each question, one
  `POST :8090/completion` with `n_predict:1, n_probs:>=len(options)`.
  The prompt ends at the answer position so the first generated token IS
  the letter; top_logprobs over letter tokens → softmax → per-option
  probabilities. Options sharing a leading prefix (instruction + text)
  are ordered so llama.cpp's radix cache reuses the prompt KV across
  questions in the same request (shared-prefix caching).
- **Abstain:** `"unknown"` winning, or `chosen` prob < `min_prob`
  (default 0.5, per-question override), yields `chosen: null,
  abstained: true`.
- **insufficient_context:** `text` empty/whitespace or below
  `MIN_TEXT_CHARS` → that request returns `decisions` with
  `"result": "insufficient_context"` per question and nano is never called.
- **no_answer:** if no option letter appears in nano's top_logprobs (the
  model didn't play along), that question returns
  `"result": "no_answer"`, `chosen: null`, `abstained: true`.
- **`question` field:** required string per question — the natural-language
  question asked about `text`. (The WO sketch elided it; the prompt is
  meaningless without it.)
- **max_input_chars:** `text` is truncated at `MAX_INPUT_CHARS` before
  prompt assembly; response sets `"truncated": true`.
- **Bounds:** `len(questions)` ≤ 16, `len(options)` ≤ 25, `len(id)` ≤ 64,
  duplicate ids → 422. Text > `MAX_INPUT_CHARS` is truncated, not rejected.
- **Fail-closed:** nano unreachable / malformed nano reply → 503
  `{"error": "decider_unavailable"}`. The service NEVER fabricates
  probabilities.
- **Decision log:** each request appends one JSONL line to
  `~/.local/share/hori/decisions/decisions-<YYYYMMDD>.jsonl`:
  `{ts, request_id, model, latency_ms, text, questions, decisions}` —
  this is the calibration dataset for WO-02/04.
- `GET /healthz` → `{"status": "ok", "nano": "up"|"down"}`.

Core module `services/aios_decide/decide.py` (importable, network calls
injected for tests):

```python
def build_prompt(text: str, question: Question) -> str
def map_letters(options: list[str]) -> dict[str, str]   # "A" -> option label
def probabilities_from_logprobs(top_logprobs: dict, letter_map: dict) -> dict
def should_abstain(probs: dict, min_prob: float) -> tuple[bool, str | None]
def decide_questions(text: str, questions: list[Question],
                     call_completion) -> list[Decision]  # call_completion injected
def append_decision_log(log_dir: Path, record: dict) -> None
```

## 3. Safety classification

**Side-effect, minimal:** writes append-only JSONL under the decisions
dir; calls nano over loopback; binds loopback only. **Not inside the
safety spine** — no tool registry, no Landlock-managed paths, no Sherpa
surface, advisory only. Consumers stay fail-closed (WO-03 flags).

Enforced invariants:

- **Fail-closed:** nano down → 503, never a synthesized distribution.
- **Confinement:** log writes resolve inside `DECIDE_LOG_DIR`
  (default `~/.local/share/hori/decisions/`); no request field reaches
  a filesystem path.
- **Bounded work:** question/option/id caps above; nano calls are
  sequential, `n_predict:1` — a /decide request costs ≤16 tiny forward
  passes; no polling loops (ai-power-manager idle-flap constraint).
- **Loopback only:** uvicorn binds `127.0.0.1`; systemd unit hardens like
  `local-tools-adaptive` (NoNewPrivileges, ProtectSystem=strict,
  ProtectHome=read-only, ReadWritePaths=decisions dir).

## 4. Test contract

- **Unit:** `services/aios_decide/test_decide.py` —
  `test_choice_letter_mapping`, `test_probabilities_normalized`,
  `test_abstain_below_min_prob`, `test_unknown_wins_abstains`,
  `test_insufficient_context_skips_nano`, `test_max_input_chars_truncates`,
  `test_noul_fixed_options`, `test_duplicate_question_ids_422`,
  `test_option_cap_422`, `test_shared_prefix_across_questions`,
  `test_decision_log_jsonl_shape` (tmp_path), `test_healthz`.
- **Adversarial (WRITTEN FIRST, must fail before implementation):**
  `tests/adversarial/test_decide_fail_closed.py` —
  `test_nano_down_returns_503_never_fabricates`,
  `test_malformed_nano_reply_returns_503`,
  `test_log_writes_confined_to_decisions_dir`,
  `test_unknown_question_type_rejected`,
  `test_request_cannot_set_log_path`.
  Defends the decision log's integrity and the fail-closed contract
  against an untrusted caller/model pair. Pillar VII — advisory services
  still never fake their outputs.
- **Integration:** none in this chunk — demo is a live `curl` to :8773
  against real nano (WO-01: service + adversarial tests + demo curl only).

## 5. Module placement

`services/aios_decide/` — new top-level service dir (sibling of
`tool_daemon`, `proactive_agent`): `decide.py` core logic, `server.py`
FastAPI app + `__main__` uvicorn runner, `test_decide.py` colocated.
Separate process from `aios_core` because: different failure domain (an
S1 helper must not take down the intelligence layer), different sandbox
profile, and WO-05 may later expose it as an optional tool endpoint —
keeping it standalone makes that exposure opt-in. Deep module: the public
surface is the POST contract + `decide_questions`; prompt engineering and
logprob plumbing stay behind it.

Systemd: `services/systemd/aios-decide.service` — Type=simple,
same User as `local-tools-adaptive`, After/Wants llama-nano-adaptive.service, hardening copied
from `local-tools-adaptive.service`, `ReadWritePaths=` the decisions dir,
env `DECIDE_HOST=127.0.0.1`, `DECIDE_PORT=8773`, `DECIDE_NANO_URL`.

## 6. Out of scope

- Any consumer wiring (WO-02 shadow triage, WO-03 migrations) — this
  chunk ships the service dark.
- Calibration fitting, isotonic/temperature params (WO-04).
- Request-path integrations: voice gate, preset routing, loop-watcher
  (WO-05 — gated on shadow data).
- MCP exposure, auth, non-loopback binding.
- Multi-token option labels, custom question types, streaming.
- Changing nano's model/ctx — the sidecar stays Llama-3.2-3B per ep-010.

## 7. Work-order link

`core/state/proposed_work_orders/typed-decisions-01.json`
