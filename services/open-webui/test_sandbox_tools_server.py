"""sandbox_exec — allowlist, confinement, and deny-overlay tests.

Two layers: _validate (pure checks — allowlist, git filter, metachars,
path escapes, deny.conf content) and the real bwrap exec (scratch is
writable, host fs is hidden). The bwrap layer is skipped when bwrap is
absent so the suite still runs on dev boxes without it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import sandbox_tools_server as sbx  # noqa: E402


@pytest.fixture(autouse=True)
def reports(monkeypatch):
    """Record rig-deny reports instead of really logging/notifying."""
    seen = []
    monkeypatch.setattr(sbx, "_report_rig_deny",
                        lambda s, sub, r: seen.append((s, sub, r)))
    return seen


def v(cmd):
    return sbx._validate(cmd)


# --- allowlist -----------------------------------------------------------

def test_allowed_core_commands():
    for c in ("ls", "cat", "grep", "find", "python3", "pytest", "pip",
              "make", "mkdir", "rm", "git"):
        base_ok, _ = v([c, "x"]) if c != "git" else v(["git", "status"])
        assert base_ok or "allowlist" not in _, f"{c} should be allowed"


def test_disallowed_commands():
    for c in ("curl", "wget", "ssh", "rsync", "nc", "node", "docker"):
        ok, err = v([c, "x"])
        assert not ok and "allowlist" in err, c


def test_allowlist_bypass_launchers_forbidden():
    # env/nohup/xargs would run ANY binary; awk has system().
    for c in ("env", "nohup", "xargs", "awk", "gawk"):
        ok, err = v([c, "ls"])
        assert not ok, c


def test_shell_interpreters_forbidden():
    for c in ("bash", "sh", "zsh", "sudo", "bwrap", "unshare", "nsenter"):
        ok, err = v([c])
        assert not ok and "forbidden" in err, c


def test_find_exec_flag_denied():
    for flag in ("-exec", "-execdir", "-ok", "-okdir"):
        ok, err = v(["find", ".", flag, "rm", "{}", ";"])
        assert not ok and "launches" in err, flag
    # In-sandbox-only flags stay allowed.
    ok, _ = v(["find", ".", "-name", "*.py", "-delete"])
    assert ok


# --- git filter ------------------------------------------------------------

def test_git_allowed_subcommands():
    for sub in ("status", "log", "diff", "show", "checkout", "add",
                "commit", "branch", "init"):
        ok, _ = v(["git", sub, "x"])
        assert ok or "subcommand" not in _, sub


def test_git_push_denied():
    ok, err = v(["git", "push", "origin", "main"])
    assert not ok and "subcommand" in err


def test_git_remote_denied():
    ok, err = v(["git", "remote", "add", "x", "https://github.com/a/b"])
    assert not ok and "subcommand" in err


def test_git_fetch_pull_config_denied():
    # fetch/pull could reach a crafted local repo's arbitrary remote;
    # config rewrites remote.origin.url — both bypass the clone filter.
    for sub in ("fetch", "pull", "config", "submodule", "send-email"):
        ok, err = v(["git", sub])
        assert not ok and "subcommand" in err, sub


def test_git_clone_host_allowlist():
    ok, _ = v(["git", "clone", "https://github.com/octocat/Hello-World"])
    assert ok
    ok, _ = v(["git", "clone", "file:///home/testuser/some-repo"])
    assert ok
    for url in ("https://evil.example.com/r", "ssh://github.com/a/b",
                "git@github.com:a/b", "https://gist.github.com/x"):
        ok, err = v(["git", "clone", url])
        assert not ok and "clone host" in err, url


def test_git_upload_pack_escape_denied():
    ok, err = v(["git", "clone", "--upload-pack=/tmp/x",
                 "https://github.com/a/b"])
    assert not ok


def test_git_global_flag_denied():
    ok, err = v(["git", "--exec-path=/tmp/x", "status"])
    assert not ok


def test_git_dash_C_allowed_and_bounded():
    # Canonical work-into-repo pattern — there is no cd inside.
    ok, _ = v(["git", "-C", "eval-repo", "status"])
    assert ok
    ok, _ = v(["git", "-C", "/sbx/eval-repo", "diff"])
    assert ok
    ok, err = v(["git", "-C", "../outside", "status"])
    assert not ok and "escapes" in err
    ok, err = v(["git", "-C", "/etc", "status"])
    assert not ok and "outside sandbox" in err
    # -C is chdir; -c is config injection — case matters (denied either
    # as a global flag or a forbidden arg depending on position).
    ok, err = v(["git", "-c", "core.x=y", "status"])
    assert not ok
    ok, err = v(["git", "status", "-c", "core.x=y"])
    assert not ok and "forbidden" in err


# --- arg guards -----------------------------------------------------------

def test_shell_metachars_are_inert_args():
    # No shell exists inside the sandbox, so |;&()` are literal args —
    # grep/find/python legitimately need them. They validate, and simply
    # don't do anything shell-like when executed.
    for arg in ("a|b", "a;b", "a&&b", "$(x)", "`x`", "a>b"):
        ok, _ = v(["grep", arg, "f.txt"])
        assert ok, arg


def test_path_escape_rejected():
    # ../etc/passwd would trip a real deny.conf credential rule first —
    # use neutral paths so the escape check itself is what fires.
    ok, err = v(["cat", "../outside-file.txt"])
    assert not ok and "escapes" in err
    ok, err = v(["mkdir", "../escape"])
    assert not ok and "escapes" in err


def test_absolute_path_policy():
    # File-inspection commands may take absolute paths (they still can't
    # read them inside the namespace, but the error is honest).
    ok, _ = v(["cat", "/etc/hostname"])
    assert ok
    # Writers may not — an absolute path to rm would be meaningless or
    # dangerous depending on the bind layout.
    ok, err = v(["rm", "/etc/passwd"])
    assert not ok


# --- deny.conf overlay ------------------------------------------------------

def test_deny_conf_overlay(monkeypatch, tmp_path, reports):
    conf = tmp_path / "deny.conf"
    conf.write_text("exec|test-minted: pytest eats kittens|\\bpytest\\b\n")
    monkeypatch.setattr(sbx, "RIG_DENY_CONF", conf)
    ok, err = v(["pytest", "tests/"])
    assert not ok and "deny.conf" in err and "test-minted" in err
    assert reports and reports[0][0] == "exec"


def test_deny_conf_nonmatching_rule_leaves_allowlist(monkeypatch, tmp_path):
    conf = tmp_path / "deny.conf"
    conf.write_text("exec|x|ZZZ_NEVER_MATCHES_123\n")
    monkeypatch.setattr(sbx, "RIG_DENY_CONF", conf)
    ok, _ = v(["ls"])
    assert ok


# --- real bwrap confinement --------------------------------------------------

needs_bwrap = pytest.mark.skipif(
    not os.path.exists(sbx.BWRAP), reason="bwrap not installed"
)


@needs_bwrap
def test_sandbox_exec_writes_inside_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sbx, "SCRATCH", tmp_path)
    r = sbx.tool_sandbox_exec({"command": "mkdir", "args": ["proj"]})
    assert r["ok"], r
    r = sbx.tool_sandbox_exec(
        {"command": "python3",
         "args": ["-c", "open('proj/x.txt','w').write('hi')"]})
    assert r["ok"], r
    assert (tmp_path / "proj" / "x.txt").read_text() == "hi"


@needs_bwrap
def test_sandbox_exec_host_fs_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(sbx, "SCRATCH", tmp_path)
    # /etc doesn't exist inside the namespace at all.
    r = sbx.tool_sandbox_exec({"command": "cat", "args": ["/etc/hostname"]})
    assert not r["ok"]
    # ~ is the sandbox — no host home visible.
    r = sbx.tool_sandbox_exec(
        {"command": "python3",
         "args": ["-c", "import os;print(os.listdir(os.path.expanduser('~')))"]})
    assert r["ok"] and "/home/" not in r["stdout"]


@needs_bwrap
def test_sandbox_exec_write_outside_impossible(tmp_path, monkeypatch):
    """A payload trying to write outside the root hits a nonexistent or
    read-only fs — confinement is structural, not policy."""
    monkeypatch.setattr(sbx, "SCRATCH", tmp_path)
    home = os.path.expanduser("~")
    marker = os.path.join(home, "agent-escape-test.txt")
    r = sbx.tool_sandbox_exec(
        {"command": "python3",
         "args": ["-c", f"open('{marker}','w').write('x')"]})
    assert not r["ok"]
    assert not os.path.exists(marker)


@needs_bwrap
def test_sandbox_status_confinement_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(sbx, "SCRATCH", tmp_path)
    r = sbx.tool_sandbox_status({})
    assert r["ok"] and r["scratch_writable"] and r["host_fs_hidden"]
