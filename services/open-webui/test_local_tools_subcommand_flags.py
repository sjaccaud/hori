"""Subcommand filters must see past leading flags — and must not be
smuggled: a flag that consumes a value ('-M <machine>', '--state <s>')
hides its value from the subcommand check, so 'systemctl -M status
restart x' still denies 'restart'.

User-scope inspection ('systemctl --user list-units') is a legitimate
read-only need — the filter should reach the subcommand, not trip on
the flag in position 1.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import local_tools_server as jlt  # noqa: E402


def _validate(argv):
    return jlt._validate_command(argv)


def test_flag_before_subcommand_allowed():
    ok, _ = _validate(["systemctl", "--user", "list-units"])
    assert ok


def test_flag_with_equals_before_subcommand():
    ok, _ = _validate(
        ["systemctl", "--type=service", "list-units", "--state=failed"])
    assert ok


def test_value_flag_consumes_its_value():
    # --state failed — 'failed' is the value, then 'restart' is the real
    # subcommand and must still be denied.
    ok, reason = _validate(
        ["systemctl", "--state", "failed", "restart", "x.service"])
    assert not ok
    assert "restart" in reason


def test_machine_flag_cannot_smuggle_subcommand():
    # -M/--machine is deliberately NOT a skip-listed value flag: the
    # machine name stays in subcommand position and gets denied.
    ok, _ = _validate(
        ["systemctl", "--machine", "x@.host", "restart", "y.service"])
    assert not ok


def test_docker_host_flag_denied():
    ok, _ = _validate(["docker", "-H", "tcp://remote:2375", "ps"])
    assert not ok


def test_long_value_flag_consumes_its_value():
    # --state failed list-units — 'failed' is --state's value, then the
    # real subcommand is allowed.
    ok, _ = _validate(
        ["systemctl", "--state", "failed", "list-units"])
    assert ok


def test_forbidden_subcommand_still_denied_behind_flags():
    ok, _ = _validate(["systemctl", "--user", "restart", "sshd"])
    assert not ok


def test_bare_forbidden_subcommand_unchanged():
    ok, _ = _validate(["systemctl", "restart", "sshd"])
    assert not ok


def test_no_subcommand_at_all_still_denied():
    ok, _ = _validate(["systemctl", "--user"])
    assert not ok


def test_git_flag_before_subcommand():
    ok, _ = _validate(["git", "--no-pager", "status"])
    assert ok


def test_git_flag_before_forbidden_subcommand():
    ok, _ = _validate(["git", "--no-pager", "push"])
    assert not ok


def test_docker_flag_before_subcommand():
    ok, _ = _validate(["docker", "--format", "{{.Names}}", "ps"])
    assert ok


def test_flag_style_subcommand_still_works():
    # Regression: smartctl/fdisk/parted filters contain flag-like entries.
    ok, _ = _validate(["smartctl", "--scan"])
    assert ok
    ok, _ = _validate(["fdisk", "-l"])
    assert ok
