#!/usr/bin/env python3
"""local-tools — OpenAPI tool server for Open-WebUI.

Exposes server-side read-only tools:

- read_file: read a text file from an allowed host path (auto-summarized
  via the local nano model when the file exceeds max_bytes).
- run_safe_command: run a pre-approved, read-only diagnostic command.
- get_status: fetch a concise AIOS system status snapshot.

Design:
- Single-file, stdlib only, no external dependencies.
- Runs as the normal user (<user>) on 127.0.0.1:8766.
- No shell; no sudo; no package installs; no writes outside an audit log.
- Commands and paths are filtered with explicit allow/deny lists.
- Every tool call is appended to an audit log.

This server is intentionally simpler than the AIOS tool_daemon (which is a
Unix-socket, Landlock/seccomp-hardened spine). It is the Open-WebUI-facing
companion, while the safety spine stays untouched.
"""
from __future__ import annotations

import fnmatch
import glob
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


PORT = int(os.environ.get("LOCAL_TOOLS_PORT", "8766"))
HOST = os.environ.get("LOCAL_TOOLS_HOST", "127.0.0.1")
MAX_OUTPUT = int(os.environ.get("LOCAL_TOOLS_MAX_OUTPUT", "100000"))
DEFAULT_TIMEOUT = int(os.environ.get("LOCAL_TOOLS_TIMEOUT", "30"))
AUDIT_LOG = os.environ.get(
    "LOCAL_TOOLS_AUDIT_LOG",
    os.path.expanduser("~/.local/share/local-tools/audit.jsonl"),
)

# read_file auto-summarization: files larger than max_bytes are condensed by
# the local nano model (llama-nano.service) instead of silently truncating.
NANO_URL = os.environ.get(
    "LOCAL_TOOLS_NANO_URL", "http://127.0.0.1:8090/v1/chat/completions"
)
NANO_MODEL = os.environ.get("LOCAL_TOOLS_NANO_MODEL", "nano")
NANO_SUMMARIZE_CHARS = 80_000
NANO_SUMMARIZE_TIMEOUT = 120

# Roots that read_file may access. Secrets are still filtered below.
_DEFAULT_READ_ROOTS = (
    os.path.expanduser("~") + ":/etc:/usr/share:/opt/krill_harvester:/tmp"
)
READ_ROOTS = [
    Path(p).resolve()
    for p in os.environ.get("LOCAL_TOOLS_READ_ROOTS", _DEFAULT_READ_ROOTS).split(":")
    if p
]

# Working directory for run_safe_command subprocesses and for resolving
# relative paths (file args, globs). Defaults to the user's home, NOT the
# service's own cwd — otherwise bare `git` commands silently operate on
# whatever repo the service happens to run inside (learned 2026-09-18:
# `git show origin/main:README.md` returned the aios repo's README to a
# model asking about an unrelated GitHub project). With cwd=~, bare git
# commands fail honestly ("not a git repository"); use `git -C <path>`.
COMMAND_CWD = Path(
    os.environ.get("LOCAL_TOOLS_CWD", os.path.expanduser("~"))
).resolve()


def _resolve_path(path: str) -> Path:
    """Resolve a user-supplied path against COMMAND_CWD (not process cwd)."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = COMMAND_CWD / p
    return p.resolve()


def _path_under_read_roots(path: str) -> bool:
    try:
        p = _resolve_path(path)
    except (OSError, ValueError):
        return False
    return any(p == root or root in p.parents for root in READ_ROOTS)

# Patterns that are always denied for reading — via read_file AND (as of
# 2026-09-23) via run_safe_command path arguments, which previously let
# `cat`/`grep`/`find` walk straight past this list.
READ_DENY_PATTERNS = os.environ.get(
    "LOCAL_TOOLS_READ_DENY_PATTERNS",
    "**/.env,**/.env.*,**/secrets.env,**/secrets.json,**/*_secret*,"
    "**/.ssh/**,**/.gnupg/**,/etc/shadow,/etc/sudoers*,/etc/gshadow,"
    # Credential-bearing dotfiles/configs found under the read roots:
    "**/.config/mcpo/**,**/.config/gh/**,**/.ollama/**,**/.aws/**,"
    "**/.kube/**,**/.docker/**,**/.netrc,**/.git-credentials,**/hosts.yml,"
    "**/*.pem,**/*.key,**/*.token,**/id_rsa*,**/id_ed25519*,"
    "**/id_ecdsa*,**/id_dsa*",
).split(",")

CORE_COMMANDS = frozenset(
    "inxi,rocm-smi,nvidia-smi,amdgpu-top,amdgpu_top,lscpu,lsmem,lsusb,lspci,lshw,"
    "dmidecode,ps,pgrep,top,htop,iotop,powertop,df,du,free,vmstat,iostat,sar,"
    "uptime,uname,ss,netstat,ip,ethtool,ping,dig,nslookup,lsof,fuser,traceroute,"
    "tailscale,lsblk,blkid,findmnt,zramctl,swapon,smartctl,hdparm,nvme,fdisk,parted,"
    "dmesg,journalctl,loginctl,systemctl,dpkg,dpkg-query,apt-cache,apt,snap,flatpak,"
    "find,grep,rg,ls,cat,head,tail,awk,sed,cut,sort,uniq,wc,tr,xargs,"
    "which,whereis,file,stat,readlink,realpath,modinfo,rocminfo,readelf,objdump,"
    "strings,lsscsi,coredumpctl,lsb_release,cpupower,mount,findmnt,xrandr,wlr-randr,"
    "gsettings,gdbus,gamemoded".split(",")
)

# Commands that can fetch from the network or touch mutable state.
# Set LOCAL_TOOLS_EXTENDED_COMMANDS=0 to disable them for stricter sessions.
EXTENDED_COMMANDS = frozenset(
    "docker,git,huggingface-cli,hf,yt-dlp,pandoc,ffprobe,ollama,sunshine".split(",")
)
# ffmpeg removed 2026-09-23: its output operand is an arbitrary file write
# (persistence via ~/.config/systemd/user etc.) — ffprobe stays for
# inspection. Per-command arg denials for the remaining writers:
COMMAND_ARG_DENY = {
    "sed": frozenset({"-i", "--in-place"}),
    "pandoc": frozenset({"-o", "--output"}),
    "yt-dlp": frozenset({"-o", "--output", "-P", "--paths"}),
    "huggingface-cli": frozenset({"--local-dir"}),
    "hf": frozenset({"--local-dir"}),
}

def _allowed_commands() -> frozenset[str]:
    """Compute the command allowlist from environment overrides."""
    override = os.environ.get("LOCAL_TOOLS_ALLOWED_COMMANDS")
    if override:
        return frozenset(override.split(","))
    strict = os.environ.get("LOCAL_TOOLS_EXTENDED_COMMANDS", "1").lower() in {
        "0",
        "false",
        "off",
        "no",
        "disabled",
    }
    if strict:
        return CORE_COMMANDS
    return CORE_COMMANDS | EXTENDED_COMMANDS


# Commands that run_safe_command may execute. Some have further filters below.
# LOCAL_TOOLS_ALLOWED_COMMANDS overrides everything; otherwise the default is
# CORE + EXTENDED. Set LOCAL_TOOLS_EXTENDED_COMMANDS to 0/false/off/no to run
# in a stricter "diagnostics only" profile (e.g., when the model has been
# reading untrusted external content).
ALLOWED_COMMANDS = _allowed_commands()

# Commands that are allowed only with specific subcommand filters.
SUBCOMMAND_FILTERS: dict[str, frozenset[str] | None] = {
    "systemctl": frozenset(
        {
            "status",
            "list-units",
            "list-unit-files",
            "list-timers",
            "is-active",
            "is-enabled",
            "is-failed",
            "cat",
            "show",
        }
    ),
    "flatpak": frozenset(
        {"list", "search", "info", "ps", "remotes", "history", "permissions"}
    ),
    "snap": frozenset({"list"}),
    # 'inspect' deliberately absent: it dumps container env, and several
    # containers carry tokens in env (aios_embedding WEBUI_SECRET_KEY,
    # n8n N8N_API_KEY) — leaks them to any caller of this tool.
    "docker": frozenset(
        {
            "ps",
            "images",
            "info",
            "logs",
            "stats",
            "top",
        }
    ),
    "apt": frozenset({"list", "show", "search", "policy"}),
    "git": frozenset(
        {"status", "log", "diff", "show", "branch", "remote", "tag", "ls-files", "ls-remote"}
    ),
    "gsettings": frozenset({"get", "list-schemas", "list-keys", "list-recursively", "read"}),
    "gdbus": frozenset({"introspect"}),
    "xargs": None,  # handled separately
    "find": None,   # handled separately
    "nvme": frozenset({"list", "id-ctrl", "id-ns", "smart-log"}),
    "fdisk": frozenset({"-l"}),
    "parted": frozenset({"-l"}),
    "smartctl": frozenset({"-a", "-A", "-H", "--scan", "-i"}),
}

# Flags/args that are forbidden in run_safe_command for any command.
FORBIDDEN_ARGS = frozenset(
    {
        "-exec",
        "-delete",
        "-ok",
        "-execdir",
        "-okdir",
        "--exec",
        "--delete",
        "--interactive",
    }
)

_SHELL_METACHAR_RE = re.compile(r"[;&|`$\n\r\x00<>\"'\\]|\$\(")
_GLOB_CHARS_RE = re.compile(r"[*?\[\]]")
_GLOB_EXPANSION_LIMIT = 256


def _expand_glob_args(argv: list[str]) -> tuple[list[str] | None, str]:
    """Expand args containing glob metachars (* ? [ ]) against the filesystem.

    Shell-free expansion; unmatched patterns return an error so the caller can
    adapt instead of the command failing on a literal '*'.
    """
    out: list[str] = []
    for arg in argv:
        if _GLOB_CHARS_RE.search(arg):
            pattern = arg if os.path.isabs(arg) else str(COMMAND_CWD / arg)
            matches = sorted(glob.glob(pattern))
            if not matches:
                return None, (
                    f"glob pattern matched no files: {arg} "
                    "(use find -name or an explicit path)"
                )
            out.extend(matches)
            if len(out) > _GLOB_EXPANSION_LIMIT:
                return None, (
                    f"glob expansion too large (>{_GLOB_EXPANSION_LIMIT} "
                    f"results): {arg}"
                )
        else:
            out.append(arg)
    return out, ""

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _ensure_audit_dir() -> None:
    Path(AUDIT_LOG).parent.mkdir(parents=True, exist_ok=True)


def _audit(tool: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    _ensure_audit_dir()
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool,
        "args": args,
        "result_summary": {
            "ok": result.get("ok", False),
            "exit_code": result.get("exit_code"),
            "stdout_len": len(result.get("stdout", "")),
            "stderr_len": len(result.get("stderr", "")),
        },
    }
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _is_path_allowed(path: str) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    try:
        p = _resolve_path(path)
    except (OSError, ValueError) as e:
        return False, f"invalid path: {e}"

    for pat in READ_DENY_PATTERNS:
        if pat and fnmatch.fnmatch(str(p), pat):
            return False, f"path matches deny pattern: {pat}"

    for root in READ_ROOTS:
        try:
            p.relative_to(root)
            return True, ""
        except ValueError:
            pass

    return False, "path outside allowed read roots"


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(2048)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    text = bytes(range(32, 127)) + b"\n\r\t\f\b"
    non_text = sum(1 for b in chunk if b not in text)
    return len(chunk) > 0 and non_text / len(chunk) > 0.30


def _summarize_with_nano(path: Path) -> str | None:
    """Ask the local nano model for a Markdown summary of a large file.

    Sends at most NANO_SUMMARIZE_CHARS of the file head to the llama-nano
    endpoint. Returns None on any failure so the caller can fall back to a
    plain truncated read.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            head = f.read(NANO_SUMMARIZE_CHARS)
    except OSError:
        return None

    truncated_note = (
        f"\n\n(Only the first ~{NANO_SUMMARIZE_CHARS} characters are shown; "
        "the file is longer.)" if len(head) >= NANO_SUMMARIZE_CHARS else ""
    )
    req = {
        "model": NANO_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise file summarizer for an expert coding "
                    "assistant. Read the file and produce a structured, dense "
                    "Markdown summary: purpose, key symbols with approximate "
                    "line numbers, imports/dependencies, TODOs and gotchas. "
                    "Use exact symbol names. Do NOT wrap the whole output in "
                    "a fenced code block."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Summarize this file. Path: `{path}`{truncated_note}\n\n"
                    f"```\n{head}\n```"
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
    }
    try:
        http_req = urllib.request.Request(
            NANO_URL,
            data=json.dumps(req).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(http_req, timeout=NANO_SUMMARIZE_TIMEOUT) as resp:
            resp_json = json.loads(resp.read().decode("utf-8"))
        return resp_json["choices"][0]["message"]["content"]
    except Exception as e:
        log.info("nano summary failed for %s: %s", path, e)
        return None


def tool_read_file(args: dict[str, Any]) -> dict[str, Any]:
    path = args.get("path", "")
    if not path or not isinstance(path, str):
        return {"ok": False, "error": "missing or invalid 'path'"}

    allowed, reason = _is_path_allowed(path)
    if not allowed:
        return {"ok": False, "error": reason}

    try:
        p = _resolve_path(path)
        if not p.is_file():
            return {"ok": False, "error": "not a regular file"}
        if _is_binary(p):
            return {"ok": False, "error": "binary files are not supported"}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"path error: {e}"}

    max_bytes = 102_400
    max_bytes_explicit = isinstance(args.get("max_bytes"), int) and 1 <= args["max_bytes"] <= 1_048_576
    if max_bytes_explicit:
        max_bytes = args["max_bytes"]
    # Auto-summarize untargeted reads that exceed the cap; callers who pass an
    # explicit max_bytes are doing bounded/paged reads and get exact content
    # unless they also pass summarize=true.
    summarize_arg = args.get("summarize")
    summarize = bool(summarize_arg) if summarize_arg is not None else not max_bytes_explicit

    try:
        file_size = p.stat().st_size
        if file_size > max_bytes and summarize:
            summary = _summarize_with_nano(p)
            if summary is not None:
                return {
                    "ok": True,
                    "path": str(p),
                    "content": (
                        f"# Bulk summary of {p} ({file_size} bytes)\n\n{summary}\n"
                    ),
                    "bytes_read": min(file_size, NANO_SUMMARIZE_CHARS),
                    "truncated": True,
                    "summarized": True,
                }
        content = p.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except (OSError, PermissionError) as e:
        return {"ok": False, "error": f"read failed: {e}"}

    return {
        "ok": True,
        "path": str(p),
        "content": content,
        "bytes_read": len(content.encode("utf-8")),
        "truncated": file_size > max_bytes,
    }


def _has_shell_meta(s: str) -> bool:
    return bool(_SHELL_METACHAR_RE.search(s))


def _arg_looks_pathy(arg: str) -> bool:
    """Heuristic: does this argv token name a filesystem path?"""
    if "://" in arg:  # URLs (yt-dlp, git ls-remote, hf) are not paths
        return False
    return arg.startswith(("/", "~", ".")) or "/" in arg


def _check_command_path_args(cmd: list[str]) -> tuple[bool, str]:
    """Apply read_file's root/deny policy to run_safe_command path args.

    Previously unchecked: `cat ~/.config/mcpo/hori.json` happily returned
    the MCP bearer token even though read_file would have denied it.
    Pathy args (absolute, ~, dot-relative, or containing a slash, incl.
    --opt=path forms) must resolve under READ_ROOTS and miss
    READ_DENY_PATTERNS; bare names still get the deny-pattern check so
    `cat id_ed25519`-style relative reads stay honest.
    """
    for arg in cmd[1:]:
        cands = [arg]
        if "=" in arg:
            cands.append(arg.split("=", 1)[1])
        for cand in cands:
            if not cand:
                continue
            try:
                resolved = _resolve_path(cand)
            except (OSError, ValueError):
                if _arg_looks_pathy(cand):
                    return False, f"invalid path argument: {cand}"
                continue
            for pat in READ_DENY_PATTERNS:
                # resolved + "/" also catches the denied dir itself:
                # '**/.ssh/**' needs a trailing child to match otherwise.
                if pat and (
                    fnmatch.fnmatch(str(resolved), pat)
                    or fnmatch.fnmatch(str(resolved) + "/", pat)
                ):
                    return False, f"path matches deny pattern: {cand}"
            if _arg_looks_pathy(cand) and not _path_under_read_roots(cand):
                return False, f"path outside allowed read roots: {cand}"
    return True, ""


def _validate_command(cmd: list[str]) -> tuple[bool, str]:
    if not cmd:
        return False, "empty command"

    base = os.path.basename(cmd[0])

    if base in ("bash", "sh", "zsh", "fish", "csh", "ksh", "tcsh", "sudo", "su", "pkexec", "doas"):
        return False, f"forbidden interpreter/escalator: {base}"

    if base not in ALLOWED_COMMANDS:
        return False, f"command not in allowlist: {base}"

    for arg in cmd[1:]:
        if _has_shell_meta(arg):
            return False, (
                f"shell metacharacter in argument: {arg} — there is no shell "
                "here: no pipes |, chaining ; &&, or $(); glob chars * ? [ ] "
                "are expanded automatically"
            )
        if arg in FORBIDDEN_ARGS:
            return False, f"forbidden argument: {arg}"

    arg_deny = COMMAND_ARG_DENY.get(base)
    if arg_deny:
        for arg in cmd[1:]:
            if arg in arg_deny or any(arg.startswith(f + "=") for f in arg_deny):
                return False, f"forbidden argument for {base}: {arg}"

    ok, reason = _check_command_path_args(cmd)
    if not ok:
        return False, reason

    # find-specific filters
    if base == "find":
        for a in cmd[1:]:
            if a in ("-exec", "-ok", "-delete", "-execdir", "-okdir"):
                return False, "find action is forbidden"

    # xargs-specific filters
    if base == "xargs":
        if any(a in ("-I", "-i") for a in cmd[1:]):
            return False, "xargs -I is forbidden"
        # xargs without a target command is read-only on stdin
        if len(cmd) > 1 and not _validate_command(cmd[1:])[0]:
            return False, f"xargs target command not allowed: {cmd[1:]}"

    sub_idx = 1
    if base == "git" and len(cmd) > 1 and cmd[1] == "-C":
        # `git -C <path> <subcommand>`: explicit repo targeting. Needed because
        # COMMAND_CWD is intentionally not inside a repo. The path must sit
        # under a read root so `git show HEAD:<file>` can't bypass read_file's
        # root restrictions.
        if len(cmd) < 4:
            return False, "git -C requires a path and a subcommand"
        if not _path_under_read_roots(cmd[2]):
            return False, f"git -C path outside allowed read roots: {cmd[2]}"
        git_ok, git_reason = _is_path_allowed(cmd[2])
        if not git_ok:
            return False, f"git -C path denied: {git_reason}"
        sub_idx = 3

    sub_filter = SUBCOMMAND_FILTERS.get(base)
    if sub_filter is not None:
        if len(cmd) <= sub_idx or cmd[sub_idx] not in sub_filter:
            return False, f"forbidden subcommand for {base}: {cmd[sub_idx] if len(cmd) > sub_idx else '(none)'}"

    return True, ""


def _resolve_command(cmd: list[str]) -> list[str]:
    """Resolve a base command to its absolute path if possible."""
    base = cmd[0]
    if os.path.isabs(base) and os.path.isfile(base) and os.access(base, os.X_OK):
        return cmd
    # Search PATH
    for d in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep):
        p = os.path.join(d, base)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            cmd[0] = p
            return cmd
    return cmd


def _execute_safe_command(args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command", "")
    extra_args = args.get("args", []) or []
    if not isinstance(extra_args, list):
        return {"ok": False, "error": "'args' must be a list"}

    timeout = DEFAULT_TIMEOUT
    if isinstance(args.get("timeout"), int) and 1 <= args["timeout"] <= 300:
        timeout = args["timeout"]

    if not command or not isinstance(command, str):
        return {"ok": False, "error": "missing or invalid 'command'"}

    if _has_shell_meta(command):
        return {
            "ok": False,
            "error": (
                "shell metacharacter in command — there is no shell here: "
                "no pipes |, chaining ; &&, or $(); pass a plain command and "
                "args (glob chars * ? [ ] are expanded automatically)"
            ),
        }

    try:
        cmd = shlex.split(command)
    except ValueError as e:
        return {"ok": False, "error": f"command parse error: {e}"}

    if extra_args:
        # extra_args are intended for the command; merge them in.
        # They may not contain shell metacharacters.
        for a in extra_args:
            if not isinstance(a, str) or _has_shell_meta(a):
                return {"ok": False, "error": f"invalid arg: {a}"}
        cmd.extend(extra_args)

    cmd, glob_err = _expand_glob_args(cmd)
    if glob_err:
        return {"ok": False, "error": glob_err}

    ok, reason = _validate_command(cmd)
    if not ok:
        return {"ok": False, "error": reason}

    cmd = _resolve_command(cmd)

    try:
        proc = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
            cwd=str(COMMAND_CWD),
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"command not found: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"command timed out after {timeout}s"}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"command failed to start: {e}"}

    stdout = proc.stdout[:MAX_OUTPUT]
    stderr = proc.stderr[:MAX_OUTPUT]
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": len(proc.stdout) > MAX_OUTPUT,
        "stderr_truncated": len(proc.stderr) > MAX_OUTPUT,
    }


# Duplicate-call guard: runaway tool loops burn iterations re-issuing the
# exact same call (seen 2026-09-18: git ls-remote x5 in one turn). Results
# are still executed fresh every time — never cached/stale — but a repeat
# that produces byte-identical output is annotated so the model knows to
# move on instead of retrying.
_RECENT_CALLS: "OrderedDict[str, str]" = OrderedDict()
_RECENT_CALLS_MAX = 256


def tool_run_safe_command(args: dict[str, Any]) -> dict[str, Any]:
    key = json.dumps(
        {"command": args.get("command"), "args": args.get("args")},
        sort_keys=True,
        default=str,
    )
    result = _execute_safe_command(args)
    signature = json.dumps(result, sort_keys=True, default=str)

    if _RECENT_CALLS.get(key) == signature:
        result["duplicate"] = True
        result["note"] = (
            "identical call already ran and produced identical output — "
            "do not retry it; adjust the approach or answer with what you have"
        )
    _RECENT_CALLS[key] = signature
    _RECENT_CALLS.move_to_end(key)
    while len(_RECENT_CALLS) > _RECENT_CALLS_MAX:
        _RECENT_CALLS.popitem(last=False)
    return result


def _run(cmd: list[str], timeout: int = 5) -> tuple[str, int]:
    """Run a command and return (stdout, returncode)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.stdout, proc.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "", 1


def _loaded_model() -> str | None:
    """Ask llama-server which preset is currently loaded, if any."""
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8080/v1/models", timeout=3
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for m in data.get("data", []):
            if m.get("status", {}).get("value") == "loaded":
                return m.get("id")
    except Exception:
        pass
    return None


def _llama_health() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("status") == "ok"
    except Exception:
        return False


def _systemctl_states(services: list[str]) -> dict[str, str]:
    stdout, _ = _run(["systemctl", "is-active", *services])
    states = stdout.strip().splitlines()
    return {s: (states[i] if i < len(states) else "unknown") for i, s in enumerate(services)}


def _gpu_snapshot() -> dict[str, Any]:
    stdout, _ = _run(["rocm-smi", "--showid", "--showmeminfo", "VRAM", "--json"])
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {}


def _disk_snapshot() -> dict[str, Any]:
    snapshot = {}
    for fs in "/ /home /opt /mnt/4TB /mnt/8TB".split():
        stdout, _ = _run(["df", "-h", fs])
        lines = stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 6:
                snapshot[fs] = {
                    "size": parts[1],
                    "used": parts[2],
                    "available": parts[3],
                    "use_percent": parts[4],
                }
    return snapshot


def _memory_snapshot() -> dict[str, str]:
    stdout, _ = _run(["free", "-h"])
    lines = stdout.strip().splitlines()
    if len(lines) >= 2:
        parts = lines[1].split()
        if len(parts) >= 7:
            return {
                "total": parts[1],
                "used": parts[2],
                "free": parts[3],
                "shared": parts[4],
                "buff_cache": parts[5],
                "available": parts[6],
            }
    return {}


def _docker_container_states(containers: list[str]) -> dict[str, str]:
    states = {}
    for name in containers:
        stdout, _ = _run(
            [
                "docker",
                "ps",
                "--filter",
                f"name={name}",
                "--format",
                "{{.Status}}",
            ]
        )
        states[name] = stdout.strip() or "not running"
    return states


def tool_get_status(_args: dict[str, Any]) -> dict[str, Any]:
    """Fetch a concise AIOS system status snapshot.

    Preferred source is aios-core /system/state. If it is unreachable, build
    the same shape from local read-only probes so the tool keeps working.
    """
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:5680/system/state", timeout=15
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        data["source"] = "aios-core"
        return {"ok": True, "status": data}
    except Exception as e:
        # Fallback: aios-core is a convenience, not a hard dependency.
        snapshot = {
            "status": "degraded",
            "source": "local-tools-fallback",
            "aios_core_error": str(e),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "services": {
                **_systemctl_states(
                    [
                        "llamacpp-adaptive",
                        "llama-nano-adaptive",
                        "local-tools-adaptive",
                        "project-tools-adaptive",
                    ]
                ),
                **_docker_container_states(
                    [
                        "open_webui_aios",
                        "searchrouter_aios",
                        "aios_embedding",
                        "prometheus_aios",
                        "grafana_aios",
                        "qdrant_aios",
                    ]
                ),
                "llama-server-health": _llama_health(),
            },
            "loaded_model": _loaded_model(),
            "gpu": _gpu_snapshot(),
            "memory": _memory_snapshot(),
            "disk": _disk_snapshot(),
        }
        return {"ok": True, "status": snapshot}


TOOLS = {
    "read_file": tool_read_file,
    "run_safe_command": tool_run_safe_command,
    "get_status": tool_get_status,
}


def _tool_schema(name: str, desc: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": [k for k, v in params.items() if v.get("required")],
            },
        },
    }


OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {
        "id": "local-tools",
        "title": "HORI Local Tools",
        "version": "0.1.0",
        "description": "Read-only filesystem and safe diagnostic command tools.",
    },
    "servers": [{"url": f"http://{HOST}:{PORT}"}],
    "paths": {
        "/get_status": {
            "post": {
                "operationId": "get_status",
                "summary": "Fetch a concise AIOS system status snapshot (services, GPU, disk, model).",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {},
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "System status snapshot or error",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
        "/read_file": {
            "post": {
                "operationId": "read_file",
                "summary": (
                    "Read a text file from an allowed host path. Files "
                    "larger than max_bytes are auto-summarized by the local "
                    "nano model instead of truncating (summarize=false "
                    "disables)."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "max_bytes": {"type": "integer"},
                                    "summarize": {"type": "boolean"},
                                },
                                "required": ["path"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "File content or error",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
        "/run_safe_command": {
            "post": {
                "operationId": "run_safe_command",
                "summary": (
                    "Run a safe, read-only diagnostic command. Shell-free "
                    "runner: no pipes |, chaining ; &&, $(), or find -exec; "
                    "glob chars (* ? [ ]) in arguments are expanded "
                    "automatically. Commands run in the user's home "
                    "directory. For git, target the repo explicitly: "
                    "git -C <path> <subcommand> (allowed: status, log, diff, "
                    "show, branch, remote, tag, ls-files, ls-remote). "
                    "Identical repeat calls are flagged — do not retry them."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "command": {"type": "string"},
                                    "args": {"type": "array", "items": {"type": "string"}},
                                    "timeout": {"type": "integer"},
                                },
                                "required": ["command"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Command output or error",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
    },
}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, data: Any) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/openapi.json":
            self._send_json(200, OPENAPI_SPEC)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        tool_name = self.path.strip("/").split("/")[0]
        if tool_name not in TOOLS:
            self._send_json(404, {"error": f"unknown tool: {tool_name}"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            args = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"error": f"invalid request: {e}"})
            return

        log.info("POST /%s command=%s", tool_name, args.get("command", "<read>"))
        result = TOOLS[tool_name](args)
        _audit(tool_name, args, result)
        self._send_json(200 if result.get("ok") else 422, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(fmt, *args)


def main() -> None:
    _ensure_audit_dir()
    server = HTTPServer((HOST, PORT), Handler)
    log.info("local-tools listening on http://%s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
