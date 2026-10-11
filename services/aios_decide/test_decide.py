"""Unit tests for the typed-decision core (WO-01 / SLICE-18).

Contract source: .devin/prd/decide-service.md section 4. Nano is never
touched — call_completion is injected.
"""

import json

import pytest
from fastapi.testclient import TestClient

from services.aios_decide.decide import (
    MAX_INPUT_CHARS,
    N_PROBS,
    DeciderUnavailable,
    Question,
    append_decision_log,
    build_prefix,
    build_prompt,
    decide_questions,
    map_letters,
    probabilities_from_logprobs,
    should_abstain,
)
from services.aios_decide.server import create_app

TEXT = "Idea: a CLI tool that turns markdown notes into reveal.js slides."


def _completion(top_logprobs, captured=None):
    """Fake nano /completion reply with a fixed top_logprobs list."""
    def call(payload):
        if captured is not None:
            captured.append(payload)
        return {"completion_probabilities": [{"top_logprobs": top_logprobs}]}
    return call


def _letters(*logprobs):
    """top_logprobs entries for letters A,B,C,... with given logprobs."""
    return [
        {"token": f" {chr(65 + i)}", "logprob": lp}
        for i, lp in enumerate(logprobs)
    ]


# --- pure functions -------------------------------------------------------

def test_choice_letter_mapping():
    m = map_letters(["keep", "drop"])
    assert m == {"A": "keep", "B": "drop", "C": "unknown"}


def test_noul_fixed_options():
    q = Question(id="q", type="noul", question="Transcribe?")
    m = map_letters(["yes", "no"])
    assert list(m.values()) == ["yes", "no", "unknown"]
    assert m["C"] == "unknown"


def test_probabilities_normalized():
    m = map_letters(["a", "b"])  # A,B,C(unknown)
    probs = probabilities_from_logprobs(_letters(-0.1, -2.0), m)
    assert pytest.approx(sum(probs.values()), abs=1e-9) == 1.0
    assert probs["a"] > probs["b"] > probs["unknown"] == 0.0


def test_probabilities_handles_whitespace_and_case():
    m = map_letters(["x", "y"])
    top = [{"token": "a", "logprob": -0.5}, {"token": " B ", "logprob": -0.5}]
    probs = probabilities_from_logprobs(top, m)
    assert pytest.approx(probs["x"], abs=1e-9) == pytest.approx(probs["y"])


def test_abstain_below_min_prob():
    probs = {"yes": 0.4, "no": 0.35, "unknown": 0.25}
    abstained, chosen = should_abstain(probs, 0.5)
    assert abstained and chosen is None


def test_unknown_wins_abstains():
    probs = {"yes": 0.2, "no": 0.1, "unknown": 0.7}
    abstained, chosen = should_abstain(probs, 0.5)
    assert abstained and chosen is None


def test_confident_answer_chosen():
    probs = {"yes": 0.8, "no": 0.15, "unknown": 0.05}
    abstained, chosen = should_abstain(probs, 0.5)
    assert not abstained and chosen == "yes"


# --- decide_questions -----------------------------------------------------

def test_insufficient_context_skips_nano():
    calls = []
    q = Question(id="q", type="noul", question="Run?")
    out = decide_questions("   ", [q], _completion(_letters(-0.1), calls))
    assert out[0]["result"] == "insufficient_context"
    assert out[0]["abstained"] and out[0]["chosen"] is None
    assert calls == []


def test_max_input_chars_truncates():
    calls = []
    q = Question(id="q", type="noul", question="Run?")
    decide_questions("x" * (MAX_INPUT_CHARS + 500), [q],
                     _completion(_letters(-0.1), calls))
    assert len(calls) == 1
    assert ("x" * MAX_INPUT_CHARS) in calls[0]["prompt"]
    assert ("x" * (MAX_INPUT_CHARS + 1)) not in calls[0]["prompt"]


def test_no_answer_when_no_letter_token():
    q = Question(id="q", type="noul", question="Run?")
    top = [{"token": " The", "logprob": -0.2}, {"token": " sure", "logprob": -0.4}]
    out = decide_questions(TEXT, [q], _completion(top))
    assert out[0]["result"] == "no_answer"
    assert out[0]["abstained"] and out[0]["chosen"] is None


def test_decision_shape_answered():
    q = Question(id="v", type="choice", question="Keep?",
                 options=("yes", "no"))
    out = decide_questions(TEXT, [q], _completion(_letters(-0.1, -2.0, -4.0)))
    d = out[0]
    assert d["result"] == "answered"
    assert d["chosen"] == "yes" and not d["abstained"]
    assert pytest.approx(d["prob"], abs=1e-6) == d["probabilities"]["yes"]
    assert set(d["probabilities"]) == {"yes", "no", "unknown"}


def test_shared_prefix_across_questions():
    calls = []
    qs = [
        Question(id="a", type="choice", question="Q one?", options=("x", "y")),
        Question(id="b", type="noul", question="Q two?"),
    ]
    decide_questions(TEXT, qs, _completion(_letters(-0.1, -1.0, -2.0), calls))
    assert len(calls) == 2
    # every question in a request shares one byte-identical prefix
    # (prompt-cache reuse) — the nonce is per-request, not per-question
    prefix = calls[0]["prompt"].split("Question:")[0]
    for c in calls:
        assert c["prompt"].startswith(prefix)
        assert c["n_predict"] == 1 and c["n_probs"] == N_PROBS


def test_nano_error_propagates_fail_closed():
    def boom(payload):
        raise ConnectionError("refused")
    q = Question(id="q", type="noul", question="Run?")
    with pytest.raises(DeciderUnavailable):
        decide_questions(TEXT, [q], boom)


def test_decision_log_jsonl_shape(tmp_path):
    rec = {"ts": 1.0, "request_id": "r", "decisions": [{"id": "q"}]}
    path = append_decision_log(tmp_path, rec)
    line = path.read_text().splitlines()
    assert len(line) == 1 and json.loads(line[0]) == rec
    assert path.name.startswith("decisions-") and path.parent == tmp_path.resolve()


# --- server surface -------------------------------------------------------

def _client(log_dir, top=None):
    top = top if top is not None else _letters(-0.1, -2.0, -3.0)
    return TestClient(create_app(call_completion=_completion(top), log_dir=log_dir))


def test_healthz(tmp_path):
    r = _client(tmp_path).get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_decide_endpoint_round_trip(tmp_path):
    r = _client(tmp_path).post("/decide", json={
        "text": TEXT,
        "questions": [
            {"id": "v", "type": "choice", "question": "Keep?",
             "options": ["yes", "no", "later"]},
            {"id": "t", "type": "noul", "question": "Transcribe?"},
        ],
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["decisions"]) == 2
    assert body["decisions"][0]["chosen"] == "yes"
    logged = list(tmp_path.glob("*.jsonl"))
    assert len(logged) == 1
    assert json.loads(logged[0].read_text())["request_id"] == body["request_id"]


def test_duplicate_question_ids_422(tmp_path):
    r = _client(tmp_path).post("/decide", json={
        "text": TEXT,
        "questions": [
            {"id": "v", "type": "noul", "question": "One?"},
            {"id": "v", "type": "noul", "question": "Two?"},
        ],
    })
    assert r.status_code == 422


def test_option_cap_422(tmp_path):
    r = _client(tmp_path).post("/decide", json={
        "text": TEXT,
        "questions": [{"id": "v", "type": "choice", "question": "Pick?",
                       "options": [f"o{i}" for i in range(26)]}],
    })
    assert r.status_code == 422


def test_score_type_behaves_like_choice(tmp_path):
    r = _client(tmp_path).post("/decide", json={
        "text": TEXT,
        "questions": [{"id": "s", "type": "score", "question": "Rate 1-5.",
                       "options": ["1", "2", "3", "4", "5"]}],
    })
    assert r.status_code == 200
    d = r.json()["decisions"][0]
    assert set(d["probabilities"]) == {"1", "2", "3", "4", "5", "unknown"}
