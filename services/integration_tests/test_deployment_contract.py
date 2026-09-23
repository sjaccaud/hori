"""Integration test: deployment contract.

Asserts the *assembled* system matches declared intent — the layer unit
tests can't see. Each check in deployment_contract.py becomes a pytest
param so a failure names the specific broken wiring (dead timer, drifted
valves, empty mcpo spec, stale snapshot) rather than a generic red.

Defends: the "built but never wired" failure class — e.g. the kb-sync
that was tested-but-never-invoked, and the mcpo boot race that silently
removed all hori-state tools for ~30h.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "services/open-webui/deployment_contract.py"
)


def _run_contract() -> list[dict]:
    p = subprocess.run(
        [sys.executable, str(CONTRACT), "--json"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert p.returncode in (0, 1), f"contract crashed: {p.stderr[-400:]}"
    return json.loads(p.stdout)


@pytest.fixture(scope="module")
def contract_results():
    return _run_contract()


def test_contract_ran_all_checks(contract_results):
    assert len(contract_results) >= 7, contract_results


def test_all_checks_pass(contract_results):
    failures = [
        f"{r['name']}: {r['detail']}" for r in contract_results if not r["ok"]
    ]
    assert not failures, "deployment contract failures:\n" + "\n".join(failures)
