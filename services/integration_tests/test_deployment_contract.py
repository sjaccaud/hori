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
        timeout=300,
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


# --- agent_smoke: the faithful-execution canary -------------------------
# Asserts the assembled daily-driver path (router + real :8766 tools +
# enforcement) produces a faithful answer, not just that endpoints live.
# Seams _agent_chat/_agent_tool/_agent_truth are monkeypatched.


def _call_msg(name, args, i=0):
    return {
        "role": "assistant",
        "tool_calls": [
            {"id": f"c{i}", "function": {"name": name, "arguments": args}}
        ],
    }


def _seq_chat(*msgs):
    it = iter(msgs)

    def chat(model, messages):
        return {"choices": [{"message": next(it)}]}

    return chat


def _loop_chat(msg):
    return lambda model, messages: {"choices": [{"message": msg}]}


def test_agent_smoke_faithful_pass(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_agent_truth", lambda: "testhost")
    monkeypatch.setattr(
        mod,
        "_agent_chat",
        _seq_chat(
            _call_msg("read_file", '{"path": "/etc/hostname"}'),
            {"role": "assistant", "content": "testhost"},
        ),
    )
    monkeypatch.setattr(
        mod, "_agent_tool", lambda n, a: {"ok": True, "content": "testhost\n"}
    )
    ok, detail = mod.check_agent_smoke()
    assert ok and "attempt 1" in detail


def test_agent_smoke_confabulation_fails(monkeypatch):
    """Tool result says testhost, model answers ghost — on BOTH attempts.
    That's the confabulation tripwire, not a model-mood miss."""
    mod = _load_module()
    monkeypatch.setattr(mod, "_agent_truth", lambda: "testhost")
    monkeypatch.setattr(
        mod,
        "_agent_chat",
        _seq_chat(
            _call_msg("read_file", '{"path": "/etc/hostname"}'),
            {"role": "assistant", "content": "ghostbox"},
            _call_msg("read_file", '{"path": "/etc/hostname"}'),
            {"role": "assistant", "content": "ghostbox"},
        ),
    )
    monkeypatch.setattr(
        mod, "_agent_tool", lambda n, a: {"ok": True, "content": "testhost\n"}
    )
    ok, detail = mod.check_agent_smoke()
    assert not ok and "faithful" in detail


def test_agent_smoke_iteration_cap_fails(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_agent_truth", lambda: "testhost")
    monkeypatch.setattr(
        mod,
        "_agent_chat",
        _loop_chat(_call_msg("read_file", '{"path": "/etc/hostname"}')),
    )
    monkeypatch.setattr(
        mod, "_agent_tool", lambda n, a: {"ok": True, "content": "testhost\n"}
    )
    ok, detail = mod.check_agent_smoke()
    assert not ok and "converge" in detail


def test_agent_smoke_envelope_contact_fails(monkeypatch):
    """A rig deny.conf hit on the benign canary task is the hard
    boundary — fail fast, not a discipline note."""
    mod = _load_module()
    monkeypatch.setattr(mod, "_agent_truth", lambda: "testhost")
    monkeypatch.setattr(
        mod,
        "_agent_chat",
        _loop_chat(_call_msg("read_file", '{"path": "/etc/hostname"}')),
    )
    monkeypatch.setattr(
        mod,
        "_agent_tool",
        lambda n, a: {
            "ok": False,
            "error": "blocked by rig deny.conf: test rule",
        },
    )
    ok, detail = mod.check_agent_smoke()
    assert not ok and "envelope" in detail


def test_agent_smoke_policy_touch_recovers(monkeypatch):
    """An allowlist deny is fed back and the model adapts — production
    semantics. PASS with the touch reported, not gated."""
    mod = _load_module()
    monkeypatch.setattr(mod, "_agent_truth", lambda: "testhost")
    monkeypatch.setattr(
        mod,
        "_agent_chat",
        _seq_chat(
            _call_msg("run_safe_command", '{"command": "hostname"}'),
            _call_msg("read_file", '{"path": "/etc/hostname"}'),
            {"role": "assistant", "content": "testhost"},
        ),
    )
    results = iter(
        [
            {"ok": False, "error": "command not in allowlist: hostname"},
            {"ok": True, "content": "testhost\n"},
        ]
    )
    monkeypatch.setattr(mod, "_agent_tool", lambda n, a: next(results))
    ok, detail = mod.check_agent_smoke()
    assert ok and "policy touches" in detail


def test_agent_smoke_unknown_tool_fails(monkeypatch):
    """Calling a tool it was never offered is the misfire-attractor
    failure class — fail even if the final answer is right."""
    mod = _load_module()
    monkeypatch.setattr(mod, "_agent_truth", lambda: "testhost")
    monkeypatch.setattr(
        mod,
        "_agent_chat",
        _loop_chat(_call_msg("execute_code", '{"code": "x"}')),
    )
    ok, detail = mod.check_agent_smoke()
    assert not ok and "unoffered" in detail


def test_agent_smoke_empty_answer_fails(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_agent_truth", lambda: "testhost")
    monkeypatch.setattr(
        mod, "_agent_chat", _loop_chat({"role": "assistant", "content": ""})
    )
    ok, detail = mod.check_agent_smoke()
    assert not ok and "empty" in detail


def test_agent_smoke_second_attempt_recovers(monkeypatch):
    """One stochastic miss must not page: attempt 2 passing is a PASS."""
    mod = _load_module()
    monkeypatch.setattr(mod, "_agent_truth", lambda: "testhost")
    monkeypatch.setattr(
        mod,
        "_agent_chat",
        _seq_chat(
            {"role": "assistant", "content": "ghostbox"},
            _call_msg("read_file", '{"path": "/etc/hostname"}'),
            {"role": "assistant", "content": "testhost"},
        ),
    )
    monkeypatch.setattr(
        mod, "_agent_tool", lambda n, a: {"ok": True, "content": "testhost\n"}
    )
    ok, detail = mod.check_agent_smoke()
    assert ok and "attempt 2" in detail


def test_fast_run_skips_agent_smoke():
    """The snapshot's 5-min --oneline cadence must not pay a model call —
    agent_smoke drops out of run(fast=True)."""
    mod = _load_module()
    called = []
    for i, (name, fn) in enumerate(mod.CHECKS):
        mod.CHECKS[i] = (
            name,
            (lambda n: (lambda: (called.append(n) or (True, "ok"))))(name),
        )
    results = mod.run(fast=True)
    assert "agent_smoke" not in called
    assert "agent_smoke" not in {r["name"] for r in results}
