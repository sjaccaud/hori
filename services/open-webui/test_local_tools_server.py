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


def test_run_safe_command_expands_tilde_in_args():
    """Shell-free exec does not expand ~ — the server must, the same way
    read_file already does via Path.expanduser(). Models frequently emit
    ~/... paths; without expansion they see a phantom 'No such file'
    and conclude the filesystem is absent."""
    result = jlt.tool_run_safe_command({"command": "ls ~/Projects/aios"})
    assert result["ok"], result
    assert "services" in result["stdout"]


def test_run_safe_command_expands_bare_tilde():
    result = jlt.tool_run_safe_command({"command": "ls ~"})
    assert result["ok"], result
    assert "Projects" in result["stdout"] or "scripts" in result["stdout"]


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
    # Nonexistent path under a read root: passes path validation, fails at exec.
    result = jlt.tool_run_safe_command({"command": "ls /tmp/nonexistent-path-12345"})
    assert not result["ok"]
    assert result["exit_code"] != 0
    assert "No such file" in result["stderr"] or "No such file" in result["stdout"]


def test_read_file_summarizes_oversized_files_via_nano(monkeypatch, tmp_path):
    """An untargeted read (no explicit max_bytes) of a file larger than the
    default cap should return a nano-generated summary instead of a raw
    truncation."""
    f = tmp_path / "long.txt"
    f.write_text("line\n" * 30_000)  # 150 KB > 102_400 default cap

    class FakeResponse:
        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "A compact summary."}}]}
            ).encode()

        def __enter__(self, *args):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        return FakeResponse()

    monkeypatch.setattr(jlt.urllib.request, "urlopen", fake_urlopen)
    result = jlt.tool_read_file({"path": str(f)})
    assert result["ok"], result
    assert result.get("summarized"), result
    assert "A compact summary." in result["content"]
    assert result["truncated"]


def test_read_file_explicit_max_bytes_is_a_targeted_read(monkeypatch, tmp_path):
    """An explicit max_bytes means the caller wants exact paged content — no
    summary unless they also pass summarize=true."""
    f = tmp_path / "long.txt"
    f.write_text("x" * 500)

    def boom_urlopen(req, timeout=None):
        raise AssertionError("nano must not be called for targeted reads")

    monkeypatch.setattr(jlt.urllib.request, "urlopen", boom_urlopen)
    result = jlt.tool_read_file({"path": str(f), "max_bytes": 100})
    assert result["ok"]
    assert result["truncated"]
    assert not result.get("summarized")
    assert result["content"] == "x" * 100


def test_read_file_summarize_opt_in_and_opt_out(monkeypatch, tmp_path):
    """summarize=true forces a summary even with explicit max_bytes;
    summarize=false disables it entirely."""
    f = tmp_path / "long.txt"
    f.write_text("x" * 500)

    class FakeResponse:
        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "opt-in summary"}}]}
            ).encode()

        def __enter__(self, *args):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        jlt.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse()
    )
    result = jlt.tool_read_file(
        {"path": str(f), "max_bytes": 100, "summarize": True}
    )
    assert result["ok"] and result.get("summarized"), result
    assert "opt-in summary" in result["content"]

    def boom_urlopen(req, timeout=None):
        raise AssertionError("nano must not be called when summarize=false")

    monkeypatch.setattr(jlt.urllib.request, "urlopen", boom_urlopen)
    big = tmp_path / "big.txt"
    big.write_text("line\n" * 30_000)  # > default cap
    result = jlt.tool_read_file({"path": str(big), "summarize": False})
    assert result["ok"]
    assert result["truncated"]
    assert not result.get("summarized")
    assert result["content"].startswith("line\n")


def test_read_file_falls_back_to_truncation_when_nano_down(monkeypatch, tmp_path):
    """If the nano endpoint is unreachable, read_file must still return the
    truncated content rather than failing."""
    f = tmp_path / "long.txt"
    f.write_text("line\n" * 30_000)

    def down_urlopen(req, timeout=None):
        raise jlt.urllib.error.URLError("connection refused")

    monkeypatch.setattr(jlt.urllib.request, "urlopen", down_urlopen)
    result = jlt.tool_read_file({"path": str(f)})
    assert result["ok"], result
    assert result["truncated"]
    assert not result.get("summarized")
    assert result["content"].startswith("line\n")


def test_run_safe_command_strict_mode_disables_extended(monkeypatch):
    """LOCAL_TOOLS_EXTENDED_COMMANDS=0 drops docker/git/ollama but keeps diagnostics."""
    monkeypatch.setenv("LOCAL_TOOLS_EXTENDED_COMMANDS", "0")
    monkeypatch.delenv("LOCAL_TOOLS_ALLOWED_COMMANDS", raising=False)
    allowed = jlt._allowed_commands()
    assert "docker" not in allowed
    assert "git" not in allowed
    assert "ollama" not in allowed
    assert "ffmpeg" not in allowed
    assert "ls" in allowed
    assert "df" in allowed
    assert "systemctl" in allowed


def test_run_safe_command_timeout_binary_is_denied():
    """The `timeout` wrapper binary is not in the allowlist (tool has its own timeout)."""
    result = jlt.tool_run_safe_command({"command": "timeout 5 ping 127.0.0.1"})
    assert not result["ok"]
    assert "not in allowlist" in result["error"]


def test_run_safe_command_docker_drops_ambiguous_subcommands():
    """docker ps is fine; docker system/volume/network/context are not."""
    result = jlt.tool_run_safe_command({"command": "docker ps"})
    assert result["ok"]
    for cmd in [
        "docker system prune",
        "docker volume ls",
        "docker network ls",
        "docker context ls",
    ]:
        result = jlt.tool_run_safe_command({"command": cmd})
        assert not result["ok"], f"should have denied {cmd}: {result}"


def test_run_safe_command_cwd_is_not_a_repo():
    """Commands run from COMMAND_CWD (~), not the service's own repo cwd —
    bare `git` must fail honestly instead of returning wrong-repo data."""
    result = jlt.tool_run_safe_command({"command": "git status"})
    assert not result["ok"]
    assert "not a git repository" in result["stderr"]


def test_run_safe_command_git_dash_C_targets_repo_under_read_roots():
    """`git -C <path>` runs read-only subcommands on repos under read roots."""
    home = os.path.expanduser("~")
    result = jlt.tool_run_safe_command({"command": f"git -C {home}/Projects/aios status"})
    assert result["ok"], result
    assert "not a git repository" not in result["stderr"]


def test_run_safe_command_git_dash_C_rejects_outside_and_mutations():
    home = os.path.expanduser("~")
    for cmd in [
        "git -C /var/lib status",                    # outside read roots
        f"git -C {home}/Projects/aios push origin main",  # mutation via -C
        "git -C",                                     # missing path+subcommand
    ]:
        result = jlt.tool_run_safe_command({"command": cmd})
        assert not result["ok"], f"should have denied {cmd}: {result}"


def test_run_safe_command_relative_glob_resolves_against_command_cwd(tmp_path, monkeypatch):
    """Relative globs expand where the command actually runs (COMMAND_CWD)."""
    monkeypatch.setattr(jlt, "COMMAND_CWD", tmp_path)
    (tmp_path / "probe-file.zzz").write_text("x")
    result = jlt.tool_run_safe_command({"command": "ls", "args": ["*.zzz"]})
    assert result["ok"], result
    assert "probe-file.zzz" in result["stdout"]


def test_run_safe_command_identical_repeat_is_annotated():
    """A repeat call with identical output gets a 'do not retry' marker."""
    jlt._RECENT_CALLS.clear()
    args = {"command": "ls", "args": ["/nonexistent-path-dup-test"]}
    first = jlt.tool_run_safe_command(args)
    assert not first["ok"]
    assert "duplicate" not in first
    second = jlt.tool_run_safe_command(args)
    assert second.get("duplicate") is True
    assert "do not retry" in second["note"]


def test_run_safe_command_no_duplicate_flag_when_output_changes(tmp_path):
    """Same call, different output = legitimate re-check, no annotation."""
    jlt._RECENT_CALLS.clear()
    args = {"command": "ls", "args": [str(tmp_path)]}
    jlt.tool_run_safe_command(args)
    (tmp_path / "newfile.txt").write_text("x")
    second = jlt.tool_run_safe_command(args)
    assert "duplicate" not in second
    assert "newfile.txt" in second["stdout"]


# --- youtube_transcript ---

def test_openapi_declares_youtube_transcript():
    assert "/youtube_transcript" in jlt.OPENAPI_SPEC["paths"]


def test_transcript_rejects_non_youtube_url():
    r = jlt.tool_youtube_transcript({"url": "https://example.com/v"})
    assert not r["ok"] and "youtube" in r["error"]


def test_transcript_rejects_empty_url():
    r = jlt.tool_youtube_transcript({})
    assert not r["ok"]


_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
hello world

00:00:02.000 --> 00:00:04.000
hello world

00:00:04.000 --> 00:00:06.000
second <c>line</c> &amp; entities
"""


def _fake_ytdlp(meta):
    def _run(cmd, timeout=5):
        return json.dumps(meta), 0
    return _run


def _fake_urlopen(payload):
    class R:
        def read(self):
            return payload if isinstance(payload, bytes) else payload.encode()
        def __enter__(self, *a):
            return self
        def __exit__(self, *a):
            return False
    return lambda url, timeout=None: R()


def test_transcript_happy_path_prefers_manual(monkeypatch):
    meta = {
        "title": "T", "channel": "C", "duration": 60,
        "upload_date": "20261010",
        "subtitles": {"en": [{"ext": "vtt", "url": "http://x/vtt"}]},
        "automatic_captions": {"en": [{"ext": "vtt", "url": "http://x/avtt"}]},
    }
    monkeypatch.setattr(jlt, "_run", _fake_ytdlp(meta))
    monkeypatch.setattr(jlt.urllib.request, "urlopen", _fake_urlopen(_VTT))
    r = jlt.tool_youtube_transcript({"url": "https://youtu.be/abc123XYZ"})
    assert r["ok"] and r["caption_kind"] == "manual" and r["lang"] == "en"
    assert "hello world second line" in r["transcript"]  # dedup + tag strip
    assert r["title"] == "T" and not r["truncated"]


def test_transcript_falls_back_to_auto(monkeypatch):
    meta = {"title": "T", "subtitles": {},
            "automatic_captions": {"en-US": [{"ext": "vtt", "url": "u"}]}}
    monkeypatch.setattr(jlt, "_run", _fake_ytdlp(meta))
    monkeypatch.setattr(jlt.urllib.request, "urlopen", _fake_urlopen(_VTT))
    r = jlt.tool_youtube_transcript({"url": "youtu.be/abc123", "lang": "en"})
    assert r["ok"] and r["caption_kind"] == "auto" and r["lang"] == "en-US"


def test_transcript_no_captions_reports_title(monkeypatch):
    monkeypatch.setattr(jlt, "_run", _fake_ytdlp({"title": "NoCaps"}))
    r = jlt.tool_youtube_transcript({"url": "youtu.be/abc123"})
    assert not r["ok"] and r["title"] == "NoCaps" and "captions" in r["error"]


def test_transcript_ytdlp_failure(monkeypatch):
    monkeypatch.setattr(jlt, "_run", lambda cmd, timeout=5: ("", 1))
    r = jlt.tool_youtube_transcript({"url": "youtu.be/abc123"})
    assert not r["ok"] and "yt-dlp" in r["error"]


def test_transcript_windows_long_text(monkeypatch):
    big_vtt = "WEBVTT\n\n" + "\n".join(
        f"00:00:{i:02d}.000 --> 00:00:{i:02d}.500\nword{i}" for i in range(40))
    meta = {"title": "T",
            "subtitles": {"en": [{"ext": "vtt", "url": "u"}]}}
    monkeypatch.setattr(jlt, "_run", _fake_ytdlp(meta))
    monkeypatch.setattr(jlt.urllib.request, "urlopen", _fake_urlopen(big_vtt))
    r = jlt.tool_youtube_transcript(
        {"url": "youtu.be/abc123", "max_chars": 60})
    assert r["ok"] and r["truncated"] and "start_char=" in r["note"]
    r2 = jlt.tool_youtube_transcript(
        {"url": "youtu.be/abc123", "start_char": r["chars_total"] - 10})
    assert r2["ok"] and not r2["truncated"]


def test_vtt_parse_json3_and_srv3():
    assert jlt._vtt_to_text(
        '{"events":[{"segs":[{"utf8":"a "},{"utf8":"b\\n"}]}]}') == "a b"
    srv3 = '<timedtext><body><p t="0">hi &amp; bye</p><p t="1">yo</p></body></timedtext>'
    assert jlt._vtt_to_text(srv3) == "hi & bye yo"
