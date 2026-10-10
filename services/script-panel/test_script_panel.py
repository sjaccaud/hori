"""Guard-action endpoint tests (Phase 4.5 approve-from-phone, 2026-10-09).

The panel is now tailnet-reachable, so its mutation surface needs the same
honesty as the exec layer: guard actions must be strict-shaped, act only on
LIVE guard units (idempotent — a stale notification can't revert the world),
and dispatch promote/revert to the scaffold's own mechanisms.
"""
import os
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import script_panel as sp  # noqa: E402


def _fake_run_probe(responses):
    """Build a _run_probe replacement driven by substring matching."""
    calls = []

    def fake(argv, timeout=8):
        calls.append(list(map(str, argv)))
        cmd = " ".join(map(str, argv))
        for pat, out in responses.items():
            if pat in cmd:
                return NS(stdout=out[0], stderr=out[1], returncode=out[2])
        return NS(stdout="", stderr="", returncode=1)

    return fake, calls


def test_rejects_malformed_gid():
    for bad in ["", "g1", "not-a-gid", "g1-2; rm -rf /", "g1-2 && x",
                "../g1-2", "g1-2`id`", "g-1", "g1-2\nreboot"]:
        r = sp.guard_action(bad, "promote")
        assert not r["ok"], f"accepted bad gid: {bad!r}"


def test_rejects_unknown_action():
    r = sp.guard_action("g1791594023-385504", "frobnicate")
    assert not r["ok"]


def test_refuses_stale_gid(monkeypatch):
    """A notification for an already-settled gid must not touch anything."""
    fake, calls = _fake_run_probe({})
    monkeypatch.setattr(sp, "_run_probe", fake)
    r = sp.guard_action("g111-222", "promote")
    assert not r["ok"]
    assert not any("agent-promote" in " ".join(c) for c in calls)
    assert not any("start" in " ".join(c) for c in calls)


def test_promote_pending_probation(monkeypatch):
    fake, calls = _fake_run_probe({
        "is-active agent-guard-g111-222-prob.timer": ("active", "", 0),
        "agent-promote.sh": ("promoted: g111-222", "", 0),
    })
    monkeypatch.setattr(sp, "_run_probe", fake)
    r = sp.guard_action("g111-222", "promote")
    assert r["ok"], r
    assert any("agent-promote.sh" in " ".join(c) for c in calls)


def test_promote_refused_when_not_in_probation(monkeypatch):
    # Armed TTL guard but NOT probation — promote makes no sense.
    fake, calls = _fake_run_probe({
        "is-active agent-guard-g111-222.timer": ("active", "", 0),
    })
    monkeypatch.setattr(sp, "_run_probe", fake)
    r = sp.guard_action("g111-222", "promote")
    assert not r["ok"]
    assert not any("agent-promote" in " ".join(c) for c in calls)


def test_revert_pending_fires_prob_service(monkeypatch):
    fake, calls = _fake_run_probe({
        "is-active agent-guard-g111-222-prob.timer": ("active", "", 0),
        "systemctl start agent-guard-g111-222-prob.service": ("", "", 0),
    })
    monkeypatch.setattr(sp, "_run_probe", fake)
    r = sp.guard_action("g111-222", "revert")
    assert r["ok"], r
    assert any("agent-guard-g111-222-prob.service" in " ".join(c) for c in calls)


def test_revert_armed_ttl_fires_plain_service(monkeypatch):
    fake, calls = _fake_run_probe({
        "is-active agent-guard-g111-222.timer": ("active", "", 0),
        "systemctl start agent-guard-g111-222.service": ("", "", 0),
    })
    monkeypatch.setattr(sp, "_run_probe", fake)
    r = sp.guard_action("g111-222", "revert")
    assert r["ok"], r
    assert any("agent-guard-g111-222.service" in " ".join(c) for c in calls)
    assert not any("-prob.service" in " ".join(c) for c in calls)


def test_promote_failure_propagates(monkeypatch):
    fake, _ = _fake_run_probe({
        "is-active agent-guard-g111-222-prob.timer": ("active", "", 0),
        "agent-promote.sh": ("", "no armed probation timer", 1),
    })
    monkeypatch.setattr(sp, "_run_probe", fake)
    r = sp.guard_action("g111-222", "promote")
    assert not r["ok"]


# --- judge_agreement: verdict x human-outcome correlation -------------


def _prop(pid, status, judge=None, guard_id=None, origin=None):
    p = {"id": pid, "status": status}
    if judge:
        p["judge"] = {"status": judge}
    if guard_id:
        p["guard_id"] = guard_id
    if origin:
        p["origin"] = origin
    return p


def test_judge_agreement_empty():
    a = sp.judge_agreement([], {})
    assert a["judged"] == 0 and a["settled"] == 0
    assert a["agreement"] is None


def test_judge_agreement_cells():
    props = [
        _prop("p-1", "probation", "clean", "g1"),     # +promoted: agreed
        _prop("p-2", "rejected", "flagged"),           # denied: agreed
        _prop("p-3", "probation", "clean", "g3"),      # reverted: MISSED
        _prop("p-4", "probation", "flagged", "g4"),    # promoted: wolf
        _prop("p-5", "pending", "clean"),              # unsettled
        _prop("p-6", "retracted", "clean"),            # model-side, not human
        _prop("p-7", "rejected", "abstain"),           # no-signal verdict
        _prop("p-8", "rejected"),                      # never judged
        _prop("p-9", "probation", "clean", "g9"),      # timer live: unsettled
    ]
    states = {"g1": "promoted", "g3": "expired/reverted",
              "g4": "promoted", "g9": "pending"}
    a = sp.judge_agreement(props, states)
    assert a["judged"] == 7      # clean/flagged verdicts (p1-6, p9)
    assert a["settled"] == 4     # p1, p2, p3, p4
    assert a["agreed"] == 2      # p1 clean-promoted, p2 flagged-rejected
    assert a["missed"] == 1      # p3 clean but reverted
    assert a["cried_wolf"] == 1  # p4 flagged but promoted
    assert a["agreement"] == 50
    by_id = {c["id"]: c["verdict"] for c in a["cells"]}
    assert by_id == {"p-1": "agreed", "p-2": "agreed",
                     "p-3": "missed", "p-4": "cried_wolf"}


def test_judge_agreement_all_unsettled():
    props = [_prop("p-1", "pending", "clean"),
             _prop("p-2", "probation", "flagged", "g2")]
    a = sp.judge_agreement(props, {"g2": "pending"})
    assert a["judged"] == 2 and a["settled"] == 0
    assert a["agreement"] is None


def test_judge_agreement_skips_synthetic():
    # eval fixtures carry judge verdicts but aren't ground truth — a
    # user dismissing one to clear the board isn't a human judgment on
    # the change's merit, so it must not pollute the calibration cells.
    props = [
        _prop("p-1", "rejected", "flagged", origin="eval"),
        _prop("p-2", "probation", "clean", "g2", origin="eval"),
        _prop("p-3", "rejected", "flagged"),              # real — counts
    ]
    a = sp.judge_agreement(props, {"g2": "promoted"})
    assert a["judged"] == 1 and a["settled"] == 1
    assert a["agreed"] == 1 and a["agreement"] == 100
