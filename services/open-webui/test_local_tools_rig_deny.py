"""run_safe_command + read_file enforce rig deny.conf CONTENT (2026-10-09).

deny.conf (~/.config/rig/deny.conf) is the canonical minted-incident
denylist for ALL agent exec paths (docs/rig-trust-scaffold.md). The Devin
exec path enforces it via agent-deny-check.py; the OWUI path previously
only had the allowlist closure — a blocked command got a bare "not in
allowlist" with no incident citation, and rules minted after an incident
never reached this surface.

These tests pin the ported-parser contract: exec-scope rules deny matching
command strings (even allowlisted commands — the conf wins), read/any-scope
rules deny matching read_file paths, and a compiled-in floor keeps the i2c
incident class cited even when the conf is unreadable.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import local_tools_server as jlt  # noqa: E402


@pytest.fixture(autouse=True)
def reports(monkeypatch):
    """Record rig-deny reports instead of really logging/notifying —
    otherwise every test deny would push a real phone notification."""
    seen = []
    monkeypatch.setattr(jlt, "_report_rig_deny",
                        lambda s, sub, r: seen.append((s, sub, r)))
    return seen


@pytest.fixture()
def deny_conf(tmp_path, monkeypatch):
    """Point the server at a scratch deny.conf; tests write their own rules."""
    conf = tmp_path / "deny.conf"
    monkeypatch.setattr(jlt, "RIG_DENY_CONF", conf)
    return conf


def test_exec_rule_denies_allowlisted_command(deny_conf):
    # lsblk is in CORE_COMMANDS — only a deny.conf rule can stop it.
    deny_conf.write_text("exec|test-minted: lsblk froze the bus|\\blsblk\\b\n")
    result = jlt.tool_run_safe_command({"command": "lsblk"})
    assert not result["ok"]
    assert "deny.conf" in result["error"]
    assert "test-minted" in result["error"]


def test_i2c_class_denied_with_citation_not_bare_allowlist():
    # Live conf or floor — either way the Sep-07/08 citation must surface.
    result = jlt.tool_run_safe_command({"command": "ddcutil"})
    assert not result["ok"]
    assert "deny.conf" in result["error"]
    assert "i2c" in result["error"].lower()


def test_any_scope_rule_denies_read_file_path(deny_conf, tmp_path):
    deny_conf.write_text("any|test-minted read block|/dev/fake-bus\n")
    # /tmp is a read root — without the rig rule this read would succeed.
    f = tmp_path / "x"
    f.write_text("data")
    monkey_conf = jlt._is_path_allowed(str(f))
    assert monkey_conf[0], f"control read denied: {monkey_conf}"
    ok, reason = jlt._is_path_allowed("/dev/fake-bus-0")
    assert not ok
    assert "test-minted" in reason


def test_exec_scope_does_not_leak_into_read(deny_conf, tmp_path):
    # exec-scope rules apply to command strings, not file reads.
    deny_conf.write_text("exec|test-minted exec only|\\brig-blocked-name\\b\n")
    f = tmp_path / "rig-blocked-name.txt"
    f.write_text("fine")
    ok, reason = jlt._is_path_allowed(str(f))
    assert ok, f"exec rule leaked into read path: {reason}"


def test_missing_conf_still_denies_i2c_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(jlt, "RIG_DENY_CONF", tmp_path / "absent.conf")
    result = jlt.tool_run_safe_command({"command": "ddcutil detect"})
    assert not result["ok"]
    assert "i2c" in result["error"].lower()


def test_malformed_lines_skipped(deny_conf):
    deny_conf.write_text(
        "garbage line\n"
        "exec|only-two-fields\n"
        "|empty scope|pattern\n"
        "exec|valid|\\blsblk\\b\n"
    )
    result = jlt.tool_run_safe_command({"command": "lsblk"})
    assert not result["ok"]
    assert "valid" in result["error"]


def test_deny_is_reported_not_silent(deny_conf, reports):
    """Deny → log + citation, never silent (Phase-1 contract). A denied
    call must reach the journal/notify reporter so the /trust deny-hits
    panel sees OWUI-path blocks, not just Devin-hook ones."""
    deny_conf.write_text("exec|test-minted|\\blsblk\\b\n")
    jlt.tool_run_safe_command({"command": "lsblk"})
    assert reports and reports[0][0] == "exec"
    assert "test-minted" in reports[0][2]


def test_allowlisted_clean_command_not_reported(reports):
    jlt.tool_run_safe_command({"command": "df -h"})
    assert reports == []
