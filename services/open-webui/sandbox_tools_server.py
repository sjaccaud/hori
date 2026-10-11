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

Network boundary (2026-10-10): every exec gets its own netns via
--unshare-net + slirp4netns --disable-host-loopback — NAT'd outbound
(git/pip/DNS work) but host loopback is unreachable, so a payload
cannot invoke the host's own tool servers (run_safe_command et al.)
from inside. Band 2.5 serve sessions reuse the same netns with a
slirp hostfwd rule: the ONLY reachable host port is the one the tool
explicitly forwards.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import select
import shlex
import socket
import subprocess
import sys
import threading
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
# sandbox_write content cap — bounded under MAX_HTTP_BODY_BYTES (256 KiB)
# so a legal request always fits the HTTP envelope.
MAX_WRITE_BYTES = int(
    os.environ.get("SANDBOX_TOOLS_MAX_WRITE", str(128 * 1024))
)
DEFAULT_TIMEOUT = int(os.environ.get("SANDBOX_TOOLS_TIMEOUT", "60"))
MAX_TIMEOUT = 300
AUDIT_LOG = os.environ.get(
    "SANDBOX_TOOLS_AUDIT_LOG",
    os.path.expanduser("~/.local/share/sandbox-tools/audit.jsonl"),
)
BWRAP = os.environ.get("SANDBOX_BWRAP", "/usr/bin/bwrap")
SLIRP = os.environ.get("SANDBOX_SLIRP", "/usr/bin/slirp4netns")
RIG_NOTIFY = os.path.expanduser("~/scripts/rig-notify.sh")

# Per-exec guest networking state: generated resolv.conf + slirp API
# sockets for serve sessions.
NET_DIR = Path(
    os.environ.get(
        "SANDBOX_NET_DIR",
        os.path.expanduser("~/.local/state/sandbox-net"),
    )
)
HOST_RESOLV = Path("/run/systemd/resolve/resolv.conf")
SLIRP_READY_TIMEOUT = float(os.environ.get("SANDBOX_SLIRP_READY_TIMEOUT", "8"))

# Band 2.5 serve sessions: forwarded only on this loopback port range,
# capped count and TTL. The guest must bind 0.0.0.0 (guest addr is the
# slirp-assigned 10.0.2.100); a guest-side 127.0.0.1 bind never answers.
SERVE_PORT_MIN = int(os.environ.get("SANDBOX_SERVE_PORT_MIN", "8890"))
SERVE_PORT_MAX = int(os.environ.get("SANDBOX_SERVE_PORT_MAX", "8899"))
SERVE_MAX_SESSIONS = int(os.environ.get("SANDBOX_SERVE_MAX_SESSIONS", "4"))
SERVE_DEFAULT_TTL = int(os.environ.get("SANDBOX_SERVE_DEFAULT_TTL", "1800"))
SERVE_MAX_TTL = int(os.environ.get("SANDBOX_SERVE_MAX_TTL", "7200"))
GUEST_ADDR = "10.0.2.100"  # slirp4netns default guest address

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
# resolv.conf is NOT here: the guest netns has no 127.0.0.53 stub —
# a generated file with real upstreams is bound in its place.
_NET_BINDS = (
    "/etc/hosts", "/etc/nsswitch.conf",
    "/etc/ssl/certs", "/etc/gai.conf",
)


def _guest_resolv_conf() -> str:
    """nameserver lines for the guest netns — real upstreams from
    systemd-resolved's non-stub file. IPv4 only (slirp runs without
    --enable-ipv6); 127.0.0.x entries are stub listeners that don't
    exist in the guest. Fallback is slirp's own DNS proxy at 10.0.2.3.
    """
    ns, search = [], ""
    try:
        for line in HOST_RESOLV.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "nameserver":
                if ":" not in parts[1] and not parts[1].startswith("127."):
                    ns.append(parts[1])
            elif parts and parts[0] == "search":
                search = " ".join(parts[1:])
    except OSError:
        pass
    if not ns:
        ns = ["10.0.2.3"]
    out = [f"nameserver {n}" for n in ns[:3]]
    if search:
        out.append(f"search {search}")
    out.append("options edns0 trust-ad")
    return "\n".join(out) + "\n"


def _bwrap_cmd(cmd: list[str], *, resolv: Path | None = None,
               extra: list[str] | None = None) -> list[str]:
    """Wrap cmd in a namespace where SCRATCH is the only writable path.

    unshare-pid/ipc/uts/net: the payload sees only its own processes,
    no host SysV objects, 'sandbox' as hostname, and an empty netns —
    slirp4netns attaches the NAT'd tap interface post-spawn. extra
    carries orchestration flags (--sync-fd/--info-fd) before '--'.
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
        "--unshare-net",
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
    if resolv:
        argv += ["--ro-bind", str(resolv), "/etc/resolv.conf"]
    for src in _NET_BINDS:
        argv += ["--ro-bind-try", src, src]
    if extra:
        argv += extra
    return [*argv, "--", *cmd]


def _spawn_confined(cmd: list[str], *, api_sock: Path | None = None,
                    log_path: Path | None = None
                    ) -> tuple[subprocess.Popen, subprocess.Popen]:
    """Spawn cmd confined in bwrap + private netns with NAT'd egress.

    Orchestration: bwrap blocks the payload on --sync-fd while the
    parent learns the child pid via --info-fd, attaches slirp4netns
    (configures tap0 inside the netns), waits for its ready-fd, then
    releases the payload. --disable-host-loopback makes the host's
    loopback — and therefore the host's tool servers — unreachable.
    Returns (bwrap_proc, slirp_proc); raises on setup failure after
    killing the blocked child. log_path redirects payload stdout/stderr
    to a file — required for long-lived sessions so undrained pipes
    can't deadlock a chatty server.
    """
    try:
        NET_DIR.mkdir(parents=True, exist_ok=True)
        resolv = NET_DIR / "resolv.conf"
        resolv.write_text(_guest_resolv_conf())
    except OSError as e:
        raise RuntimeError(f"guest resolv.conf write failed: {e}")

    sync_r, sync_w = os.pipe()
    info_r, info_w = os.pipe()
    argv = _bwrap_cmd(
        cmd, resolv=resolv,
        extra=["--sync-fd", str(sync_r), "--info-fd", str(info_w)],
    )
    log_fh = open(log_path, "ab") if log_path else None
    proc = subprocess.Popen(
        argv, stdout=log_fh or subprocess.PIPE,
        stderr=subprocess.STDOUT if log_fh else subprocess.PIPE,
        text=(log_fh is None), pass_fds=(sync_r, info_w),
    )
    if log_fh:
        log_fh.close()  # proc holds its own copy
    os.close(info_w)
    os.close(sync_r)
    info: dict[str, Any] = {}
    try:
        # bwrap writes info JSON incrementally — accumulate reads until
        # it parses (can't read to EOF: the payload inherits info_w).
        dec = json.JSONDecoder()
        buf = ""
        deadline = time.time() + 5
        while time.time() < deadline:
            chunk = os.read(info_r, 65536)
            if not chunk:
                break
            buf += chunk.decode()
            try:
                info, _ = dec.raw_decode(buf)
                break
            except ValueError:
                info = {}
        child_pid = int(info["child-pid"])
    except Exception as e:
        proc.kill()
        proc.wait()
        raise RuntimeError(f"bwrap info-fd read failed: {e}")
    finally:
        os.close(info_r)

    ready_r, ready_w = os.pipe()
    sargv = [SLIRP, "--configure", "--mtu=65520",
             "--disable-host-loopback", "-r", str(ready_w)]
    if api_sock:
        sargv += ["-a", str(api_sock)]
    slirp = subprocess.Popen(
        [*sargv, str(child_pid), "tap0"],
        pass_fds=(ready_w,), stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True,
    )
    os.close(ready_w)
    try:
        got, _, _ = select.select(
            [ready_r], [], [], SLIRP_READY_TIMEOUT)
        ready = bool(got) and bool(os.read(ready_r, 64))
    finally:
        os.close(ready_r)
    if not ready or slirp.poll() is not None:
        err = ""
        try:
            err = (slirp.stderr.read() or "")[:400]
        except Exception:
            pass
        slirp.kill()
        proc.kill()
        proc.wait()
        raise RuntimeError(f"slirp4netns attach failed: {err or 'no ready signal'}")
    os.close(sync_w)  # release the payload — network is live
    return proc, slirp


def _reap(proc: subprocess.Popen | None,
          slirp: subprocess.Popen | None) -> None:
    for p in (proc, slirp):
        if p is None or p.poll() is not None:
            continue
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def _run_confined(cmd: list[str], timeout: int
                  ) -> tuple[int | None, str, str]:
    """Run cmd through the full confined path; return (rc, out, err).
    rc None means the process was killed for timeout."""
    proc, slirp = _spawn_confined(cmd)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return None, "", f"timeout after {timeout}s"
    finally:
        _reap(None, slirp)


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
            # The URL is the arg carrying a scheme — flag values like
            # `--depth 1` are non-flag args too and must not be picked.
            urls = [a for a in rest[1:] if "://" in a]
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


def _audit(args: dict[str, Any], result: dict[str, Any],
           tool: str = "sandbox_exec") -> None:
    try:
        Path(AUDIT_LOG).parent.mkdir(parents=True, exist_ok=True)
        cmd = " ".join(
            shlex.quote(str(c))
            for c in [args.get("command"), *(args.get("args") or [])]
            if c
        )
        entry = {
            "ts": int(time.time()),
            "tool": tool,
            "command": cmd or str(args.get("path", "")),
            "ok": result.get("ok"),
            "rc": result.get("returncode"),
            "stdout_len": len(result.get("stdout", "")),
        }
        for k in ("event", "sid", "port", "ttl", "path"):
            if k in result:
                entry[k] = result[k]
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
        rc, out, err = _run_confined(cmd, timeout)
        if rc is None:
            return {"ok": False, "error": err or f"timeout after {timeout}s"}
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + "\n...[truncated]"
        return {
            "ok": rc == 0,
            "returncode": rc,
            "stdout": out,
            "stderr": err[:8000],
            "sandbox": str(SCRATCH),
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"command not found: {cmd[0]}"}
    except Exception as e:
        return {"ok": False, "error": f"exec error: {e}"}


def tool_sandbox_write(args: dict[str, Any]) -> dict[str, Any]:
    """Write a text file inside the scratch root — the band-2 file
    primitive that replaces `python3 -c` quoting (the measured top
    shared failure mode in the coding-duty eval). Same disposable-root
    trust zone as sandbox_exec: the ONLY boundary is path confinement
    under SCRATCH, enforced mechanically — resolve() follows symlinks,
    so a link pointing outside the root leaves SCRATCH and the write
    fails closed; `..` is rejected outright, matching _validate's
    posture. deny.conf is not consulted: it governs host surfaces, and
    nothing outside the scratch root is reachable from here."""
    result: dict[str, Any] = {"ok": False}
    try:
        path = args.get("path")
        content = args.get("content")
        if not isinstance(path, str) or not path.strip():
            result["error"] = "path must be a non-empty string"
            return result
        if not isinstance(content, str):
            result["error"] = (
                "content must be a string — for bytes, use "
                "python3 -c inside sandbox_exec"
            )
            return result
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            result["error"] = (
                f"content too large: {len(data)} bytes "
                f"(max {MAX_WRITE_BYTES})"
            )
            return result
        rel = path.strip()
        if rel in ("/sbx", "/sbx/"):
            result["error"] = "path is the sandbox root, not a file"
            return result
        if rel.startswith("/sbx/"):
            rel = rel[len("/sbx/"):]
        elif rel.startswith("/"):
            result["error"] = (
                f"absolute path outside sandbox root: {path} — "
                "write under /sbx/ or use a relative path"
            )
            return result
        if ".." in rel.split("/"):
            result["error"] = f"path escapes sandbox: {path}"
            return result
        dest = (SCRATCH / rel).resolve()
        if not dest.is_relative_to(SCRATCH):
            result["error"] = f"path escapes sandbox: {path}"
            return result
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "ab" if args.get("append") else "wb") as f:
            f.write(data)
        result = {
            "ok": True,
            "path": f"/sbx/{dest.relative_to(SCRATCH)}",
            "bytes": len(data),
        }
        return result
    except (UnicodeEncodeError, OSError) as e:
        result["error"] = f"write failed: {e}"
        return result
    finally:
        _audit(args if isinstance(args, dict) else {}, result,
               tool="sandbox_write")


# One-shot confined probe for sandbox_status — a single spawn answers
# all canaries. 10.0.2.2 is slirp's host alias (maps to host loopback);
# a successful connect means --disable-host-loopback is broken and the
# host's tool surfaces are reachable from inside the sandbox.
_PROBE_SRC = (
    "import json,os,socket\n"
    "r={'writable':os.access('/sbx',os.W_OK),"
    "'host_etc':os.path.exists('/etc/passwd')}\n"
    "try:\n"
    " s=socket.create_connection(('10.0.2.2',8766),3);s.close()\n"
    " r['host_loopback']=True\n"
    "except OSError: r['host_loopback']=False\n"
    "try:\n"
    " socket.setdefaulttimeout(6);socket.gethostbyname('github.com')\n"
    " r['dns']=True\n"
    "except OSError: r['dns']=False\n"
    "print(json.dumps(r))\n"
)


def tool_sandbox_status(_args: dict[str, Any]) -> dict[str, Any]:
    """Report sandbox shape: scratch root, usage, confinement alive."""
    usage = "?"
    try:
        p = subprocess.run(["du", "-sh", str(SCRATCH)],
                           capture_output=True, text=True, timeout=10)
        usage = p.stdout.split()[0] if p.returncode == 0 else "?"
    except Exception:
        usage = "?"
    probes: dict[str, Any] = {}
    net_ok = False
    try:
        rc, out, _ = _run_confined(["python3", "-c", _PROBE_SRC], 30)
        if rc == 0:
            probes = json.loads(out.strip().splitlines()[-1])
            net_ok = True
    except Exception:
        pass
    return {
        "ok": True,
        "scratch": str(SCRATCH),
        "scratch_usage": usage,
        "bwrap": Path(BWRAP).exists(),
        "slirp": Path(SLIRP).exists(),
        "netns": net_ok,
        "scratch_writable": bool(probes.get("writable")),
        "host_fs_hidden": not probes.get("host_etc", True),
        "host_loopback_blocked": not probes.get("host_loopback", True),
        "dns": bool(probes.get("dns")),
        "serve_sessions": len(_SERVE_SESSIONS),
    }


# --- Band 2.5: named serve sessions -------------------------------------
#
# sandbox_serve runs a long-lived confined command whose bound port is
# forwarded to host loopback through slirp's API socket (add_hostfwd).
# The boundary is the same confined netns as exec — the forward is the
# ONLY hole opened, to exactly one loopback port in a bounded range.

_SERVE_LOCK = threading.Lock()
_SERVE_SESSIONS: dict[str, dict[str, Any]] = {}


def _slirp_api(sock_path: Path, payload: dict[str, Any],
               retries: int = 20) -> Any:
    """One JSON-RPC command over slirp's API unix socket."""
    last = ""
    for _ in range(retries):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(str(sock_path))
            s.sendall((json.dumps(payload) + "\n").encode())
            data = b""
            while b"\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            s.close()
            resp = json.loads(data.decode())
            if "error" in resp:
                raise RuntimeError(str(resp["error"]))
            return resp.get("return")
        except (OSError, json.JSONDecodeError) as e:
            last = str(e)
            time.sleep(0.25)
    raise RuntimeError(f"slirp api unreachable: {last}")


def _host_port_open(port: int) -> bool:
    """Bare TCP connect — answers 'does anything listen on this host
    port' for shadow detection. NOT a guest-reachability probe."""
    try:
        s = socket.create_connection(("127.0.0.1", port), 1.5)
        s.close()
        return True
    except OSError:
        return False


def _host_port_answered(port: int) -> bool:
    """True only if a GUEST listener is really behind the forward.

    slirp completes the host-side handshake eagerly and only then
    attempts the guest connect, so a bare connect() is meaningless.
    Probe one exchange: guest dead → the relay RSTs (reset/EOF);
    guest alive → socket.timeout (waiting on our request) or banner
    data. Either 'alive' outcome counts.
    """
    try:
        s = socket.create_connection(("127.0.0.1", port), 2)
    except OSError:
        return False
    try:
        s.settimeout(0.8)
        try:
            return bool(s.recv(64))
        except socket.timeout:
            return True
        except (ConnectionResetError, BrokenPipeError):
            return False
    finally:
        s.close()


def _kill_session(sid: str) -> dict[str, Any] | None:
    s = _SERVE_SESSIONS.pop(sid, None)
    if not s:
        return None
    try:
        if s.get("api_sock") and Path(s["api_sock"]).exists():
            _slirp_api(Path(s["api_sock"]),
                       {"execute": "remove_hostfwd", "arguments": {
                           "proto": "tcp", "host_addr": "127.0.0.1",
                           "host_port": s["port"],
                           "guest_port": s["port"]}}, retries=1)
    except Exception:
        pass
    _reap(s["proc"], s["slirp"])
    try:
        Path(s["api_sock"]).unlink(missing_ok=True)
    except OSError:
        pass
    _audit({"command": s["cmd"]},
           {"ok": True, "event": "serve_stop", "sid": sid},
           tool="sandbox_serve")
    return s


def _sweep_sessions() -> None:
    now = time.time()
    with _SERVE_LOCK:
        for sid, s in list(_SERVE_SESSIONS.items()):
            if s["proc"].poll() is not None or (
                now - s["started"] > s["ttl"]
            ):
                _kill_session(sid)


def tool_sandbox_serve(args: dict[str, Any]) -> dict[str, Any]:
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
        port = int(args.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not (SERVE_PORT_MIN <= port <= SERVE_PORT_MAX):
        return {"ok": False, "error": (
            f"port must be {SERVE_PORT_MIN}-{SERVE_PORT_MAX}: {port}")}
    try:
        ttl = min(int(args.get("ttl") or SERVE_DEFAULT_TTL), SERVE_MAX_TTL)
    except (TypeError, ValueError):
        ttl = SERVE_DEFAULT_TTL
    _sweep_sessions()
    with _SERVE_LOCK:
        if any(s["port"] == port for s in _SERVE_SESSIONS.values()):
            return {"ok": False, "error": f"port {port} already served"}
        if len(_SERVE_SESSIONS) >= SERVE_MAX_SESSIONS:
            return {"ok": False, "error": (
                f"session cap {SERVE_MAX_SESSIONS} reached — "
                "stop one with sandbox_serve_stop")}
    if _host_port_open(port):
        return {"ok": False, "error": (
            f"host port {port} already answers — refusing to shadow "
            "an existing listener")}

    sid = f"s-{int(time.time())}-{secrets.token_hex(2)}"
    api_sock = NET_DIR / f"{sid}.sock"
    try:
        proc, slirp = _spawn_confined(
            cmd, api_sock=api_sock, log_path=NET_DIR / f"{sid}.log")
    except Exception as e:
        return {"ok": False, "error": f"spawn failed: {e}"}
    session = {"proc": proc, "slirp": slirp, "port": port,
               "cmd": " ".join(cmd), "started": time.time(),
               "ttl": ttl, "api_sock": str(api_sock),
               "log": str(NET_DIR / f"{sid}.log")}
    try:
        _slirp_api(api_sock, {"execute": "add_hostfwd", "arguments": {
            "proto": "tcp", "host_addr": "127.0.0.1",
            "host_port": port, "guest_addr": GUEST_ADDR,
            "guest_port": port}})
    except Exception as e:
        _kill_session_after_register(sid, session)
        return {"ok": False, "error": f"hostfwd failed: {e}"}
    with _SERVE_LOCK:
        _SERVE_SESSIONS[sid] = session
    # Readiness = two consecutive guest-answered probes ~0.3s apart —
    # _host_port_answered proves a real guest listener is behind the
    # forward, not just slirp's eager host-side accept.
    deadline = time.time() + 8
    opens = 0
    while time.time() < deadline:
        if proc.poll() is not None:
            _kill_session(sid)
            return {"ok": False, "error":
                    "served command exited — session torn down"}
        opens = opens + 1 if _host_port_answered(port) else 0
        if opens >= 2:
            _audit({"command": session["cmd"]},
                   {"ok": True, "event": "serve_start", "sid": sid,
                    "port": port}, tool="sandbox_serve")
            return {"ok": True, "session_id": sid, "port": port,
                    "url": f"http://127.0.0.1:{port}", "ttl": ttl,
                    "note": "reachable on host loopback only"}
        time.sleep(0.3)
    _kill_session(sid)
    return {"ok": False, "error": (
        "port never answered — the service must bind 0.0.0.0 inside "
        "the sandbox (e.g. python3 -m http.server --bind 0.0.0.0); "
        "127.0.0.1 binds are unreachable through hostfwd")}


def _kill_session_after_register(sid: str,
                                 session: dict[str, Any]) -> None:
    with _SERVE_LOCK:
        _SERVE_SESSIONS[sid] = session
    _kill_session(sid)


def tool_sandbox_serve_status(_args: dict[str, Any]) -> dict[str, Any]:
    _sweep_sessions()
    now = time.time()
    with _SERVE_LOCK:
        sessions = [
            {"session_id": sid, "port": s["port"], "cmd": s["cmd"],
             "age_s": int(now - s["started"]), "ttl": s["ttl"],
             "alive": s["proc"].poll() is None}
            for sid, s in _SERVE_SESSIONS.items()
        ]
    return {"ok": True, "sessions": sessions,
            "port_range": [SERVE_PORT_MIN, SERVE_PORT_MAX],
            "cap": SERVE_MAX_SESSIONS}


def tool_sandbox_serve_stop(args: dict[str, Any]) -> dict[str, Any]:
    sid = str(args.get("session_id") or "")
    if not re.fullmatch(r"s-\d+-[0-9a-f]{4}", sid):
        return {"ok": False, "error": "bad session_id"}
    with _SERVE_LOCK:
        s = _kill_session(sid)
    if not s:
        return {"ok": False, "error": f"no such session: {sid}"}
    return {"ok": True, "stopped": sid, "cmd": s["cmd"]}


TOOLS: dict[str, Any] = {
    "sandbox_exec": tool_sandbox_exec,
    "sandbox_write": tool_sandbox_write,
    "sandbox_serve": tool_sandbox_serve,
    "sandbox_serve_status": tool_sandbox_serve_status,
    "sandbox_serve_stop": tool_sandbox_serve_stop,
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
                    "sandbox, private netns with NAT'd egress; the host's "
                    "loopback services are unreachable from inside). For "
                    "exploratory work: clone a repo, inspect code, run "
                    "tests, scratch computations. Allowed: git "
                    "(clone/status/log/diff/show/branch/checkout/add/commit "
                    "— no push/fetch/pull/remote/config; clone URLs limited "
                    "to github.com, gitlab.com, codeberg.org, git.sr.ht, "
                    "file://), file ops (ls cat grep find head tail wc tree "
                    "mkdir cp mv rm), python3/pytest/pip (venvs stay inside), "
                    "make, sed. There is no shell — redirections like > and "
                    ">> do nothing; write files with sandbox_write (or "
                    "sed -i for edits), and use git -C <dir> <subcommand> "
                    "inside a clone. "
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
        "/sandbox_write": {
            "post": {
                "operationId": "sandbox_write",
                "summary": (
                    "Write a text file inside the sandbox scratch root "
                    "— the preferred way to create files. path is "
                    "relative to the sandbox root (or /sbx/-prefixed); "
                    "parent dirs are created. append=true appends. "
                    "Content is UTF-8 text, max ~128 KiB. Paths outside "
                    "the root, '..', and escaping symlinks are refused."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                    "append": {"type": "boolean"},
                                },
                                "required": ["path", "content"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Write result"}},
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
        "/sandbox_serve": {
            "post": {
                "operationId": "sandbox_serve",
                "summary": (
                    "Start a long-running service INSIDE the sandbox, "
                    "reachable at http://127.0.0.1:<port> on the host "
                    "(forwarded through the sandbox netns — the only "
                    "hole opened). The command MUST bind 0.0.0.0 inside "
                    "— e.g. python3 -m http.server <port> --bind 0.0.0.0; "
                    "a guest-side 127.0.0.1 bind never answers. port must "
                    "be in the serve range (8890-8899); ttl defaults to "
                    "1800s, max 7200s; max 4 concurrent sessions. Use for "
                    "'run it and let me try' work — dev servers, little "
                    "web tools the user should poke in a browser. "
                    "Sessions die with the service; keep them no longer "
                    "than needed and stop with sandbox_serve_stop."
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
                                    "port": {"type": "integer"},
                                    "ttl": {"type": "integer"},
                                },
                                "required": ["command", "port"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Serve session"}},
            }
        },
        "/sandbox_serve_status": {
            "post": {
                "operationId": "sandbox_serve_status",
                "summary": "List live serve sessions (id, port, age, ttl).",
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "responses": {"200": {"description": "Sessions"}},
            }
        },
        "/sandbox_serve_stop": {
            "post": {
                "operationId": "sandbox_serve_stop",
                "summary": "Stop a serve session by session_id.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "session_id": {"type": "string"},
                                },
                                "required": ["session_id"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Stopped"}},
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
        _audit(args, result, tool=tool_name)
        self._send_json(200 if result.get("ok") else 422, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(fmt, *args)


def _sweep_orphan_slirps() -> int:
    """Reap user-owned slirp4netns whose target pid is gone.

    slirp outlives a dead netns and keeps its hostfwd listeners — a
    server crash or test abort would otherwise leave forwarded ports
    bound forever. Our slirps are identifiable: argv ends
    '<target_pid> tap0' and only this service spawns them.
    """
    killed = 0
    uid = str(os.getuid())
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            status = (p / "status").read_text()
            if f"Uid:\t{uid}" not in status:
                continue
            cmd = (p / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if not cmd or Path(cmd[0].decode()).name != "slirp4netns":
            continue
        target = cmd[-2].decode() if len(cmd) >= 2 else ""
        if target.isdigit() and not Path(f"/proc/{target}").exists():
            try:
                os.kill(int(p.name), 15)
                killed += 1
            except OSError:
                pass
    return killed


def main() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    NET_DIR.mkdir(parents=True, exist_ok=True)
    swept = _sweep_orphan_slirps()
    if swept:
        log.warning("reaped %d orphaned slirp4netns from a previous life",
                    swept)
    if not Path(BWRAP).exists():
        log.error("bwrap missing at %s — sandbox cannot start", BWRAP)
        sys.exit(1)
    if not Path(SLIRP).exists():
        # Fail loudly but keep serving /health + sandbox_status for
        # diagnosis — every exec/serve fails closed with a clear error,
        # and the contract canary flags it on the 5-min path.
        log.error("slirp4netns missing at %s — exec will refuse; "
                  "network boundary cannot be established", SLIRP)
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
