"""Adversarial tests for proposal_tools_server — the Band-3 boundary.

The model-facing contract: proposals stage content for HUMAN approval;
the envelope (allowlist, protected set, verify enum, deny overlay) is
what makes that safe. Every test below is an escape attempt or a
degenerate input the boundary must reject honestly.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proposal_tools_server as pts  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated proposal state + a fake allowlist root with a script in it."""
    root = tmp_path / "scripts"
    root.mkdir()
    target = root / "helper.sh"
    target.write_text("#!/bin/bash\necho old\n")
    target.chmod(0o755)
    state = tmp_path / "state" / "proposals"
    monkeypatch.setattr(pts, "PROPOSAL_ROOTS", [root])
    monkeypatch.setattr(pts, "STATE_DIR", state)
    monkeypatch.setattr(pts, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(pts, "RIG_NOTIFY", "/bin/true")
    # decide is a real service on this host — disabled by default so the
    # suite never touches the network; judge tests re-enable + stub it.
    monkeypatch.setattr(pts, "DECIDE_URL", "")
    return {"root": root, "target": target, "state": state,
            "tmp": tmp_path}


def _propose(target, content="#!/bin/bash\necho new\n",
             rationale="fix the thing", verify="syntax", **kw):
    return pts.tool_propose_host_change(
        {"path": str(target), "content": content, "rationale": rationale,
         "verify": verify, **kw})


class TestHappyPath:
    def test_valid_proposal_stages_with_diff(self, env):
        r = _propose(env["target"])
        assert r["ok"] is True
        assert r["status"] == "pending"
        assert r["adds"] == 1 and r["dels"] == 1
        pid = r["id"]
        pdir = env["state"] / pid
        meta = json.loads((pdir / "meta.json").read_text())
        assert meta["path"] == str(env["target"])
        assert meta["verify"] == "syntax"
        assert meta["status"] == "pending"
        assert "+echo new" in (pdir / "diff.patch").read_text()
        # the host file is UNTOUCHED — staging is not applying
        assert env["target"].read_text() == "#!/bin/bash\necho old\n"

    def test_propose_denied_does_not_stage(self, env):
        outside = env["tmp"] / "elsewhere.sh"
        outside.write_text("x")
        before = list(env["tmp"].rglob("meta.json"))
        _propose(outside)
        assert list(env["tmp"].rglob("meta.json")) == before

    def test_list_and_status_roundtrip(self, env):
        r = _propose(env["target"])
        pid = r["id"]
        st = pts.tool_proposal_status({"id": pid})
        assert st["ok"] and st["proposal"]["status"] == "pending"
        lst = pts.tool_list_proposals({"status": "pending"})
        assert lst["count"] == 1 and lst["proposals"][0]["id"] == pid
        lst_all = pts.tool_list_proposals({})
        assert lst_all["count"] == 1

    def test_retract_pending(self, env):
        pid = _propose(env["target"])["id"]
        r = pts.tool_retract_proposal({"id": pid})
        assert r["ok"] and r["status"] == "retracted"
        st = pts.tool_proposal_status({"id": pid})
        assert st["proposal"]["status"] == "retracted"
        assert pts.tool_list_proposals({"status": "pending"})["count"] == 0

    def test_retract_settled_fails(self, env):
        pid = _propose(env["target"])["id"]
        pts.tool_retract_proposal({"id": pid})
        r = pts.tool_retract_proposal({"id": pid})
        assert r["ok"] is False


class TestPathBoundary:
    def test_outside_root_denied(self, env):
        outside = env["tmp"] / "outside.sh"
        outside.write_text("x")
        r = _propose(outside)
        assert r["ok"] is False and "outside proposal roots" in r["error"]

    def test_dotdot_escape_denied(self, env):
        r = _propose(str(env["root"] / ".." / "escape.sh"))
        assert r["ok"] is False

    @pytest.mark.parametrize("name", [
        "rig-notify.sh", "guarded-change.sh", "agent-approve.sh",
        "gpu-lane.sh", "deny.conf", "invariants.conf",
        "deployment_contract.py", "safe-config-edit.sh",
    ])
    def test_protected_scaffold_denied(self, env, name):
        f = env["root"] / name
        f.write_text("old")
        r = _propose(f)
        assert r["ok"] is False and "protected" in r["error"].lower()

    def test_nonexistent_target_denied(self, env):
        r = _propose(env["root"] / "new-file.sh")
        assert r["ok"] is False and "does not exist" in r["error"]

    def test_directory_target_denied(self, env):
        d = env["root"] / "subdir"
        d.mkdir()
        r = _propose(d)
        assert r["ok"] is False

    def test_symlink_outside_root_denied(self, env):
        real = env["tmp"] / "real-target.sh"
        real.write_text("secret host file")
        link = env["root"] / "linked.sh"
        link.symlink_to(real)
        r = _propose(link)
        assert r["ok"] is False  # resolves outside the root

    def test_deny_conf_path_hit(self, env):
        f = env["root"] / "passwd-check.sh"
        f.write_text("x")
        r = _propose(f)
        assert r["ok"] is False and r.get("denied") is True


class TestContentBoundary:
    def test_deny_conf_in_content(self, env):
        r = _propose(env["target"], content="#!/bin/bash\nddcutil getvcp 1\n")
        assert r["ok"] is False and r.get("denied") is True

    def test_identical_content_rejected(self, env):
        r = _propose(env["target"], content="#!/bin/bash\necho old\n")
        assert r["ok"] is False and "identical" in r["error"]

    def test_empty_content_rejected(self, env):
        assert _propose(env["target"], content="")["ok"] is False
        assert _propose(env["target"], content="   \n")["ok"] is False

    def test_oversize_content_rejected(self, env):
        r = _propose(env["target"], content="x" * (pts.MAX_CONTENT + 1))
        assert r["ok"] is False and "too large" in r["error"]

    def test_missing_rationale_rejected(self, env):
        assert _propose(env["target"], rationale="")["ok"] is False


class TestVerifyEnum:
    def test_freeform_verify_rejected(self, env):
        r = _propose(env["target"], verify="bash -c 'rm -rf /'")
        assert r["ok"] is False and "bounded enum" in r["error"]

    def test_unknown_verify_rejected(self, env):
        r = _propose(env["target"], verify="make me a sandwich")
        assert r["ok"] is False

    @pytest.mark.parametrize("v", ["health", "syntax", "pycompile"])
    def test_enum_verifies_accepted(self, env, v):
        # vary content so each proposal isn't "identical"
        r = _propose(env["target"], content=f"# {v}\n", verify=v)
        assert r["ok"] is True and r["verify"] == v


class TestBounds:
    def test_pending_cap(self, env, monkeypatch):
        monkeypatch.setattr(pts, "MAX_PENDING", 2)
        for i in range(2):
            t = env["root"] / f"s{i}.sh"
            t.write_text(f"old{i}")
            assert _propose(t)["ok"] is True
        t3 = env["root"] / "s3.sh"
        t3.write_text("old3")
        r = _propose(t3)
        assert r["ok"] is False and "pending" in r["error"]

    def test_bad_id_rejected(self, env):
        assert pts.tool_proposal_status({"id": "../etc"})["ok"] is False
        assert pts.tool_proposal_status({"id": "p-1"})["ok"] is False
        assert pts.tool_retract_proposal({"id": "x"})["ok"] is False


def _decide_stub(answers, request_id="req-1"):
    """_decide_post double. answers = {qid: (chosen, abstained)}."""
    def post(payload):
        assert "DIFF:" in payload["text"]
        assert "RATIONALE:" in payload["text"]
        assert len(payload["questions"]) == 3
        return {"request_id": request_id, "decisions": [
            {"id": qid, "type": "noul", "result": "answered",
             "probabilities": {chosen: 0.9},
             "chosen": chosen, "prob": 0.9, "abstained": ab}
            for qid, (chosen, ab) in answers.items()]}
    return post


def _judge_on(monkeypatch, stub):
    monkeypatch.setattr(pts, "DECIDE_URL", "http://fake-decide")
    monkeypatch.setattr(pts, "_decide_post", stub)


class TestJudge:
    """Second-opinion sidecar: advisory verdicts ride the staged meta."""

    def test_clean_verdict_in_response_and_meta(self, env, monkeypatch):
        _judge_on(monkeypatch, _decide_stub({
            "fit": ("yes", False), "scope": ("no", False),
            "risk": ("low", False)}))
        r = _propose(env["target"])
        assert r["ok"] is True and r["judge"]["status"] == "clean"
        meta = json.loads(
            (env["state"] / r["id"] / "meta.json").read_text())
        assert meta["judge"]["status"] == "clean"
        assert meta["judge"]["answers"]["fit"]["chosen"] == "yes"
        assert meta["judge"]["request_id"] == "req-1"

    @pytest.mark.parametrize("answers,flag", [
        ({"fit": ("no", False), "scope": ("no", False),
          "risk": ("low", False)}, "fit=no"),
        ({"fit": ("yes", False), "scope": ("yes", False),
          "risk": ("low", False)}, "scope=yes"),
        ({"fit": ("yes", False), "scope": ("no", False),
          "risk": ("high", False)}, "risk=high"),
    ])
    def test_flagged_verdicts(self, env, monkeypatch, answers, flag):
        _judge_on(monkeypatch, _decide_stub(answers))
        r = _propose(env["target"])
        assert r["ok"] is True  # advisory — staging is never gated
        assert r["judge"]["status"] == "flagged"
        assert flag in r["judge"]["flagged"]

    @pytest.mark.parametrize("answers", [
        {"fit": ("yes", False), "scope": ("no", False),
         "risk": ("medium", False)},          # can't call it low-risk
        {"fit": ("yes", False), "scope": ("no", False),
         "risk": (None, True)},               # model abstained
        {"fit": (None, True), "scope": ("no", False),
         "risk": ("low", False)},
    ])
    def test_abstain_verdicts(self, env, monkeypatch, answers):
        _judge_on(monkeypatch, _decide_stub(answers))
        r = _propose(env["target"])
        assert r["judge"]["status"] == "abstain"
        assert r["judge"]["abstained"]

    def test_unavailable_does_not_block_stage(self, env, monkeypatch):
        def boom(payload):
            raise OSError("connection refused")
        _judge_on(monkeypatch, boom)
        r = _propose(env["target"])
        assert r["ok"] is True
        assert r["judge"]["status"] == "unavailable"
        assert "connection refused" in r["judge"]["error"]
        assert (env["state"] / r["id"] / "meta.json").exists()

    def test_disabled_without_url(self, env):
        r = _propose(env["target"])  # env fixture sets DECIDE_URL=""
        assert r["ok"] is True and r["judge"]["status"] == "disabled"

    def test_verdict_surfaces_in_list_and_status(self, env, monkeypatch):
        _judge_on(monkeypatch, _decide_stub({
            "fit": ("yes", False), "scope": ("yes", False),
            "risk": ("low", False)}))
        pid = _propose(env["target"])["id"]
        st = pts.tool_proposal_status({"id": pid})
        assert st["proposal"]["judge"]["status"] == "flagged"
        lst = pts.tool_list_proposals({"status": "pending"})
        assert lst["proposals"][0]["judge"] == "flagged"


class TestOrigin:
    """origin marks synthetic proposals (eval fixtures): they stage and
    journal identically but never page the phone — a measurement
    artifact is not a human decision."""

    def _notified(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            pts, "_notify", lambda tag, msg, url="": calls.append(tag))
        return calls

    def test_eval_origin_suppresses_push(self, env, monkeypatch):
        calls = self._notified(monkeypatch)
        r = _propose(env["target"], origin="eval")
        assert r["ok"] is True and r["origin"] == "eval"
        assert calls == []                     # phone stays quiet
        meta = json.loads(
            (env["state"] / r["id"] / "meta.json").read_text())
        assert meta["origin"] == "eval"        # but the audit is full

    def test_eval_origin_still_journals(self, env, monkeypatch):
        self._notified(monkeypatch)
        pid = _propose(env["target"], origin="eval")["id"]
        journal = (env["state"] / "journal.log").read_text()
        assert f"propose\t{pid}" in journal    # staging is still logged

    def test_absent_origin_pushes_normally(self, env, monkeypatch):
        calls = self._notified(monkeypatch)
        r = _propose(env["target"])
        assert r["ok"] is True and "origin" not in r
        assert calls == [f"propose-{r['id']}"]

    def test_unknown_origin_pushes(self, env, monkeypatch):
        # only the eval marker is synthetic — a future scheduler origin
        # that wants human attention just isn't in the set.
        calls = self._notified(monkeypatch)
        r = _propose(env["target"], origin="scheduler")
        assert calls == [f"propose-{r['id']}"]

    def test_origin_roundtrips_list_and_status(self, env, monkeypatch):
        self._notified(monkeypatch)
        pid = _propose(env["target"], origin="eval")["id"]
        st = pts.tool_proposal_status({"id": pid})
        assert st["proposal"]["origin"] == "eval"
        lst = pts.tool_list_proposals({"status": "pending"})
        assert lst["proposals"][0]["origin"] == "eval"

    def test_eval_origin_still_judged(self, env, monkeypatch):
        self._notified(monkeypatch)
        _judge_on(monkeypatch, _decide_stub({
            "fit": ("yes", False), "scope": ("no", False),
            "risk": ("low", False)}))
        r = _propose(env["target"], origin="eval")
        assert r["judge"]["status"] == "clean"  # measurement unaffected
