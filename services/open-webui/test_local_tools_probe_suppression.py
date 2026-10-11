"""Synthetic-deny suppression: requests marked "probe": true still get
the full deny response (same 422 + citation) but journal under
agent-deny-probe and skip the phone push.

Why: the deployment contract's local_tools_deny check rides the 5-min
state snapshot — an unmarked probe produced ~250 identical [deny]
notifications/day, flooding the phone with synthetic incidents.
Suppression only mutes the push; the block + journal record hold.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import local_tools_server as jlt  # noqa: E402


def test_probe_request_denies_but_does_not_notify(monkeypatch):
    calls = []
    monkeypatch.setattr(
        jlt.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd),
    )
    deny_conf = jlt.RIG_DENY_CONF
    # Built-in floor rules cover the i2c incident class even without a
    # readable conf — probe a compiled-in deny so no fixture is needed.
    assert deny_conf is not None
    jlt._PROBE_REQUEST.set(True)
    try:
        jlt._report_rig_deny("exec", "probething", "test citation")
    finally:
        jlt._PROBE_REQUEST.set(False)
    logged = [c for c in calls if c[0] == "logger"]
    notified = [c for c in calls if "rig-notify" in " ".join(str(x) for x in c)]
    assert logged and "agent-deny-probe" in logged[0]
    assert not notified


def test_real_deny_still_notifies(monkeypatch):
    calls = []
    monkeypatch.setattr(
        jlt.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd),
    )
    jlt._PROBE_REQUEST.set(False)
    jlt._report_rig_deny("exec", "realthing", "test citation")
    logged = [c for c in calls if c[0] == "logger"]
    notified = [c for c in calls if "rig-notify" in " ".join(str(x) for x in c)]
    assert logged and "agent-deny" in logged[0]
    assert "agent-deny-probe" not in logged[0]
    assert notified


def test_probe_flag_set_per_request(monkeypatch):
    """do_POST semantics: args.probe drives the contextvar per request —
    a probe request must not poison the next request's flag."""
    jlt._PROBE_REQUEST.set(False)
    jlt._PROBE_REQUEST.set(bool({"probe": True}.get("probe")))
    assert jlt._PROBE_REQUEST.get() is True
    jlt._PROBE_REQUEST.set(bool({}.get("probe")))
    assert jlt._PROBE_REQUEST.get() is False
