"""Offline calibration engine for the typed-decision service (WO-04 / SLICE-19).

Why it exists: the nano sidecar's probabilities are only trustworthy if
measured. This job samples logged /decide decisions — confident AND
borderline — through the 27B tier as audit oracle, labels agreement,
refits isotonic calibration, and proposes an abstain threshold against an
error budget. It produces artifacts a human reviews; nothing auto-tunes
the live service.

What it defends against: unlabeled data (oracle abstain/malformed ->
None, never a wrong label), nondeterminism (seeded sampling, stable
ordering), write escape (out_dir resolve-and-check, same pattern as
append_decision_log), and silent thin-data fits (MIN_LABELED gate).

Traces to: .devin/prd/decide-calibration.md (interface + safety
contract), core/state/proposed_work_orders/typed-decisions-04-*.json.
"""

from __future__ import annotations

import json
import random
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

BORDERLINE_BAND = 0.1      # prob within +/- BAND of min_prob is "borderline"
DEFAULT_MIN_PROB = 0.5
DEFAULT_ERROR_BUDGET = 0.15
MIN_LABELED = 20           # don't fit or tune on thinner oracle data
SCORE_TOLERANCE = 1        # oracle agreement rule for score questions
ECE_BUCKETS = 10


def load_records(log_dir: Path) -> list[dict]:
    """Read decisions-*.jsonl under log_dir; skip malformed lines."""
    root = Path(log_dir)
    records = []
    if not root.is_dir():
        return records
    for path in sorted(root.glob("decisions-*.jsonl")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def extract_samples(records: list[dict]) -> list[dict]:
    """One sample per answered decision with a real probability map.

    Abstained-but-answered rows are kept for stats but never oracle-
    sampled — a None chosen can't be labeled by the agreement rule.
    Question text/options are joined from record-level questions[].
    """
    samples = []
    for rec in records:
        text = rec.get("text", "")
        rid = rec.get("request_id", "")
        qdefs = {q.get("id"): q for q in rec.get("questions", [])
                 if isinstance(q, dict)}
        for d in rec.get("decisions", []):
            if d.get("result") != "answered":
                continue
            probs = d.get("probabilities")
            if not isinstance(probs, dict) or not probs:
                continue
            qdef = qdefs.get(d.get("id"), {})
            samples.append({
                "request_id": rid,
                "question_id": d.get("id"),
                "type": d.get("type"),
                "question": qdef.get("question"),
                "options": qdef.get("options"),
                "text": text,
                "chosen": d.get("chosen"),
                "prob": float(d.get("prob", 0.0)),
                "abstained": bool(d.get("abstained")),
                "probabilities": probs,
            })
    return samples


def select_for_oracle(samples: list[dict], n_borderline: int,
                      n_confident: int, seed: int,
                      min_prob: float = DEFAULT_MIN_PROB) -> list[dict]:
    """Deterministic stratified sample: borderline (prob within BAND of
    min_prob, non-abstained) + confident (everything else)."""
    lo, hi = min_prob - BORDERLINE_BAND, min_prob + BORDERLINE_BAND
    border = [s for s in samples if not s["abstained"]
              and lo <= s["prob"] <= hi]
    # abstained rows are unlabelable by the agreement rule — third class,
    # counted but never oracle-sampled
    conf = [s for s in samples if not s["abstained"] and s not in border]
    rng = random.Random(seed)
    border = sorted(border, key=lambda s: (s["request_id"], s["question_id"]))
    conf = sorted(conf, key=lambda s: (s["request_id"], s["question_id"]))
    rng.shuffle(border)
    rng.shuffle(conf)
    return border[:n_borderline] + conf[:n_confident]


def oracle_label(sample: dict, oracle_answer) -> bool | None:
    """Agreement rule. choice/noul -> exact label match; score -> |delta|
    <= SCORE_TOLERANCE. Oracle unknown/abstain/malformed -> None."""
    if oracle_answer is None:
        return None
    ans = str(oracle_answer).strip().lower()
    chosen = sample.get("chosen")
    if chosen is None or not ans or ans == "unknown":
        return None
    if sample.get("type") == "score":
        try:
            return abs(float(ans) - float(chosen)) <= SCORE_TOLERANCE
        except (TypeError, ValueError):
            return None
    if ans != str(chosen).strip().lower():
        # only a clean single-token answer counts as disagreement
        return None if len(ans.split()) != 1 else False
    return True


def isotonic_fit(xs: list[float], ys: list[float]
                 ) -> list[tuple[float, float]]:
    """PAVA isotonic regression on (x, y) pairs sorted by x. Returns
    (threshold, fitted) breakpoints, non-decreasing in fitted."""
    pairs = sorted(zip(xs, ys), key=lambda p: p[0])  # stable: keep y-order on x ties
    if not pairs:
        return []
    # pool equal x's first, then blocks of (weight, sum, x_low, x_high)
    blocks: list[list[float]] = []
    for x, y in pairs:
        if blocks and x == blocks[-1][2]:
            blocks[-1][0] += 1.0
            blocks[-1][1] += y
        else:
            blocks.append([1.0, y, x, x])
    merged: list[list[float]] = []
    for b in blocks:
        merged.append(b)
        while len(merged) >= 2 and merged[-2][1] / merged[-2][0] > \
                merged[-1][1] / merged[-1][0]:
            a, c = merged.pop(), merged.pop()
            merged.append([a[0] + c[0], a[1] + c[1], c[2], a[3]])
    return [(b[2], b[1] / b[0]) for b in merged]


def isotonic_predict(fit: list[tuple[float, float]], x: float) -> float:
    """Piecewise-constant predict; clamps to the fit's x-range."""
    if not fit:
        return 0.0
    val = fit[0][1]
    for threshold, v in fit:
        if x >= threshold:
            val = v
        else:
            break
    return val


def expected_calibration_error(labeled: list, buckets: int = ECE_BUCKETS
                               ) -> float:
    """ECE over oracle-labeled samples: sum_b (|B_b|/N)|acc_b - conf_b|.
    Unlabeled (None) samples are excluded."""
    rows = [(s["prob"], 1.0 if ok else 0.0)
            for s, ok in labeled if ok is not None]
    if not rows:
        return 0.0
    n = len(rows)
    ece = 0.0
    for i in range(buckets):
        lo, hi = i / buckets, (i + 1) / buckets
        cell = [(p, y) for p, y in rows if lo <= p < (hi if i < buckets - 1
                                                    else hi + 1e-9)]
        if not cell:
            continue
        acc = sum(y for _, y in cell) / len(cell)
        conf = sum(p for p, _ in cell) / len(cell)
        ece += (len(cell) / n) * abs(acc - conf)
    return ece


def suggest_min_prob(labeled: list, error_budget: float,
                     min_labeled: int = MIN_LABELED) -> float | None:
    """Smallest threshold t where empirical oracle-disagreement rate among
    samples with prob >= t is <= error_budget. None on thin data or when
    no threshold is feasible."""
    rows = [(s["prob"], ok) for s, ok in labeled if ok is not None]
    if len(rows) < min_labeled:
        return None
    best = None
    for t in sorted({p for p, _ in rows}):
        above = [ok for p, ok in rows if p >= t]
        if not above:
            continue
        err = 1.0 - sum(1.0 if ok else 0.0 for ok in above) / len(above)
        if err <= error_budget:
            best = t
            break
    return best


def _question_for_oracle(sample: dict) -> dict:
    opts = sample.get("options") or [k for k in sample["probabilities"]
                                   if k != "unknown"]
    return {"id": sample["question_id"], "type": sample["type"],
            "question": sample.get("question"), "options": opts}


def _write_report(day_dir: Path, stats: dict, labeled: list,
                  disagreements: list[dict]) -> None:
    lines = [
        "# decide calibration report",
        "",
        f"- decisions scanned: {stats['n_samples']}",
        f"- oracle-sampled: {stats['n_oracle']} "
        f"({stats['n_borderline']} borderline / {stats['n_confident']} confident)",
        f"- oracle-labeled: {stats['n_labeled']} "
        f"(unlabeled: {stats['n_unlabeled']})",
        f"- agreement rate: {stats['agreement']:.3f}",
        f"- ECE (10-bucket): {stats['ece']:.4f}",
        f"- suggested min_prob: {stats['suggested_min_prob']}",
        f"- error budget: {stats['error_budget']}",
        "",
        "## disagreements (nano chose X, oracle said Y)",
        "",
    ]
    for d in disagreements:
        lines.append(
            f"- `{d['request_id']}` q={d['question_id']} "
            f"nano={d['chosen']}@{d['prob']:.3f} oracle={d['oracle']} "
            f"type={d['type']}")
    if not disagreements:
        lines.append("- none")
    (day_dir / "report.md").write_text("\n".join(lines) + "\n")


def run(log_dir: Path, oracle, out_dir: Path, seed: int = 42,
        oracle_limit: int = 60, error_budget: float = DEFAULT_ERROR_BUDGET,
        dry_run: bool = False) -> dict:
    """End-to-end: load -> sample -> oracle -> label -> fit -> report.
    `oracle` is callable (text, question) -> answer label or None."""
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    records = load_records(Path(log_dir))
    samples = extract_samples(records)
    n_border = min(oracle_limit // 2, len(samples))
    n_conf = min(oracle_limit - n_border, len(samples))
    selected = select_for_oracle(samples, n_border, n_conf, seed)
    n_border_sel = sum(
        1 for s in selected
        if abs(s["prob"] - DEFAULT_MIN_PROB) <= BORDERLINE_BAND
        and not s["abstained"])

    labeled = []
    if not dry_run:
        for s in selected:
            ans = oracle(s["text"], _question_for_oracle(s))
            labeled.append((s, oracle_label(s, ans),
                            None if ans is None else str(ans).strip()))
    disagreements = [
        {**{k: s[k] for k in ("request_id", "question_id", "type",
                              "chosen", "prob")}, "oracle": ans}
        for s, ok, ans in labeled if ok is False]
    labeled_pairs = [(s, ok) for s, ok, _ in labeled]
    n_labeled = sum(1 for _, ok, _ in labeled if ok is not None)
    n_correct = sum(1 for _, ok, _ in labeled if ok is True)
    fit = isotonic_fit([s["prob"] for s, ok in labeled_pairs
                        if ok is not None],
                       [1.0 if ok else 0.0 for _, ok in labeled_pairs
                        if ok is not None])
    suggested = None if dry_run else suggest_min_prob(
        labeled_pairs, error_budget)

    stats = {
        "n_samples": len(samples), "n_oracle": len(selected),
        "n_borderline": n_border_sel,
        "n_confident": len(selected) - n_border_sel,
        "n_labeled": n_labeled,
        "n_unlabeled": len(labeled) - n_labeled,
        "agreement": (n_correct / n_labeled) if n_labeled else 0.0,
        "n_abstained": sum(1 for s in samples if s["abstained"]),
        "ece": expected_calibration_error(labeled_pairs),
        "suggested_min_prob": suggested, "error_budget": error_budget,
    }

    if dry_run:
        return stats

    day_dir = (out / date.today().isoformat()).resolve()
    if day_dir.parent != out:
        raise ValueError("report path escapes out_dir")
    day_dir.mkdir(parents=True, exist_ok=True)
    _write_report(day_dir, stats, labeled_pairs, disagreements)
    with (day_dir / "exemplars.jsonl").open("w") as f:
        for s, ok, ans in labeled:
            f.write(json.dumps({"request_id": s["request_id"],
                                "question_id": s["question_id"],
                                "type": s["type"], "chosen": s["chosen"],
                                "prob": s["prob"], "oracle": ans,
                                "correct": ok,
                                "text": s["text"][:4000]}) + "\n")
    (day_dir / "params.json").write_text(json.dumps({
        "suggested_min_prob": suggested,
        "isotonic_fit": [[t, v] for t, v in fit],
        "ece": stats["ece"], "agreement": stats["agreement"],
        "n_labeled": n_labeled, "seed": seed}, indent=1) + "\n")
    return stats


def http_oracle(base_url: str, timeout: float = 120.0):
    """Real oracle: re-ask the same typed question of the 27B router
    (OpenAI-compatible /v1/chat/completions), reply = verdict label."""
    url = base_url.rstrip("/") + "/v1/chat/completions"

    def call(text: str, question: dict):
        opts = question.get("options") or ["yes", "no"]
        listing = "\n".join(f"- {o}" for o in opts + ["unknown"])
        qtext = question.get("question") or question.get("id", "verdict")
        prompt = (f"TEXT:\n\"\"\"\n{text[:6000]}\n\"\"\"\n\n"
                  f"Question: {qtext}\n"
                  f"Choose exactly one option label:\n{listing}\n"
                  f"Reply with ONLY the option label.")
        body = {"model": "qwen-quality",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0, "max_tokens": 8}
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read())
            return resp["choices"][0]["message"]["content"]
        except Exception:
            return None  # fail-closed: unlabeled, never a wrong label

    return call


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--log-dir", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--oracle-url", default="http://127.0.0.1:8080")
    p.add_argument("--oracle-limit", type=int, default=60)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--error-budget", type=float,
                   default=DEFAULT_ERROR_BUDGET)
    p.add_argument("--dry-run", action="store_true",
                   help="sampling + stats only; no oracle calls")
    args = p.parse_args(argv)

    oracle = (lambda t, q: None) if args.dry_run else http_oracle(
        args.oracle_url)
    stats = run(args.log_dir, oracle, args.out_dir, seed=args.seed,
                oracle_limit=args.oracle_limit,
                error_budget=args.error_budget, dry_run=args.dry_run)
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
