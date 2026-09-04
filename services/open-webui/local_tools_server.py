#!/usr/bin/env python3
"""local-tools — OpenAPI tool server for Open-WebUI.

Exposes two server-side tools:

- read_file: read a text file from an allowed host path.
- run_safe_command: run a pre-approved, read-only diagnostic command.

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
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
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

# Roots that read_file may access. Secrets are still filtered below.
_DEFAULT_READ_ROOTS = (
    os.path.expanduser("~") + ":/etc:/usr/share:/opt/krill_harvester:/tmp"
)
READ_ROOTS = [
    Path(p).resolve()
    for p in os.environ.get("LOCAL_TOOLS_READ_ROOTS", _DEFAULT_READ_ROOTS).split(":")
    if p
]

# Patterns that are always denied for reading.
READ_DENY_PATTERNS = os.environ.get(
    "LOCAL_TOOLS_READ_DENY_PATTERNS",
    "**/.env,**/.env.*,**/secrets.env,**/secrets.json,**/*_secret*,"
    "**/.ssh/**,**/.gnupg/**,/etc/shadow,/etc/sudoers*,/etc/gshadow",
).split(",")

# Commands that run_safe_command may execute. Some have further filters below.
ALLOWED_COMMANDS = frozenset(
    os.environ.get(
        "LOCAL_TOOLS_ALLOWED_COMMANDS",
        "inxi,rocm-smi,nvidia-smi,amdgpu-top,amdgpu_top,lscpu,lsmem,lsusb,lspci,lshw,"
        "dmidecode,ps,pgrep,top,htop,iotop,powertop,df,du,free,vmstat,iostat,sar,"
        "uptime,uname,ss,netstat,ip,ethtool,ping,dig,nslookup,lsof,fuser,traceroute,"
        "tailscale,lsblk,blkid,findmnt,zramctl,swapon,smartctl,hdparm,nvme,fdisk,parted,"
        "dmesg,journalctl,loginctl,systemctl,dpkg,dpkg-query,apt-cache,apt,snap,flatpak,"
        "docker,git,find,grep,rg,ls,cat,head,tail,awk,sed,cut,sort,uniq,wc,tr,xargs,"
        "which,whereis,file,stat,readlink,realpath,modinfo,rocminfo,readelf,objdump,"
        "strings,lsscsi,coredumpctl,lsb_release,cpupower,mount,findmnt,xrandr,wlr-randr,"
        "gsettings,gdbus,ollama,huggingface-cli,hf,yt-dlp,pandoc,ffprobe,ffmpeg,timeout,"
        "gamemoded,sunshine",
    ).split(",")
)

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
    "docker": frozenset(
        {
            "ps",
            "images",
            "info",
            "system",
            "logs",
            "inspect",
            "volume",
            "context",
            "network",
            "stats",
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
        p = Path(path).expanduser().resolve()
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


def tool_read_file(args: dict[str, Any]) -> dict[str, Any]:
    path = args.get("path", "")
    if not path or not isinstance(path, str):
        return {"ok": False, "error": "missing or invalid 'path'"}

    allowed, reason = _is_path_allowed(path)
    if not allowed:
        return {"ok": False, "error": reason}

    try:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return {"ok": False, "error": "not a regular file"}
        if _is_binary(p):
            return {"ok": False, "error": "binary files are not supported"}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"path error: {e}"}

    max_bytes = 102_400
    if isinstance(args.get("max_bytes"), int) and 1 <= args["max_bytes"] <= 1_048_576:
        max_bytes = args["max_bytes"]

    try:
        content = p.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except (OSError, PermissionError) as e:
        return {"ok": False, "error": f"read failed: {e}"}

    return {
        "ok": True,
        "path": str(p),
        "content": content,
        "bytes_read": len(content.encode("utf-8")),
        "truncated": p.stat().st_size > max_bytes,
    }


def _has_shell_meta(s: str) -> bool:
    return bool(_SHELL_METACHAR_RE.search(s))


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
            return False, f"shell metacharacter in argument: {arg}"
        if arg in FORBIDDEN_ARGS:
            return False, f"forbidden argument: {arg}"

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

    sub_filter = SUBCOMMAND_FILTERS.get(base)
    if sub_filter is not None:
        if len(cmd) < 2 or cmd[1] not in sub_filter:
            return False, f"forbidden subcommand for {base}: {cmd[1] if len(cmd) > 1 else '(none)'}"

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


def tool_run_safe_command(args: dict[str, Any]) -> dict[str, Any]:
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
        return {"ok": False, "error": "shell metacharacter in command"}

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


TOOLS = {
    "read_file": tool_read_file,
    "run_safe_command": tool_run_safe_command,
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
        "/read_file": {
            "post": {
                "operationId": "read_file",
                "summary": "Read a text file from an allowed host path.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "max_bytes": {"type": "integer"},
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
                "summary": "Run a safe, read-only diagnostic command.",
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
