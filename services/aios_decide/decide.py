"""Core logic for the typed-decision service (WO-01 / SLICE-18).

Why it exists: the stack does generate-then-parse judgment in several
places (idea-factory triage regex, LLM-asserted relevance floats). This
is the S1 primitive that replaces them: typed questions answered by a
single-token top_logprobs read on the nano sidecar — one forward pass, a
real probability distribution, an explicit abstain contract.

What it defends against: fabricated answers (fail-closed when nano is
down or replies with garbage — DeciderUnavailable, never a synthesized
distribution), unbounded sidecar prefill (MAX_INPUT_CHARS), and log
escape (writes resolve inside the decisions dir only). Advisory only —
output is inert data; consumers decide what to do with it.

Traces to: .devin/prd/decide-service.md (interface + safety contract),
docs/manifesto.md Pillar I (tiered reasoning) and VII (simplicity as
security).
"""

from __future__ import annotations

import json
import math
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from operator import itemgetter
from pathlib import Path

UNKNOWN_LABEL = "unknown"
NOUL_OPTIONS = ("yes", "no")
QUESTION_TYPES = ("choice", "score", "noul")
MAX_QUESTIONS = 16
MAX_OPTIONS = 25  # caller options A..Y; the next letter is always "unknown"
MAX_ID_CHARS = 64
MIN_TEXT_CHARS = 8
MAX_INPUT_CHARS = 8000
N_PROBS = 32
DEFAULT_MIN_PROB = 0.5
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_PREFIX_TEMPLATE = (
    "You answer typed questions about a TEXT. "
    "Reply with ONLY the letter of the best option.\n\n"
    'TEXT:\n"""\n{text}\n"""\n\n'
)


class DeciderUnavailable(Exception):
    """Nano is unreachable or returned a malformed reply. Fail closed."""


@dataclass(frozen=True)
class Question:
    id: str
    type: str  # "choice" | "score" | "noul"
    question: str
    options: tuple = ()
    min_prob: float = DEFAULT_MIN_PROB


def options_for(question: Question) -> list[str]:
    if question.type == "noul":
        return list(NOUL_OPTIONS)
    return [str(o) for o in question.options]


def map_letters(options: list[str]) -> dict[str, str]:
    """Map option labels to single-token letters; 'unknown' is appended last."""
    opts = list(options) + [UNKNOWN_LABEL]
    return {LETTERS[i]: label for i, label in enumerate(opts)}


def build_prefix(text: str) -> str:
    """The part every question in a request shares — kept byte-identical so
    llama.cpp's prompt cache reuses the KV across questions."""
    return _PREFIX_TEMPLATE.format(text=text)


def build_prompt(text: str, question: Question) -> str:
    parts = [build_prefix(text), f"Question: {question.question}\n"]
    for letter, label in map_letters(options_for(question)).items():
        parts.append(f"{letter}) {label}\n")
    parts.append("Answer:")
    return "".join(parts)


def probabilities_from_logprobs(
    top_logprobs: list[dict], letter_map: dict[str, str]
) -> dict[str, float]:
    """Softmax over the letter tokens present in top_logprobs, renormalized
    across ALL options (missing letters -> 0). Empty if no letter appears."""
    found: dict[str, float] = {}
    for entry in top_logprobs:
        letter = str(entry.get("token", "")).strip().upper()
        if letter in letter_map and letter not in found:
            found[letter] = math.exp(float(entry["logprob"]))
    total = sum(found.values())
    if total <= 0:
        return {}
    return {label: found.get(letter, 0.0) / total for letter, label in letter_map.items()}


def should_abstain(probs: dict[str, float], min_prob: float) -> tuple[bool, str | None]:
    """Abstain when 'unknown' wins or the best option is below min_prob."""
    if not probs:
        return True, None
    chosen, p = max(probs.items(), key=itemgetter(1))
    if chosen == UNKNOWN_LABEL or p < min_prob:
        return True, None
    return False, chosen


def _extract_top_logprobs(resp: dict) -> list[dict]:
    probs_list = resp.get("completion_probabilities")
    if not isinstance(probs_list, list) or not probs_list:
        raise DeciderUnavailable("nano reply lacks completion_probabilities")
    top = probs_list[0].get("top_logprobs")
    if not isinstance(top, list):
        raise DeciderUnavailable("nano reply lacks top_logprobs")
    return top


def _decision(question: Question, **kw) -> dict:
    d = {"id": question.id, "type": question.type, "result": "answered",
         "probabilities": {}, "chosen": None, "prob": 0.0, "abstained": True}
    d.update(kw)
    return d


def decide_questions(
    text: str, questions: list[Question], call_completion
) -> list[dict]:
    """Answer each question with one nano forward pass. call_completion is
    injected (tests never hit the network); any transport error or
    malformed reply aborts the request via DeciderUnavailable."""
    if len(text.strip()) < MIN_TEXT_CHARS:
        return [_decision(q, result="insufficient_context") for q in questions]

    text = text[:MAX_INPUT_CHARS]
    decisions = []
    for q in questions:
        letter_map = map_letters(options_for(q))
        prompt = build_prompt(text, q)
        payload = {
            "prompt": prompt,
            "n_predict": 1,
            "n_probs": N_PROBS,
            "temperature": 0,
            "cache_prompt": True,
        }
        try:
            resp = call_completion(payload)
        except DeciderUnavailable:
            raise
        except Exception as e:
            raise DeciderUnavailable(str(e)) from e
        probs = probabilities_from_logprobs(_extract_top_logprobs(resp), letter_map)
        if not probs:
            decisions.append(_decision(q, result="no_answer"))
            continue
        abstained, chosen = should_abstain(probs, q.min_prob)
        decisions.append(_decision(
            q, probabilities=probs, chosen=chosen,
            prob=max(probs.values()),
            abstained=abstained))
    return decisions


def append_decision_log(log_dir: Path, record: dict) -> Path:
    """Append one JSONL line inside log_dir. The filename is server-derived
    (date) — no request field ever reaches it."""
    root = Path(log_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = (root / f"decisions-{date.today():%Y%m%d}.jsonl").resolve()
    if path.parent != root:
        raise ValueError("log path escapes decisions dir")
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    return path


def http_completion(nano_url: str, timeout: float = 15.0):
    """Real transport: POST to llama-server's /completion on nano."""
    url = nano_url.rstrip("/") + "/completion"

    def call(payload: dict) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    return call
