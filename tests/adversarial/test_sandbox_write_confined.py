"""Adversarial test: sandbox_write confinement.

sandbox_write is a band-2 write primitive on :8774 — the one tool that
mutates the disposable workspace without a command allowlist in front
of it. Its ONLY boundary is path confinement under SCRATCH
(~/agent-scratch). These tests assert that boundary is mechanical:

  - ../ traversal rejected outright (same posture as _validate)
  - absolute paths outside the root rejected
  - /sbx/-prefixed paths normalize under the root (the in-guest view)
  - symlink escapes fail closed — resolve() follows links, so a link
    pointing outside the root makes the resolved path leave SCRATCH
  - oversized and non-string content rejected before any disk touch
  - every call audited (writes must be accountable like execs)

Defends: SLICE-22 write primitive; same disposable-root trust zone as
sandbox_exec. The scratch root is disposable — host files outside it
must be unreachable through this tool.

Status: RED first (Pillar VII) — written before tool_sandbox_write
exists.
"""
import os
import sys

import pytest

_SBX_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "open-webui"
)
sys.path.insert(0, os.path.abspath(_SBX_DIR))
import sandbox_tools_server as sbx  # noqa: E402


@pytest.fixture()
def scratch(tmp_path, monkeypatch):
    """Point SCRATCH at an isolated dir + capture audit entries."""
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(sbx, "SCRATCH", root.resolve())
    seen = []
    monkeypatch.setattr(
        sbx, "_audit",
        lambda a, r, tool="sandbox_exec": seen.append((tool, a, r)),
    )
    return root.resolve(), seen


def w(path, content="x", **kw):
    args = {"path": path, "content": content}
    args.update(kw)
    return sbx.tool_sandbox_write(args)


# --- accepted writes ------------------------------------------------------

def test_relative_path_writes_under_root(scratch):
    root, _ = scratch
    r = w("a/b/c.py", "print(1)\n")
    assert r["ok"], r
    assert (root / "a/b/c.py").read_text() == "print(1)\n"


def test_sbx_prefix_normalizes_to_root(scratch):
    root, _ = scratch
    r = w("/sbx/foo.py", "y\n")
    assert r["ok"], r
    assert (root / "foo.py").read_text() == "y\n"


def test_append_mode_appends(scratch):
    root, _ = scratch
    w("log.txt", "one\n")
    r = w("log.txt", "two\n", append=True)
    assert r["ok"], r
    assert (root / "log.txt").read_text() == "one\ntwo\n"


def test_write_audited(scratch):
    _, seen = scratch
    w("ok.txt", "x")
    writes = [s for s in seen if s[0] == "sandbox_write"]
    assert writes, "sandbox_write must be audited"
    _, args, result = writes[0]
    assert result.get("ok")


# --- traversal ------------------------------------------------------------

def test_dotdot_rejected(scratch):
    root, _ = scratch
    r = w("../escape.txt", "pwned")
    assert not r["ok"]
    assert not (root.parent / "escape.txt").exists()


def test_deep_dotdot_rejected(scratch):
    r = w("../../../../etc/passwd", "x")
    assert not r["ok"]


def test_interior_dotdot_rejected(scratch):
    # foo/../bar stays inside after resolve, but _validate's posture is
    # reject-any-.. — consistent policy, no normalization games.
    r = w("foo/../bar.txt", "x")
    assert not r["ok"]


def test_absolute_outside_root_rejected(scratch):
    for p in ("/etc/cron.d/pwn", "/tmp/sbx-escape.txt", "/home/x/.ssh/k"):
        r = w(p, "x")
        assert not r["ok"], p


def test_sbx_root_itself_rejected(scratch):
    # /sbx is the root directory, not a file — writing it must fail,
    # not truncate the workspace.
    r = w("/sbx", "x")
    assert not r["ok"]


# --- symlink escape --------------------------------------------------------

def test_symlink_escape_rejected(scratch, tmp_path):
    root, _ = scratch
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "evil").symlink_to(outside)
    r = w("evil/pwn.txt", "escaped")
    assert not r["ok"], "write through escaping symlink must fail closed"
    assert not (outside / "pwn.txt").exists()


def test_in_sandbox_symlink_ok(scratch):
    root, _ = scratch
    (root / "real").mkdir()
    (root / "link").symlink_to(root / "real")
    r = w("link/inside.txt", "x")
    assert r["ok"], r
    assert (root / "real/inside.txt").read_text() == "x"


# --- input validation ------------------------------------------------------

def test_oversize_content_rejected(scratch):
    big = "x" * (sbx.MAX_WRITE_BYTES + 1)
    r = w("big.bin", big)
    assert not r["ok"]
    assert "large" in r["error"] or "max" in r["error"]


def test_non_string_content_rejected(scratch):
    for bad in (123, {"a": 1}, None, [1, 2], b"bytes"):
        r = w("x.txt", bad)
        assert not r["ok"], repr(bad)


def test_missing_or_empty_path_rejected(scratch):
    for p in (None, "", "   "):
        r = sbx.tool_sandbox_write({"path": p, "content": "x"})
        assert not r["ok"], repr(p)


def test_directory_target_fails_clean(scratch):
    root, _ = scratch
    (root / "adir").mkdir()
    r = w("adir", "x")
    assert not r["ok"]
    assert "error" in r


def test_rejected_write_audited(scratch):
    _, seen = scratch
    w("../escape.txt", "x")
    writes = [s for s in seen if s[0] == "sandbox_write"]
    assert writes, "rejected writes must be audited too"
    assert not writes[0][2].get("ok")
