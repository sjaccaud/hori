#!/usr/bin/env python3
"""Thread consolidation CLI — the workstream half of Sleep & Dream.

Nightly job (aios-thread-consolidation.timer): refreshes mechanical
state.md fields from episode dates, flags threads that had sessions but
no episodes, and emits LLM-assisted state/rule proposals into the review
queue (~/.local/share/hori/review/). Proposals are always pending; a
human applies or rejects them.

LLM stages are skipped (fail-open, noted in the report) when the router
is unreachable or the power manager reports game mode — this job must
never contend with an active game session.

Traces to: .devin/prd/thread-consolidation.md, docs/roadmap.md Tier 1B.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.aios_core import thread_consolidation as tc  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("thread_consolidation")

LLM_API_URL = os.getenv(
    "LLM_API_URL", "http://localhost:8080/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-fast")
POWER_STATE = Path("/run/ai-power-manager/state")
REPORT_DIR = Path.home() / ".local/share/hori/consolidation"


def in_game_mode() -> bool:
    try:
        return POWER_STATE.read_text().strip() == "game"
    except OSError:
        return False


def call_llm(system: str, user: str) -> str:
    resp = requests.post(
        LLM_API_URL,
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 1024,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def write_report(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).date().isoformat()
    path = REPORT_DIR / f"{date}.md"
    lines = [
        "---",
        f'date: "{datetime.now(timezone.utc).isoformat(timespec="seconds")}"',
        f'llm_available: {json.dumps(report["llm_available"])}',
        "---",
        "",
        f"# Thread consolidation — {date}",
        "",
        f"- Threads scanned: {report['threads']}",
        f"- Mechanical updates: {json.dumps(report['mechanical'])}",
        f"- Stale threads: {json.dumps(report['stale'])}",
        f"- Proposals written: {json.dumps(report['proposals'])}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    llm = None
    if in_game_mode():
        log.info("game mode active — skipping LLM stages")
    else:
        try:
            requests.get(LLM_API_URL.rsplit("/chat/completions", 1)[0]
                         + "/models", timeout=5).raise_for_status()
            llm = call_llm
        except Exception as exc:
            log.warning("LLM unreachable (%s) — mechanical only", exc)

    report = tc.run_consolidation(
        tc.THREADS_ROOT, tc.REVIEW_ROOT, call_llm=llm)
    path = write_report(report)
    log.info("consolidation done: %s", json.dumps(report))
    log.info("report: %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
