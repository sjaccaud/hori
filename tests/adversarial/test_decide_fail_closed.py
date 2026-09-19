"""Adversarial test: the /decide service fails closed and stays confined.

The typed-decision service is an advisory S1 primitive: it asks nano
(:8090) for a single-token letter distribution and writes an append-only
JSONL decision log. Two ways it can be abused or lie:

1. A caller can reach it while nano is down or returning garbage. The
   service must return 503 and NEVER fabricate a probability
   distribution — a synthesized answer is worse than no answer, because
   consumers (WO-03) will treat output as ground truth.
2. Request fields must never reach the filesystem. The decision log
   lives only under the configured decisions dir; crafted ids or extra
   path fields must not redirect, escape, or corrupt it.

This test defines the TDD contract: all of those must hold before the
service exists, per Pillar VII.

Defends: the integrity of ~/.local/share/hori/decisions/ as calibration
ground truth, and the fail-closed contract between /decide and its
future consumers.

Traces to: .devin/prd/decide-service.md section 3.
"""

import json

import pytest

VALID_REQUEST = {
    "text": "Idea: a CLI tool that converts markdown notes to slides.",
    "questions": [
        {
            "id": "verdict",
            "type": "choice",
            "question": "Is this idea worth building?",
            "options": ["yes", "no", "later"],
        }
    ],
}


def _client(call_completion, log_dir):
    from fastapi.testclient import TestClient
    from services.aios_decide.server import create_app

    return TestClient(create_app(call_completion=call_completion, log_dir=log_dir))


def _ok_completion(payload):
    """Fake nano reply: letter A wins."""
    return {
        "completion_probabilities": [
            {
                "token": " A",
                "logprob": -0.1,
                "top_logprobs": [
                    {"token": " A", "logprob": -0.1},
                    {"token": " B", "logprob": -2.0},
                    {"token": " C", "logprob": -3.0},
                ],
            }
        ]
    }


def test_nano_down_returns_503_never_fabricates(tmp_path):
    """Nano unreachable -> 503, and not a single fabricated probability."""

    def boom(payload):
        raise ConnectionError("connection refused")

    client = _client(boom, tmp_path)
    r = client.post("/decide", json=VALID_REQUEST)
    assert r.status_code == 503
    body = r.json()
    assert "probabilities" not in json.dumps(body)


def test_malformed_nano_reply_returns_503(tmp_path):
    """A nano reply without completion_probabilities -> 503, never a guess."""

    def garbage(payload):
        return {"content": "dunno lol"}

    client = _client(garbage, tmp_path)
    r = client.post("/decide", json=VALID_REQUEST)
    assert r.status_code == 503


def test_log_writes_confined_to_decisions_dir(tmp_path):
    """Crafted request fields must not redirect or escape the log dir."""
    log_dir = tmp_path / "decisions"
    client = _client(_ok_completion, log_dir)
    evil = tmp_path / "evil"
    req = dict(VALID_REQUEST)
    req["request_id"] = "../evil"
    req["log_dir"] = str(evil)
    req["log_path"] = str(evil / "x.jsonl")
    r = client.post("/decide", json=req)
    assert r.status_code == 200
    assert not evil.exists()
    written = list(log_dir.glob("*.jsonl"))
    assert len(written) == 1
    for line in written[0].read_text().splitlines():
        json.loads(line)  # well-formed JSONL, no corruption


def test_unknown_question_type_rejected(tmp_path):
    """Types outside choice|score|noul are refused, not improvised on."""
    req = {
        "text": "Some text to judge.",
        "questions": [
            {"id": "q1", "type": "oracle", "question": "?", "options": ["a", "b"]}
        ],
    }
    client = _client(_ok_completion, tmp_path)
    assert client.post("/decide", json=req).status_code == 422


def test_request_cannot_set_log_path(tmp_path):
    """No request-supplied key may rename the log file the server chose."""
    log_dir = tmp_path / "decisions"
    client = _client(_ok_completion, log_dir)
    r = client.post(
        "/decide",
        json={
            "text": "Some text to judge.",
            "filename": "../../tmp/pwned.jsonl",
            "questions": [
                {
                    "id": "q1",
                    "type": "noul",
                    "question": "Should this run?",
                }
            ],
        },
    )
    assert r.status_code == 200
    assert not (tmp_path / "pwned.jsonl").exists()
    assert not __import__("pathlib").Path("/tmp/pwned.jsonl").exists()
