"""Adversarial test: sandbox_edit confinement + match discipline.

sandbox_edit is the band-2 edit primitive on :8774 — exact-match
search/replace on files inside the disposable scratch root. Its ONLY
boundary is the shared path confinement from SLICE-22 plus the
match-discipline rules that keep small models from mangling files:

  - same confinement as sandbox_write: .. rejected, absolute paths
    outside the root rejected, symlink escapes fail closed
  - `old` must match the file content EXACTLY (no regex, no fuzz)
  - zero matches and ambiguous matches both REFUSE — a small model
    must not get a silent first-match edit on the wrong location;
    ambiguity requires an explicit expect_count
  - missing file refuses (edit, not create — sandbox_write's job)
  - every call audited, accepts and rejects alike

Defends: SLICE-23 edit primitive; same disposable-root trust zone as
sandbox_exec/sandbox_write. A wrong edit inside the sandbox is
recoverable — a wrong path outside it is not, so path confinement is
the hard line.

Status: RED first (Pillar VII) — written before tool_sandbox_edit
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
    (root / "f.py").write_text("def f():\n    return 1\n")
    (root / "dup.txt").write_text("aa bb aa\n")
    monkeypatch.setattr(sbx, "SCRATCH", root.resolve())
    seen = []
    monkeypatch.setattr(
        sbx, "_audit",
        lambda a, r, tool="sandbox_exec": seen.append((tool, a, r)),
    )
    return root.resolve(), seen


def e(path, old, new, **kw):
    args = {"path": path, "old": old, "new": new}
    args.update(kw)
    return sbx.tool_sandbox_edit(args)


# --- accepted edits ---------------------------------------------------------

def test_exact_match_replaces(scratch):
    root, _ = scratch
    r = e("f.py", "return 1", "return 2")
    assert r["ok"], r
    assert "return 2" in (root / "f.py").read_text()


def test_sbx_prefix_and_relative(scratch):
    root, _ = scratch
    r = e("/sbx/f.py", "def f", "def g")
    assert r["ok"], r
    assert (root / "f.py").read_text().startswith("def g")


def test_ambiguous_match_with_expect_count(scratch):
    root, _ = scratch
    r = e("dup.txt", "aa", "cc", expect_count=2)
    assert r["ok"], r
    assert (root / "dup.txt").read_text() == "cc bb cc\n"


def test_edit_audited(scratch):
    _, seen = scratch
    e("f.py", "return 1", "return 9")
    edits = [s for s in seen if s[0] == "sandbox_edit"]
    assert edits and edits[0][2].get("ok")


# --- match discipline --------------------------------------------------------

def test_zero_match_refuses(scratch):
    root, _ = scratch
    r = e("f.py", "return 99", "return 3")
    assert not r["ok"]
    assert (root / "f.py").read_text() == "def f():\n    return 1\n"


def test_ambiguous_match_refuses_without_count(scratch):
    root, _ = scratch
    r = e("dup.txt", "aa", "cc")
    assert not r["ok"], "ambiguous match must refuse, not silently pick one"
    assert "match" in r["error"].lower()
    assert (root / "dup.txt").read_text() == "aa bb aa\n"


def test_wrong_expect_count_refuses(scratch):
    r = e("dup.txt", "aa", "cc", expect_count=3)
    assert not r["ok"]


def test_missing_file_refuses(scratch):
    r = e("nope.py", "x", "y")
    assert not r["ok"]


def test_empty_old_refuses(scratch):
    # "" matches everywhere — degenerate edit.
    r = e("f.py", "", "x")
    assert not r["ok"]


# --- confinement (same boundary as sandbox_write) ----------------------------

def test_dotdot_rejected(scratch):
    r = e("../outside.txt", "a", "b")
    assert not r["ok"]


def test_absolute_outside_root_rejected(scratch):
    r = e("/etc/hostname", "a", "b")
    assert not r["ok"]


def test_symlink_escape_rejected(scratch, tmp_path):
    root, _ = scratch
    outside = tmp_path / "victim.txt"
    outside.write_text("safe\n")
    (root / "link.txt").symlink_to(outside)
    r = e("link.txt", "safe", "pwned")
    assert not r["ok"]
    assert outside.read_text() == "safe\n"


def test_missing_and_bad_types_refuse(scratch):
    for bad_args in (
        {"path": "f.py"},                      # no old/new
        {"path": "f.py", "old": "a"},          # no new
        {"path": "f.py", "old": 1, "new": "x"},
        {"path": None, "old": "a", "new": "b"},
    ):
        r = sbx.tool_sandbox_edit(bad_args)
        assert not r["ok"], bad_args


def test_rejected_edit_audited(scratch):
    _, seen = scratch
    e("../nope.txt", "a", "b")
    edits = [s for s in seen if s[0] == "sandbox_edit"]
    assert edits and not edits[0][2].get("ok")
