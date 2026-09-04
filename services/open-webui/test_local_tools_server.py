import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

import pytest

# The module under test lives next to this test file because the directory
# name contains a hyphen and cannot be a Python package.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import local_tools_server as jlt


@pytest.fixture(autouse=True)
def isolate_audit(tmp_path, monkeypatch):
    """Route the audit log into a temp directory so tests do not pollute home."""
    monkeypatch.setattr(jlt, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    (tmp_path / "audit.jsonl").parent.mkdir(parents=True, exist_ok=True)


def test_health_and_openapi_are_sane():
    spec = jlt.OPENAPI_SPEC
    assert spec["info"]["id"] == "local-tools"
    assert "/read_file" in spec["paths"]
    assert "/run_safe_command" in spec["paths"]
    assert "/get_status" in spec["paths"]


def test_get_status_fetches_aios_core_state(monkeypatch):
    """get_status should call aios-core /system/state and return the snapshot."""
    fake_payload = b'{"status":"healthy","services":{"llama-server":{"up":true}}}'

    class FakeResponse:
        def read(self):
            return fake_payload
        def __enter__(self, *args):
            return self
        def __exit__(self, *args):
            return False

    def fake_urlopen(url, timeout=None):
        assert "127.0.0.1:5680/system/state" in url
        return FakeResponse()

    monkeypatch.setattr(jlt.urllib.request, "urlopen", fake_urlopen)
    result = jlt.tool_get_status({})
    assert result["ok"]
    assert result["status"]["status"] == "healthy"


def test_read_allowed_text_file(tmp_path):
    f = tmp_path / "allowed.txt"
    f.write_text("hello world")
    result = jlt.tool_read_file({"path": str(f)})
    assert result["ok"]
    assert result["content"] == "hello world"
    assert not result["truncated"]


def test_read_file_max_bytes(tmp_path):
    f = tmp_path / "long.txt"
    f.write_text("x" * 500)
    result = jlt.tool_read_file({"path": str(f), "max_bytes": 100})
    assert result["ok"]
    assert result["truncated"]
    assert len(result["content"]) <= 100


def test_read_file_denies_binary(tmp_path):
    f = tmp_path / "binary.dat"
    f.write_bytes(bytes(range(256)))
    result = jlt.tool_read_file({"path": str(f)})
    assert not result["ok"]
    assert "binary" in result["error"].lower()


def test_read_file_denies_secret_paths():
    home = os.path.expanduser("~")
    denied = [
        f"{home}/.ssh/id_rsa",
        f"{home}/.gnupg/secring.gpg",
        f"{home}/.config/aios/secrets.env",
        f"{home}/Projects/foo/secrets.json",
        "/etc/shadow",
        "/etc/sudoers.d/hori-user",
    ]
    for path in denied:
        result = jlt.tool_read_file({"path": path})
        assert not result["ok"], f"should have denied {path}"
        assert "deny" in result["error"].lower() or "denied" in result["error"].lower()


def test_read_file_allows_public_system_files():
    # /etc/passwd is world-readable and contains no credentials.
    result = jlt.tool_read_file({"path": "/etc/passwd", "max_bytes": 200})
    assert result["ok"], result["error"]
    assert "root" in result["content"]


def test_run_allowed_diagnostic_commands():
    for cmd in [
        "ls /tmp",
        "df -h",
        "uname -a",
        "lscpu",
    ]:
        result = jlt.tool_run_safe_command({"command": cmd})
        assert result["ok"], f"{cmd} failed: {result.get('stderr')}"
        assert result["exit_code"] == 0


def test_run_safe_command_rejects_interpreters_and_escalators():
    for cmd in ["bash -c 'id'", "sh -c 'id'", "zsh -c 'id'", "sudo id", "su - root", "pkexec whoami", "doas id"]:
        result = jlt.tool_run_safe_command({"command": cmd})
        assert not result["ok"], f"should have denied {cmd}"


def test_run_safe_command_rejects_package_installers_and_mutations():
    for cmd in [
        "apt install foo",
        "apt-get install foo",
        "pip install foo",
        "pip3 install foo",
        "flatpak install foo",
        "snap install foo",
        "docker run busybox",
        "docker compose up",
        "systemctl start foo",
        "systemctl enable foo",
        "rm -rf /",
        f"rm -rf {os.path.expanduser('~/Projects')}",
        "git commit -m pwn",
        "git push origin main",
    ]:
        result = jlt.tool_run_safe_command({"command": cmd})
        assert not result["ok"], f"should have denied {cmd}: {result}"


def test_run_safe_command_rejects_find_actions_and_xargs_injection():
    for cmd in [
        "find / -exec rm {} ;",
        "find / -delete",
        "find / -ok rm {} ;",
        "xargs -I {} sh -c rm {}",
    ]:
        result = jlt.tool_run_safe_command({"command": cmd})
        assert not result["ok"], f"should have denied {cmd}: {result}"


def test_run_safe_command_rejects_shell_metacharacters():
    for cmd in [
        "ls; id",
        "ls && id",
        "ls || id",
        "ls | cat",
        "ls > /tmp/out",
        "ls < /tmp/out",
        "echo $(whoami)",
        "echo `whoami`",
        "echo $HOME",
        "echo '$(rm -rf /)'",
    ]:
        result = jlt.tool_run_safe_command({"command": cmd})
        assert not result["ok"], f"should have rejected shell metachars in {cmd}: {result}"


def test_run_safe_command_accepts_safe_find():
    home = os.path.expanduser("~")
    result = jlt.tool_run_safe_command(
        {"command": f"find {home}/Projects/aios -maxdepth 3 -type f -name local_tools_server.py"}
    )
    assert result["ok"], result
    assert result["exit_code"] == 0
    assert "local_tools_server.py" in result["stdout"]


def test_run_safe_command_timeout_kills_long_running_command():
    start = time.time()
    result = jlt.tool_run_safe_command({"command": "ping -c 100 127.0.0.1", "timeout": 1})
    elapsed = time.time() - start
    assert not result["ok"]
    assert "timed out" in result["error"].lower()
    assert elapsed < 3


def test_run_safe_command_output_is_capped():
    result = jlt.tool_run_safe_command({"command": "find /etc -type f"})
    # It may complete or be capped; the important part is that output is bounded.
    if result["ok"]:
        assert len(result["stdout"]) <= jlt.MAX_OUTPUT
        assert len(result["stderr"]) <= jlt.MAX_OUTPUT


def test_run_safe_command_returns_stderr_and_exit_code():
    result = jlt.tool_run_safe_command({"command": "ls /nonexistent-path-12345"})
    assert not result["ok"]
    assert result["exit_code"] != 0
    assert "No such file" in result["stderr"] or "No such file" in result["stdout"]
