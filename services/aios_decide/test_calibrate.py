"""Unit tests for the decide calibration engine (WO-04 / SLICE-19).

Contract source: .devin/prd/decide-calibration.md sections 3-5. The oracle
is injected — tests never touch a model or the network.
"""

import json
from pathlib import Path

import pytest

from services.aios_decide.calibrate import (
    MIN_LABELED,
    expected_calibration_error,
    extract_samples,
    isotonic_fit,
    isotonic_predict,
    load_records,
    oracle_label,
    run,
    select_for_oracle,
    suggest_min_prob,
)


def _record(decisions, text="subject text", rid="r1"):
    return {"request_id": rid, "model": "nano", "text": text,
            "decisions": decisions}


def _decision(qid="verdict", qtype="choice", chosen="keep", prob=0.9,
              probs=None, abstained=False, result="answered"):
    return {"id": qid, "type": qtype, "result": result, "chosen": chosen,
            "prob": prob, "abstained": abstained,
            "probabilities": probs or {"keep": prob, "drop": 1 - prob,
                                       "unknown": 0.0}}


def _sample(**kw):
    base = {"request_id": "r1", "question_id": "verdict", "type": "choice",
            "chosen": "keep", "prob": 0.9, "abstained": False,
            "text": "subject text",
            "probabilities": {"keep": 0.9, "drop": 0.1, "unknown": 0.0}}
    base.update(kw)
    return base


# ---------- load_records ----------

def test_load_records_skips_malformed(tmp_path):
    good = _record([_decision()])
    (tmp_path / "decisions-20260101.jsonl").write_text(
        json.dumps(good) + "\n{bad json\n" + json.dumps(good) + "\n")
    (tmp_path / "other.txt").write_text(json.dumps(good) + "\n")
    recs = load_records(tmp_path)
    assert len(recs) == 2


def test_load_records_empty_dir(tmp_path):
    assert load_records(tmp_path) == []


# ---------- extract_samples ----------

def test_extract_samples_answered_only():
    recs = [_record([
        _decision(qid="a", result="answered"),
        _decision(qid="b", result="no_answer", probs={}),
        _decision(qid="c", result="insufficient_context", probs={}),
    ])]
    samples = extract_samples(recs)
    assert [s["question_id"] for s in samples] == ["a"]


def test_extract_samples_keeps_abstained_answered():
    # abstained=True but probabilities exist -> still a measurable sample
    recs = [_record([_decision(abstained=True, chosen=None, prob=0.4)])]
    samples = extract_samples(recs)
    assert len(samples) == 1 and samples[0]["abstained"] is True


def test_extract_samples_joins_question_text():
    # question text/options live at record level (questions[]), not on the
    # decision — the oracle prompt needs them (live bug 2026-10-03)
    rec = _record([_decision(qid="fit", qtype="score")])
    rec["questions"] = [{"id": "fit", "type": "score",
                         "question": "How well does this fit the owner?",
                         "options": ["1", "2", "3", "4", "5"]}]
    s = extract_samples([rec])[0]
    assert s["question"] == "How well does this fit the owner?"
    assert s["options"] == ["1", "2", "3", "4", "5"]


# ---------- select_for_oracle ----------

def test_select_for_oracle_deterministic_and_bounded():
    samples = [_sample(request_id=f"r{i}", prob=p)
               for i, p in enumerate([0.45, 0.5, 0.55, 0.9, 0.95, 0.3, 0.8])]
    a = select_for_oracle(samples, n_borderline=2, n_confident=2, seed=7)
    b = select_for_oracle(samples, n_borderline=2, n_confident=2, seed=7)
    assert a == b
    assert len(a) <= 4


def test_select_for_oracle_borderline_band():
    # min_prob default 0.5, band 0.1 -> borderline prob in [0.4, 0.6]
    samples = [_sample(request_id="edge1", prob=0.5),
               _sample(request_id="edge2", prob=0.42),
               _sample(request_id="far", prob=0.99)]
    sel = select_for_oracle(samples, n_borderline=5, n_confident=1, seed=1)
    ids = {s["request_id"] for s in sel}
    assert "far" in ids
    border = [s for s in sel if s["request_id"] != "far"]
    assert all(0.4 <= s["prob"] <= 0.6 for s in border)


# ---------- oracle_label ----------

def test_oracle_label_choice_exact():
    s = _sample(type="choice", chosen="keep")
    assert oracle_label(s, "keep") is True
    assert oracle_label(s, "drop") is False


def test_oracle_label_score_tolerance():
    s = _sample(type="score", chosen="3")
    assert oracle_label(s, "3") is True
    assert oracle_label(s, "4") is True   # |3-4| <= 1
    assert oracle_label(s, "5") is False  # |3-5| > 1


def test_oracle_label_unlabeled():
    s = _sample(chosen="keep")
    for bad in ("unknown", None, "", "KEEP with extra words"):
        assert oracle_label(s, bad) is None, bad


# ---------- isotonic ----------

def test_isotonic_monotone():
    xs = [0.9, 0.1, 0.5, 0.7, 0.3]
    ys = [0.0, 1.0, 0.0, 1.0, 0.0]  # deliberately non-monotone truth
    fit = isotonic_fit(xs, ys)
    vals = [v for _, v in fit]
    assert vals == sorted(vals)
    # predictions interpolate monotone
    assert isotonic_predict(fit, 0.2) <= isotonic_predict(fit, 0.8)


def test_isotonic_degenerate():
    assert isotonic_fit([], []) == []
    fit = isotonic_fit([0.5, 0.5, 0.5], [0.0, 1.0, 0.0])
    assert isotonic_predict(fit, 0.5) == pytest.approx(1 / 3)
    assert isotonic_predict(fit, 0.0) == pytest.approx(1 / 3)  # clamp


# ---------- ECE ----------

def test_ece_perfect_calibration():
    # 10 samples at conf 1.0 all correct -> ECE 0
    labeled = [(_sample(prob=1.0), True) for _ in range(10)]
    assert expected_calibration_error(labeled, buckets=5) == pytest.approx(0.0)


def test_ece_counts_unlabeled_out():
    labeled = [(_sample(prob=1.0), True), (_sample(prob=0.5), None)]
    assert expected_calibration_error(labeled, buckets=5) == pytest.approx(0.0)


# ---------- suggest_min_prob ----------

def _labeled(prob, correct):
    return (_sample(prob=prob), correct)


def test_suggest_min_prob_finds_smallest_feasible():
    # error below 0.6 is 100%, error >= 0.6 is 0% -> any budget <=1 gives
    # smallest feasible threshold ~0.6
    labeled = [_labeled(0.4, False), _labeled(0.5, False),
               _labeled(0.6, True), _labeled(0.7, True),
               _labeled(0.8, True), _labeled(0.9, True),
               _labeled(0.45, False), _labeled(0.65, True)]
    t = suggest_min_prob(labeled, error_budget=0.15, min_labeled=5)
    assert t == pytest.approx(0.6)


def test_suggest_min_prob_thin_data():
    labeled = [_labeled(0.9, True)]
    assert suggest_min_prob(labeled, error_budget=0.15) is None
    assert MIN_LABELED > 1


def test_suggest_min_prob_no_feasible():
    # everything wrong -> no threshold meets budget
    labeled = [_labeled(p, False) for p in
               (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.55, 0.65, 0.75, 0.85)]
    assert suggest_min_prob(labeled, error_budget=0.15,
                            min_labeled=5) is None


# ---------- run end-to-end ----------

def _fake_oracle(text, question):
    # oracle agrees with "keep", disagrees otherwise; abstains on "abstainme"
    if "abstainme" in text:
        return None
    return "keep"


def test_run_writes_artifacts(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    recs = [_record([_decision(chosen="keep", prob=0.9)], rid=f"r{i}")
            for i in range(30)]
    recs += [_record([_decision(chosen="drop", prob=0.5,
                                probs={"keep": 0.3, "drop": 0.5,
                                       "unknown": 0.2})], rid=f"b{i}")
             for i in range(30)]
    (log_dir / "decisions-20260101.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in recs))
    out = tmp_path / "out"
    report = run(log_dir, _fake_oracle, out, seed=1, oracle_limit=20)
    day_dir = next(out.iterdir())
    names = {p.name for p in day_dir.iterdir()}
    assert {"report.md", "exemplars.jsonl", "params.json"} <= names
    rep = (day_dir / "report.md").read_text()
    assert "ECE" in rep and "agreement" in rep.lower()
    params = json.loads((day_dir / "params.json").read_text())
    assert "suggested_min_prob" in params


def test_run_dry_run_no_oracle_calls(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    recs = [_record([_decision()], rid=f"r{i}") for i in range(10)]
    (log_dir / "decisions-20260101.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in recs))
    calls = []
    def oracle(t, q):
        calls.append(1)
        return "keep"
    run(log_dir, oracle, tmp_path / "out", seed=1, dry_run=True)
    assert calls == []


def test_run_confines_writes(tmp_path):
    # '..' resolves to a legitimate path; artifacts land under the RESOLVED
    # out_dir, never outside it
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "decisions-20260101.jsonl").write_text(
        json.dumps(_record([_decision()])) + "\n")
    resolved = (tmp_path / "out" / ".." / "escape").resolve()
    run(log_dir, _fake_oracle, tmp_path / "out" / ".." / "escape")
    for p in resolved.rglob("*"):
        assert resolved in p.resolve().parents or p == resolved
    assert not (tmp_path / "out").exists()  # nothing written to the alias
