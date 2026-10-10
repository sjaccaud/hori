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
