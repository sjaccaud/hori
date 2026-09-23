"""run_safe_command path-arg policy tests (2026-09-23 hardening).

Before this change, read_file's READ_DENY_PATTERNS/READ_ROOTS only applied
to read_file — run_safe_command path arguments were unchecked, so
`cat ~/.config/mcpo/hori.json` returned the MCP bearer token through the
"read-only" surface.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import local_tools_server as jlt  # noqa: E402

HOME = os.path.expanduser("~")


def _denied(cmd):
    result = jlt.tool_run_safe_command({"command": cmd})
    assert not result["ok"], f"should have denied: {cmd} -> {result}"
    return result


def test_path_args_respect_deny_patterns():
    for cmd in [
        f"cat {HOME}/.ssh/id_ed25519",
        f"cat {HOME}/.config/mcpo/hori.json",
        f"cat {HOME}/.config/gh/hosts.yml",
        f"cat {HOME}/.ollama/id_ed25519",
        "cat /etc/shadow",
        f"grep -r token {HOME}/.gnupg",
        f"find {HOME}/.ssh -type f",
        f"git -C {HOME}/.ssh status",
    ]:
        _denied(cmd)


def test_denied_dir_itself_not_just_children():
    # '**/.ssh/**' needs a trailing child to match — the bare dir arg must
    # still be denied.
    _denied(f"ls {HOME}/.ssh")
    _denied(f"cat {HOME}/.env")


def test_path_args_outside_read_roots():
    for cmd in [
        "cat /root/.bashrc",
        "ls /nonexistent-path-12345",
        "find / -name foo",
        "cat /var/log/syslog",
    ]:
        _denied(cmd)


def test_docker_inspect_rejected():
    # inspect dumps container env — tokens live there.
    _denied("docker inspect open_webui_aios")


def test_writer_arg_guards():
    for cmd in [
        "sed -i s/x/y/ /tmp/f",
        "sed --in-place s/x/y/ /tmp/f",
        "pandoc -o /tmp/out /tmp/in",
        "pandoc --output=/tmp/out /tmp/in",
        "yt-dlp -o /tmp/x https://example.com/v",
        "hf download x --local-dir /tmp/x",
        "ffmpeg -i /tmp/a /tmp/b",  # removed from allowlist entirely
    ]:
        _denied(cmd)


def test_normal_reads_still_work():
    for cmd in [
        "cat /etc/hostname",
        "ls /tmp",
        "df -h",
        "cat /etc/fstab",
    ]:
        result = jlt.tool_run_safe_command({"command": cmd})
        assert result["ok"], f"{cmd} failed: {result.get('stderr') or result.get('error')}"
        assert result["exit_code"] == 0


def test_url_args_not_treated_as_paths():
    # A URL must not be resolved as a filesystem path (would false-deny).
    ok, reason = jlt._check_command_path_args(["yt-dlp", "https://example.com/v"])
    assert ok, reason
    ok, reason = jlt._check_command_path_args(["git", "ls-remote", "https://github.com/x/y"])
    assert ok, reason
