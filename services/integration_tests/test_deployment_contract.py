"""Integration test: deployment contract.

Asserts the *assembled* system matches declared intent — the layer unit
tests can't see. Each check in deployment_contract.py becomes a pytest
param so a failure names the specific broken wiring (dead timer, drifted
valves, dead MCP lane, stale snapshot) rather than a generic red.

Defends: the "built but never wired" failure class — e.g. the kb-sync
that was tested-but-never-invoked, and the mcpo boot race that silently
removed all hori-state tools for ~30h (bridge retired 2026-10-09 —
the native lane now carries the check).
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "services/open-webui/deployment_contract.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("deployment_contract", CONTRACT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def test_builtin_tools_check_flags_drift(monkeypatch):
    """Deliberate builtinTools drift must fail and name the model —
    this is the 'OWUI upgrade re-bloats the grammar' tripwire."""
    mod = _load_module()
    canon = mod._canon_constants()
    armed = canon["WEB_SEARCH_MODELS"]

    def meta_for(mid, **overrides):
        m = dict(canon["META_BUILTINTOOLS"])
        m["web_search"] = mid in armed
        m.update(overrides)
        return json.dumps({"builtinTools": m})

    rows = [
        ("qwen-fast", meta_for("qwen-fast"), 1),
        ("qwen-think", meta_for("qwen-think", tasks=True, surprise_cat=True), 1),
    ]
    monkeypatch.setattr(mod, "_owui_sql", lambda q: rows)
    ok, detail = mod.check_builtin_tools()
    assert not ok
    assert "qwen-think" in detail
    assert "qwen-fast" not in detail

    monkeypatch.setattr(
        mod, "_owui_sql", lambda q: [(m, meta_for(m), 1) for m in ("a", "b")]
    )
    ok, _ = mod.check_builtin_tools()
    assert ok


def test_builtin_tools_nano_expects_all_false(monkeypatch):
    mod = _load_module()
    canon = mod._canon_constants()
    all_false = {k: False for k in canon["META_BUILTINTOOLS"]}
    monkeypatch.setattr(
        mod,
        "_owui_sql",
        lambda q: [("nano", json.dumps({"builtinTools": all_false}), 1)],
    )
    ok, _ = mod.check_builtin_tools()
    assert ok
    leaky = dict(all_false, time=True)
    monkeypatch.setattr(
        mod,
        "_owui_sql",
        lambda q: [("nano", json.dumps({"builtinTools": leaky}), 1)],
    )
    ok, detail = mod.check_builtin_tools()
    assert not ok and "nano" in detail
