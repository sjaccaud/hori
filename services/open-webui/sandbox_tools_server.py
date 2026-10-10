"""sandbox-tools — bwrap-confined disposable-workspace exec for agent tasks.

One tool: sandbox_exec. Runs a bounded command allowlist inside
~/agent-scratch via bubblewrap — the scratch root is the ONLY writable
path in the namespace (system dirs ro-bound, home/etc absent entirely),
and HOME points inside so venvs, pip caches, and git config can't leak.

Trust model (docs/rig-trust-scaffold.md — "the envelope is the safety"):
- Confinement is structural, not prompt-level: bwrap makes /etc, ~, and
  every un-bound path literally nonexistent to the payload.
- Command allowlist is small and shaped for "clone, inspect, test":
  git (no push, no remote subcommand — clone URLs restricted to known
  git hosts or file://), file ops, python3/pytest/pip, make.
- deny.conf still applies as an overlay on the full command line — a
  minted incident citation beats every other layer.
- Everything is journaled to AUDIT_LOG; deny hits go through rig-notify
  like every other surface.

This is Band 2 of the agent trust ladder: disposable by construction —
the worst outcome is a messy scratch dir and wasted iterations.
"""
from __future__ import annotations

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

log = logging.getLogger("sandbox-tools")
logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")

PORT = int(os.environ.get("SANDBOX_TOOLS_PORT", "8774"))
HOST = os.environ.get("SANDBOX_TOOLS_HOST", "127.0.0.1")
SCRATCH = Path(
    os.environ.get(
        "SANDBOX_SCRATCH", os.path.expanduser("~/agent-scratch")
    )
).resolve()
MAX_OUTPUT = int(os.environ.get("SANDBOX_TOOLS_MAX_OUTPUT", "60000"))
DEFAULT_TIMEOUT = int(os.environ.get("SANDBOX_TOOLS_TIMEOUT", "60"))
MAX_TIMEOUT = 300
AUDIT_LOG = os.environ.get(
    "SANDBOX_TOOLS_AUDIT_LOG",
    os.path.expanduser("~/.local/share/sandbox-tools/audit.jsonl"),
)
BWRAP = os.environ.get("SANDBOX_BWRAP", "/usr/bin/bwrap")
RIG_NOTIFY = os.path.expanduser("~/scripts/rig-notify.sh")

# Commands the sandbox may run. Everything else is rejected before bwrap.
# Notable exclusions: env/xargs/nohup (generic launchers — would run ANY
# binary), awk (system() bypass), shells, net tools (curl/wget/ssh/nc —
# egress goes through git-clone host filter or pip/PyPI only). The
# allowlist bounds what the MODEL directly invokes for audit clarity;
# bwrap is the real safety boundary.
ALLOWED_COMMANDS = frozenset(
    "git ls cat head tail grep find wc du tree file stat pwd echo test "
    "mkdir touch cp mv rm chmod python3 python pytest pip make sed diff "
    "sort uniq basename dirname readlink realpath".split()
)

# git subcommands allowed inside the sandbox. push is OUT — nothing in a
# disposable workspace should reach a remote. fetch/pull are out too: a
# crafted file:// clone could carry an arbitrary remote.origin.url, and
# git doesn't re-validate remote hosts — fresh clones are the workflow.
# config is out (would let a call rewrite remote.origin.url or set exec
# aliases); commit identity comes from GIT_* env in the namespace.
GIT_SUBCOMMANDS = frozenset(
    "clone status log diff show branch checkout add commit ls-files tag "
    "rev-parse describe init".split()
)

# git clone may fetch only from these hosts (or local file:// paths).
# The model's egress is thus limited to the git smart protocol on known
# forges — it cannot curl arbitrary URLs because curl isn't allowed at all.
GIT_CLONE_HOSTS = re.compile(
    r"^(https://(github\.com|gitlab\.com|codeberg\.org|git\.sr\.ht)/"
    r"[\w./~-]+|file://[\w./~ -]+)$"
)

# Arg-level guards scoped to git: things that turn an allowed subcommand
# into an escape (upload-pack/exec helpers, remote URLs, config injection).
# Applied only when base == "git" — `-c` is legitimate for python3 etc.
# Flag check is case-SENSITIVE: git -c is config injection (denied) while
# git -C is the canonical chdir-into-repo (allowed, path-checked above).
GIT_FORBIDDEN_ARG_RE = re.compile(
    r"^(--upload-pack|ssh://|ext::|git@[^:]+:)", re.I
)
GIT_FORBIDDEN_FLAG_RE = re.compile(r"^(-u$|--config|-c$)")

# find flags that launch other binaries — same allowlist-bypass class as
# awk system(). -delete/-fprintf stay (they act inside the sandbox only).
FIND_FORBIDDEN_ARG_RE = re.compile(r"^-exec(dir)?$|^-ok(dir)?$")

RIG_DENY_CONF = Path(
    os.environ.get("RIG_DENY_CONF", os.path.expanduser("~/.config/rig/deny.conf"))
)


# Compiled-in floor — same incident-class citations as local_tools_server;
# tuples are (pattern, scope, reason), matching its parser output.
_RIG_DENY_FLOOR = (
    (r"\bddcutil\b", "any",
     "i2c/DDC probe — RAS events + freezes (system-stability Sep-07/08)"),
    (r"\bi2cdetect\b|\bi2cdump\b|\bi2cget\b|\bi2cset\b", "exec",
     "i2c bus probe — same incident class"),
    (r"/dev/i2c", "any", "direct i2c access — same incident class"),
    (r"\bflashrom\b", "exec", "firmware flashing — human-only"),
)


def _rig_deny_rules() -> list[tuple[str, str, str]]:
    """Re-parse deny.conf each call (scope|reason|pattern lines — pattern
    LAST since it may contain |). Identical to local_tools_server's
    parser; keep the two in sync."""
    rules = list(_RIG_DENY_FLOOR)
    try:
        for line in RIG_DENY_CONF.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            scope, reason, pat = parts
            rules.append((pat.strip(), scope.strip(), reason.strip()))
    except OSError:
        pass
    return rules


def _rig_deny_check(subject: str, scope: str) -> str:
    for pat, rule_scope, reason in _rig_deny_rules():
        if rule_scope and rule_scope not in (scope, "any"):
            continue
        try:
            if re.search(pat, subject):
                return reason
        except re.error:
            continue
    return ""


def _report_rig_deny(scope: str, subject: str, reason: str) -> None:
    try:
        subprocess.run(
            ["logger", "-t", "agent-deny",
             f"BLOCKED[sandbox:{scope}] {reason} :: {subject[:160]}"],
            check=False, timeout=5)
        subprocess.run(
            [RIG_NOTIFY, "deny", "600",
             f"blocked[sandbox:{scope}] {subject[:80]} — {reason[:80]}"],
            check=False, timeout=10)
    except Exception:
        pass


# Minimal /etc material for clone/pip: DNS + CA certs. Bound file-by-file
# with --ro-bind-try so absent files don't break startup — and /etc/ssl
# is NEVER bound wholesale (its private/ dir holds host keys).
_NET_BINDS = (
    "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf",
    "/etc/ssl/certs", "/etc/gai.conf",
)


def _bwrap_cmd(cmd: list[str]) -> list[str]:
    """Wrap cmd in a namespace where SCRATCH is the only writable path.

    unshare-pid/ipc/uts: the payload sees only its own processes, no host
    SysV objects, and 'sandbox' as hostname. Network stays shared —
    egress is bounded by the git-clone host filter and pip's PyPI reach.
    """
    argv = [
        BWRAP,
        "--bind", str(SCRATCH), "/sbx",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--dev", "/dev",
        "--proc", "/proc",
        "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--hostname", "sandbox",
        "--tmpfs", "/tmp",
        "--setenv", "HOME", "/sbx",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "GIT_AUTHOR_NAME", "sandbox-agent",
        "--setenv", "GIT_AUTHOR_EMAIL", "agent@sandbox",
        "--setenv", "GIT_COMMITTER_NAME", "sandbox-agent",
        "--setenv", "GIT_COMMITTER_EMAIL", "agent@sandbox",
        "--chdir", "/sbx",
        "--die-with-parent",
    ]
    for src in _NET_BINDS:
        argv += ["--ro-bind-try", src, src]
    return [*argv, "--", *cmd]


def _validate(cmd: list[str]) -> tuple[bool, str]:
    if not cmd:
        return False, "empty command"
    base = os.path.basename(cmd[0])

    reason = _rig_deny_check(" ".join(shlex.quote(c) for c in cmd), "exec")
    if reason:
        _report_rig_deny("exec", " ".join(cmd), reason)
        return False, f"blocked by rig deny.conf: {reason}"

    if base in ("bash", "sh", "zsh", "fish", "csh", "ksh", "tcsh", "sudo",
                "su", "pkexec", "doas", "bwrap", "unshare", "nsenter"):
        return False, f"forbidden interpreter/escalator: {base}"
    if base not in ALLOWED_COMMANDS:
        return False, f"command not in sandbox allowlist: {base}"

    if base == "git":
        # `git -C <path>` is the canonical way to operate inside a cloned
        # repo (there is no cd/workdir) — the -C arg must stay inside the
        # sandbox root, then a subcommand must follow.
        rest = cmd[1:]
        if rest[:1] == ["-C"]:
            if len(rest) < 3:
                return False, "git -C needs a path then a subcommand"
            cpath = rest[1]
            if ".." in cpath.split("/"):
                return False, f"git -C path escapes sandbox: {cpath}"
            if cpath.startswith("/") and not (
                cpath == "/sbx" or cpath.startswith("/sbx/")
            ):
                return False, f"git -C path outside sandbox root: {cpath}"
            rest = rest[2:]
        if not rest:
            return False, "git requires a subcommand"
        sub = rest[0]
        if sub.startswith("-"):
            return False, f"git global flag not allowed: {sub}"
        if sub not in GIT_SUBCOMMANDS:
            return False, f"git subcommand not in sandbox allowlist: {sub}"
        if sub == "clone":
            urls = [a for a in rest[1:] if not a.startswith("-")]
            if not urls:
                return False, "git clone needs a URL"
            url = urls[0]
            if not GIT_CLONE_HOSTS.match(url):
                return False, (
                    f"clone host not allowed: {url} — permitted: "
                    "github.com, gitlab.com, codeberg.org, git.sr.ht, file://"
                )

    for arg in cmd[1:]:
        if base == "git" and (
            GIT_FORBIDDEN_ARG_RE.search(arg)
            or GIT_FORBIDDEN_FLAG_RE.match(arg)
        ):
            return False, f"forbidden argument: {arg}"
        if base == "find" and FIND_FORBIDDEN_ARG_RE.match(arg):
            return False, f"find flag launches commands: {arg}"
        # No metachar check: there is no shell here — |;&()` are inert
        # literal args, and grep/find/python legitimately need them.
        if arg.startswith("/") and not (
            arg == "/sbx" or arg.startswith("/sbx/")
        ) and base not in ("ls", "cat", "head", "tail",
                           "grep", "find", "wc", "du",
                           "tree", "file", "stat",
                           "readlink", "realpath",
                           "dirname", "basename", "diff"):
            return False, (
                f"absolute path argument not allowed for {base}: {arg} — "
                "work inside the sandbox root"
            )
        if ".." in arg.split("/"):
            return False, f"path escapes sandbox: {arg}"
    return True, ""


def _audit(args: dict[str, Any], result: dict[str, Any]) -> None:
    try:
        Path(AUDIT_LOG).parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": int(time.time()),
            "tool": "sandbox_exec",
            "command": " ".join(
                shlex.quote(str(c))
                for c in [args.get("command"), *(args.get("args") or [])]
                if c
            ),
            "ok": result.get("ok"),
            "rc": result.get("returncode"),
            "stdout_len": len(result.get("stdout", "")),
        }
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def tool_sandbox_exec(args: dict[str, Any]) -> dict[str, Any]:
    cmd = [str(args.get("command", ""))]
    raw_args = args.get("args") or []
    if isinstance(raw_args, str):
        raw_args = [raw_args]
    cmd.extend(str(a) for a in raw_args)
    cmd = [c for c in cmd if c]

    ok, err = _validate(cmd)
    if not ok:
        return {"ok": False, "error": err}

    try:
        timeout = min(int(args.get("timeout") or DEFAULT_TIMEOUT), MAX_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    try:
        proc = subprocess.run(
            _bwrap_cmd(cmd),
            capture_output=True, text=True, timeout=timeout,
        )
        out = proc.stdout
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + "\n...[truncated]"
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": out,
            "stderr": proc.stderr[:8000],
            "sandbox": str(SCRATCH),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except FileNotFoundError:
        return {"ok": False, "error": f"command not found: {cmd[0]}"}
    except Exception as e:
        return {"ok": False, "error": f"exec error: {e}"}


def tool_sandbox_status(_args: dict[str, Any]) -> dict[str, Any]:
    """Report sandbox shape: scratch root, usage, confinement alive."""
    usage = "?"
    try:
        p = subprocess.run(["du", "-sh", str(SCRATCH)],
                           capture_output=True, text=True, timeout=10)
        usage = p.stdout.split()[0] if p.returncode == 0 else "?"
    except Exception:
        usage = "?"
    probe = subprocess.run(
        _bwrap_cmd(["test", "-w", "/sbx"]),
        capture_output=True, timeout=10,
    )
    confined = subprocess.run(
        _bwrap_cmd(["test", "-e", "/etc/passwd"]),
        capture_output=True, timeout=10,
    )
    return {
        "ok": True,
        "scratch": str(SCRATCH),
        "scratch_usage": usage,
        "bwrap": Path(BWRAP).exists(),
        "scratch_writable": probe.returncode == 0,
        "host_fs_hidden": confined.returncode != 0,
    }


TOOLS: dict[str, Any] = {
    "sandbox_exec": tool_sandbox_exec,
    "sandbox_status": tool_sandbox_status,
}

OPENAPI_SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "Sandbox Tools",
        "version": "1.0.0",
        "description": (
            "Disposable bwrap-confined workspace for exploratory agent "
            "work — clone, inspect, test inside ~/agent-scratch; nothing "
            "outside the root exists to the payload."
        ),
    },
    "paths": {
        "/sandbox_exec": {
            "post": {
                "operationId": "sandbox_exec",
                "summary": (
                    "Run a command inside a disposable sandbox confined to "
                    "~/agent-scratch (bwrap — host fs hidden, HOME is the "
                    "sandbox). For exploratory work: clone a repo, inspect "
                    "code, run tests, scratch computations. Allowed: git "
                    "(clone/status/log/diff/show/branch/checkout/add/commit "
                    "— no push/fetch/pull/remote/config; clone URLs limited "
                    "to github.com, gitlab.com, codeberg.org, git.sr.ht, "
                    "file://), file ops (ls cat grep find head tail wc tree "
                    "mkdir cp mv rm), python3/pytest/pip (venvs stay inside), "
                    "make, sed. There is no shell — redirections like > and "
                    ">> do nothing; write files with python3 -c or sed -i, "
                    "and use git -C <dir> <subcommand> inside a clone. "
                    "Refuse tasks that need shells or network tools rather "
                    "than working around."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "command": {"type": "string"},
                                    "args": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "timeout": {"type": "integer"},
                                },
                                "required": ["command"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Exec result"}},
            }
        },
        "/sandbox_status": {
            "post": {
                "operationId": "sandbox_status",
                "summary": "Sandbox shape: root, usage, confinement health.",
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "responses": {"200": {"description": "Status"}},
            }
        },
    },
}

MAX_HTTP_BODY_BYTES = 256 * 1024


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, data: Any) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        )

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
            if length > MAX_HTTP_BODY_BYTES:
                self.close_connection = True
                self._send_json(413, {"error": "request body too large"})
                return
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            args = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"error": f"invalid request: {e}"})
            return
        result = TOOLS[tool_name](args)
        _audit(args, result)
        self._send_json(200 if result.get("ok") else 422, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(fmt, *args)


def main() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if not Path(BWRAP).exists():
        log.error("bwrap missing at %s — sandbox cannot start", BWRAP)
        sys.exit(1)
    server = HTTPServer((HOST, PORT), Handler)
    log.info("sandbox-tools listening on http://%s:%d root=%s",
             HOST, PORT, SCRATCH)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
