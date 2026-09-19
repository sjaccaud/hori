"""FastAPI surface for the typed-decision service (WO-01 / SLICE-18).

Thin adapter over services/aios_decide/decide.py — validation lives in
pydantic models, decision logic in decide.py, transport behind an
injected callable so tests never touch the network.

Binds loopback only (DECIDE_HOST defaults to 127.0.0.1); writes only
under DECIDE_LOG_DIR. Advisory — never inside the safety spine, never a
polling consumer (ai-power-manager idle-flap constraint: event-driven
calls only).

Traces to: .devin/prd/decide-service.md sections 2-3.
"""

from __future__ import annotations

import os
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .decide import (
    DEFAULT_MIN_PROB,
    MAX_ID_CHARS,
    MAX_INPUT_CHARS,
    MAX_OPTIONS,
    MAX_QUESTIONS,
    DeciderUnavailable,
    Question,
    append_decision_log,
    decide_questions,
    http_completion,
)

DEFAULT_LOG_DIR = "~/.local/share/hori/decisions"


class QuestionIn(BaseModel):
    id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    type: Literal["choice", "score", "noul"]
    question: str = Field(min_length=1, max_length=512)
    options: list[str] | None = None
    min_prob: float = Field(default=DEFAULT_MIN_PROB, ge=0.0, le=1.0)


class DecideRequest(BaseModel):
    text: str
    questions: list[QuestionIn] = Field(min_length=1, max_length=MAX_QUESTIONS)

    @field_validator("questions")
    @classmethod
    def _check_questions(cls, qs):
        ids = [q.id for q in qs]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate question id")
        for q in qs:
            if q.type == "noul":
                if q.options:
                    raise ValueError("noul has fixed options; supply none")
            else:
                if not q.options or len(q.options) < 2:
                    raise ValueError(f"{q.type} needs >=2 options")
                if len(q.options) > MAX_OPTIONS:
                    raise ValueError(f"too many options (>{MAX_OPTIONS})")
        return qs


def _to_question(q: QuestionIn) -> Question:
    return Question(
        id=q.id, type=q.type, question=q.question,
        options=tuple(q.options or ()), min_prob=q.min_prob)


def _nano_probe(nano_url: str) -> str:
    try:
        with urllib.request.urlopen(nano_url.rstrip("/") + "/health", timeout=2) as r:
            return "up" if r.status == 200 else "down"
    except Exception:
        return "down"


def create_app(call_completion=None, log_dir=None) -> FastAPI:
    nano_url = os.environ.get("DECIDE_NANO_URL", "http://127.0.0.1:8090")
    call_completion = call_completion or http_completion(nano_url)
    log_dir = Path(
        log_dir or os.environ.get("DECIDE_LOG_DIR", DEFAULT_LOG_DIR)
    ).expanduser()

    app = FastAPI(title="aios-decide", version="0.1.0")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "nano": _nano_probe(nano_url)}

    @app.post("/decide")
    def decide(req: DecideRequest):
        t0 = time.monotonic()
        questions = [_to_question(q) for q in req.questions]
        try:
            decisions = decide_questions(req.text, questions, call_completion)
        except DeciderUnavailable:
            return JSONResponse({"error": "decider_unavailable"}, status_code=503)
        record = {
            "ts": time.time(),
            "request_id": uuid.uuid4().hex,
            "model": "nano",
            "latency_ms": round((time.monotonic() - t0) * 1000, 2),
            "text": req.text,
            "questions": [q.model_dump() for q in req.questions],
            "decisions": decisions,
        }
        append_decision_log(log_dir, record)
        return {
            "request_id": record["request_id"],
            "model": "nano",
            "truncated": len(req.text) > MAX_INPUT_CHARS,
            "decisions": decisions,
        }

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "services.aios_decide.server:app",
        host=os.environ.get("DECIDE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DECIDE_PORT", "8773")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
