"""proposal-tools — Band-3 host-change proposals for agent tasks.

Tools: propose_host_change, list_proposals, proposal_status,
retract_proposal. The model never touches the host file — it stages a
*proposal* (target path + full new content + rationale) that a human
approves from /trust or `agent-approve <id>`. Approval runs the staged
content through guarded-change.sh: snapshot → dead-man timer → apply →
verify → probation → promote/revert. The scaffold's existing envelope
executes; this server only validates and stages.

Trust model (docs/rig-trust-scaffold.md — the envelope is the safety):
- Path allowlist: PROPOSAL_ROOTS only (~/scripts v1). Target must exist,
  resolve inside the root, and not match PROTECTED_RE — the scaffold's
  own machinery is not proposable (a model that can propose weakening
  the deny layer is a priv-esc hole; those files stay human-edited).
- deny.conf overlays the target path AND the proposed content — a
  minted incident citation beats every other layer.
- verify is an ENUM, not free-form: the verify command runs inside
  guarded-change at approval time — model-supplied commands would be
  arbitrary exec gated only by phone-fatigue. Bounded choices only.
- Content ships as full-file replacement the model proved in the
  Band-2 sandbox; the server generates the review diff. Max size,
  pending-cap, and identical-content rejection bound the surface.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import secrets
import stat
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger("proposal-tools")
logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")

PORT = int(os.environ.get("PROPOSAL_TOOLS_PORT", "8775"))
HOST = os.environ.get("PROPOSAL_TOOLS_HOST", "127.0.0.1")
STATE_DIR = Path(
    os.environ.get(
        "PROPOSAL_STATE",
        os.path.expanduser("~/.local/state/proposals"),
    )
).resolve()
AUDIT_LOG = os.environ.get(
    "PROPOSAL_TOOLS_AUDIT_LOG",
    os.path.expanduser("~/.local/share/proposal-tools/audit.jsonl"),
)
RIG_NOTIFY = os.path.expanduser("~/scripts/rig-notify.sh")
NOTIFY_CARD = os.path.expanduser("~/scripts/notify-card.py")
TRUST_URL = os.environ.get("PROPOSAL_TRUST_URL", "")

# Second-opinion judge: aios-decide (:8773) reviews each staged diff —
# typed questions, calibrated answers, an explicit abstain contract.
# ADVISORY ONLY: a down or abstaining judge never blocks staging — the
# structural boundaries (allowlist/protected/verify-enum/deny) are the
# safety; the judge is a second pair of eyes for the human reviewer.
DECIDE_URL = os.environ.get(
    "PROPOSAL_DECIDE_URL", "http://127.0.0.1:8773")
DECIDE_TIMEOUT = float(os.environ.get("PROPOSAL_DECIDE_TIMEOUT", "30"))
JUDGE_DIFF_CHARS = 6500  # decide's 8k input cap minus header budget

# Typed questions over the diff. noul = yes/no/unknown; choice picks a
# label. Every question can abstain — an unsure judge says 'unknown'
# instead of bluffing, which is itself useful signal for the human.
_JUDGE_QUESTIONS = [
    {"id": "fit", "type": "noul",
     "question": "The DIFF implements the stated RATIONALE and "
                "nothing else."},
    {"id": "scope", "type": "noul",
     "question": "The DIFF contains changes beyond the stated fix — "
                "extra edits, unrelated hunks, or deletions the "
                "rationale doesn't explain."},
    {"id": "risk", "type": "choice",
     "options": ["low", "medium", "high"],
     "question": "Overall risk of applying this diff to the file?"},
]

# Proposal targets: existing files under these roots only.
PROPOSAL_ROOTS = [
    Path(p).resolve()
    for p in os.environ.get(
        "PROPOSAL_ROOTS", os.path.expanduser("~/scripts")
    ).split(":")
    if p
]

# The scaffold's own machinery + high-blast-radius scripts are NOT
# proposable — a model steering changes to the enforcement layer is the
# priv-esc path this whole architecture exists to close.
PROTECTED_RE = re.compile(
    r"(rig-notify|rig-guard-status|rig-gpu-clients|rig-invariant|"
    r"rig-health-check|guardrail-self-test|guarded-change|agent-approve|"
    r"agent-promote|agent-deny|agent-pre-mutate|gpu-lane|gpu-exclusive|"
    r"gpu-job-audit|safe-config-edit|notify_send|notify-digest|"
    r"deny\.conf|invariants\.conf|deployment_contract|"
    r"sandbox_tools_server|proposal_tools|fix_tooling|script_panel|"
    r"apt-state-restore|open-webui-backup)"
)
MAX_CONTENT = 262_144          # 256 KiB — scripts are small; big = error
MAX_PENDING = 10               # flood bound — settle before more staging
MAX_RATIONALE = 2000
VERIFY_CHOICES = {"health", "syntax", "pycompile"}

# --- rig deny.conf overlay (canonical parser — mirrors local_tools_server)


def _rig_rules() -> list[tuple[str, str, str]]:
    rules = []
    for line in _load_deny_lines():
        parts = [p.strip() for p in line.split("|", 2)]
        if len(parts) == 3:
            scope, reason, pat = parts
        elif len(parts) == 2:
            scope, reason, pat = parts[0], parts[1], parts[1]
        else:
            continue
        if pat:
            rules.append((pat, scope, reason))
    return rules


def _load_deny_lines() -> list[str]:
    lines = []
    try:
        with open(os.path.expanduser("~/.config/rig/deny.conf")) as f:
            for raw in f:
                line = raw.strip()
                if line and not line.startswith("#"):
                    lines.append(line)
    except OSError:
        pass
    # Floor rules — mirror local_tools_server.py builtins verbatim.
    lines += [
        "exec|I2C/DDC probing caused hard freezes (rig AGENTS)|\\bddcutil\\b|\\bi2cdetect\\b|/dev/i2c",
        "exec|direct I2C access — hard-freeze class|/dev/i2c",
        "exec|firmware/GPU flashing is human-only|amdgpuflash|\\bvbios\\b|\\bnvflash\\b",
    ]
    return lines


def _rig_deny_check(text: str, scope: str) -> dict | None:
    for pat, rule_scope, reason in _rig_rules():
        if rule_scope not in (scope, "any"):
            continue
        try:
            if re.search(pat, text, re.I):
                return {"denied": True, "rule": pat, "reason": reason,
                        "scope": rule_scope}
        except re.error:
            if pat.lower() in text.lower():
                return {"denied": True, "rule": pat, "reason": reason,
                        "scope": rule_scope}
    return None


def _audit(event: str, **fields: Any) -> None:
    rec = {"ts": round(time.time(), 3), "event": event, **fields}
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        log.error("audit write failed: %s", event)


def _journal(line: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "journal.log", "a") as f:
            f.write(f"{int(time.time())}\t{line}\n")
    except OSError:
        pass


def _notify(tag: str, msg: str, url: str = "") -> None:
    argv = [RIG_NOTIFY, tag, "0", msg]
    u = url or TRUST_URL
    if u:
        argv.append(u)
    try:
        subprocess.run(argv, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("rig-notify failed: %s", e)


def _card(*args: str) -> str:
    """Decision-shaped card via notify-card.py — empty string when the
    formatter is missing so notify falls back to a plain line."""
    try:
        r = subprocess.run([NOTIFY_CARD, *args], capture_output=True,
                           text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _decide_post(payload: dict) -> dict:
    """Single seam for the decide call — tests patch this, the network
    never leaks into the unit suite."""
    req = urllib.request.Request(
        DECIDE_URL.rstrip("/") + "/decide",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=DECIDE_TIMEOUT) as r:
        return json.loads(r.read())


def _judge_proposal(target: Path, rationale: str, verify: str,
                    diff: str) -> dict:
    """Second-model review of a staged diff via aios-decide.

    Returns {"status": clean|flagged|abstain|unavailable|disabled,
             "answers": {...}}. Advisory — every failure mode maps to
    unavailable, never an exception. Flagged = any answered question
    came back bad (fit=no, scope=yes, risk=high); abstain = a question
    declined to answer or risk came back medium — the judge can't call
    it low-risk, which for a second opinion is honest uncertainty.
    """
    if not DECIDE_URL:
        return {"status": "disabled"}
    text = (
        f"FILE: {target}\nRATIONALE: {rationale}\n"
        f"POST-APPLY VERIFY: {verify}\nDIFF:\n{diff[:JUDGE_DIFF_CHARS]}"
    )
    try:
        resp = _decide_post({"text": text,
                             "questions": _JUDGE_QUESTIONS})
    except Exception as e:
        log.warning("judge unavailable: %s", e)
        return {"status": "unavailable", "error": str(e)[:120]}

    answers: dict[str, Any] = {}
    flagged, abstained = [], []
    for d in resp.get("decisions", []):
        qid = d.get("id", "?")
        answers[qid] = {"chosen": d.get("chosen"),
                        "prob": round(d.get("prob") or 0.0, 3),
                        "abstained": bool(d.get("abstained"))}
        if d.get("abstained"):
            abstained.append(qid)
            continue
        ch = d.get("chosen")
        if (qid == "fit" and ch == "no") or \
           (qid == "scope" and ch == "yes") or \
           (qid == "risk" and ch == "high"):
            flagged.append(f"{qid}={ch}")
        elif qid == "risk" and ch == "medium":
            abstained.append(qid)
    status = ("flagged" if flagged else
              "abstain" if abstained else "clean")
    return {"status": status, "answers": answers,
            "flagged": flagged, "abstained": abstained,
            "request_id": resp.get("request_id")}


# --- validation ----------------------------------------------------------


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _resolve_target(path_arg: str) -> tuple[Path | None, dict | None]:
    """Validate a proposed target path. Returns (path, error)."""
    if not path_arg or not isinstance(path_arg, str):
        return None, _err("path is required")
    p = Path(path_arg).expanduser()
    if not p.is_absolute():
        p = Path(os.path.expanduser("~")) / p
    try:
        p = p.resolve()
    except (OSError, ValueError):
        return None, _err(f"cannot resolve path: {path_arg}")
    if not any(p == r or r in p.parents for r in PROPOSAL_ROOTS):
        roots = ", ".join(str(r) for r in PROPOSAL_ROOTS)
        return None, _err(
            f"path outside proposal roots ({roots}). Host edits are "
            "limited to the allowlist — propose inside it or don't "
            "propose at all."
        )
    if PROTECTED_RE.search(p.name):
        return None, _err(
            f"{p.name} is part of the trust scaffold / protected set — "
            "the proposal channel can't touch the enforcement layer "
            "itself. Report the issue to the operator instead."
        )
    if not p.exists():
        return None, _err(
            f"{p} does not exist — proposals edit existing files only "
            "(new-file proposals are a later band-3 increment)."
        )
    if not p.is_file():
        return None, _err(f"{p} is not a regular file")
    deny = _rig_deny_check(str(p), "exec")
    if deny:
        return None, {"ok": False, "denied": True, "rig_deny": deny,
                      "error": f"deny.conf: {deny['reason']}"}
    return p, None


def _pending_ids() -> list[str]:
    out = []
    try:
        for d in STATE_DIR.iterdir():
            if d.is_dir() and (d / "meta.json").exists():
                try:
                    meta = json.loads((d / "meta.json").read_text())
                    if meta.get("status") == "pending":
                        out.append(meta["id"])
                except (OSError, json.JSONDecodeError):
                    continue
    except OSError:
        pass
    return sorted(out)


def _load_meta(pid: str) -> tuple[dict | None, dict | None]:
    if not re.fullmatch(r"p-\d+-[a-z0-9]{6}", pid or ""):
        return None, _err(f"bad proposal id: {pid!r}")
    meta_path = STATE_DIR / pid / "meta.json"
    try:
        return json.loads(meta_path.read_text()), None
    except (OSError, json.JSONDecodeError):
        return None, _err(f"no proposal {pid}")


# --- tools ----------------------------------------------------------------


def tool_propose_host_change(args: dict) -> dict:
    path_arg = args.get("path", "")
    content = args.get("content", "")
    rationale = (args.get("rationale") or "").strip()
    verify = (args.get("verify") or "health").strip()
    evidence = (args.get("evidence") or "").strip()[:2000]

    target, err = _resolve_target(path_arg)
    if err:
        _audit("propose-denied", path=path_arg, error=err.get("error"))
        return err

    if not isinstance(content, str) or not content.strip():
        return _err("content is required — the full new file content "
                    "(produce it in the sandbox first; it must be the "
                    "version you verified)")
    if len(content) > MAX_CONTENT:
        return _err(f"content too large ({len(content)} > {MAX_CONTENT})")
    content_deny = _rig_deny_check(content, "exec")
    if content_deny:
        _audit("propose-denied", path=str(target), rule=content_deny["rule"])
        return {"ok": False, "denied": True, "rig_deny": content_deny,
                "error": f"deny.conf in content: {content_deny['reason']}"}
    if not rationale:
        return _err("rationale is required — the human reviews it on "
                    "their phone; say what the change does and why")
    if len(rationale) > MAX_RATIONALE:
        return _err(f"rationale too long ({len(rationale)} > {MAX_RATIONALE})")
    if verify not in VERIFY_CHOICES:
        return _err(
            f"verify must be one of {sorted(VERIFY_CHOICES)} — the verify "
            "command runs at approval time inside the guarded-change "
            "envelope, so it's a bounded enum, not free text. "
            "syntax=bash -n for shell scripts, pycompile for python, "
            "health=rig-health-check otherwise."
        )

    try:
        old = target.read_text(errors="replace")
    except OSError as e:
        return _err(f"cannot read current {target}: {e}")
    if old == content:
        return _err("content is identical to the current file — nothing "
                    "to propose")

    pending = _pending_ids()
    if len(pending) >= MAX_PENDING:
        return _err(f"{len(pending)} proposals already pending — the "
                    "human approves at human speed; wait for settlement "
                    "before proposing more")

    pid = f"p-{int(time.time())}-{secrets.token_hex(3)}"
    pdir = STATE_DIR / pid
    try:
        pdir.mkdir(parents=True, exist_ok=False)
        (pdir / "new-content").write_text(content)
    except OSError as e:
        return _err(f"staging failed: {e}")

    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=f"a/{target.name}", tofile=f"b/{target.name}",
    ))
    try:
        (pdir / "diff.patch").write_text(diff)
    except OSError:
        pass
    adds = sum(1 for l in diff.splitlines()
               if l.startswith("+") and not l.startswith("+++"))
    dels = sum(1 for l in diff.splitlines()
               if l.startswith("-") and not l.startswith("---"))

    # Second opinion: the decide sidecar reads the staged diff before
    # the human's phone does. Advisory — never gates the stage.
    judge = _judge_proposal(target, rationale, verify, diff)

    meta = {
        "id": pid,
        "created": int(time.time()),
        "status": "pending",
        "path": str(target),
        "mode": stat.S_IMODE(target.stat().st_mode),
        "rationale": rationale,
        "evidence": evidence,
        "verify": verify,
        "adds": adds,
        "dels": dels,
        "judge": judge,
        "sha256": hashlib.sha256(content.encode()).hexdigest()[:16],
        "orig_sha256": hashlib.sha256(old.encode()).hexdigest()[:16],
    }
    try:
        (pdir / "meta.json").write_text(json.dumps(meta, indent=2))
    except OSError as e:
        return _err(f"staging meta failed: {e}")

    _journal(f"propose\t{pid}\t{target}\t+{adds}/-{dels}\t"
             f"judge:{judge['status']}\t{rationale[:80]}")
    _audit("propose-staged", id=pid, path=str(target), adds=adds,
           dels=dels, judge=judge["status"])
    home = os.path.expanduser("~")
    card = _card(
        "propose",
        "--file", target.name,
        "--dir", str(target.parent).replace(home, "~", 1),
        "--adds", str(adds), "--dels", str(dels),
        "--judge", judge["status"],
        "--judge-detail", " ".join(judge.get("flagged") or []),
        "--rationale", rationale[:160],
    )
    _notify(
        f"propose-{pid}",
        card or f"[propose] {pid}: {target} (+{adds}/-{dels}) "
                f"judge:{judge['status']} — {rationale[:120]}",
        f"{TRUST_URL}#prop-{pid}" if TRUST_URL else "",
    )
    return {
        "ok": True,
        "id": pid,
        "status": "pending",
        "path": str(target),
        "adds": adds,
        "dels": dels,
        "verify": verify,
        "judge": judge,
        "note": "staged — a human approves or denies it. Do NOT edit the "
                "host file yourself; proposal_status reports the outcome.",
    }


def tool_list_proposals(args: dict) -> dict:
    want = (args.get("status") or "").strip()
    out = []
    try:
        dirs = sorted(
            (d for d in STATE_DIR.iterdir()
             if d.is_dir() and (d / "meta.json").exists()),
            key=lambda d: d.name, reverse=True,
        )[:25]
        for d in dirs:
            try:
                meta = json.loads((d / "meta.json").read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if want and meta.get("status") != want:
                continue
            out.append({
                "id": meta["id"], "status": meta["status"],
                "path": meta["path"], "created": meta["created"],
                "adds": meta.get("adds"), "dels": meta.get("dels"),
                "judge": (meta.get("judge") or {}).get("status"),
                "rationale": meta.get("rationale", "")[:120],
            })
    except OSError:
        pass
    return {"ok": True, "proposals": out, "count": len(out)}


def tool_proposal_status(args: dict) -> dict:
    pid = args.get("id", "")
    meta, err = _load_meta(pid)
    if err:
        return err
    out = {k: meta.get(k) for k in
           ("id", "status", "path", "created", "adds", "dels", "verify",
            "rationale", "guard_id", "settled", "judge")}
    return {"ok": True, "proposal": out}


def tool_retract_proposal(args: dict) -> dict:
    pid = args.get("id", "")
    meta, err = _load_meta(pid)
    if err:
        return err
    if meta.get("status") != "pending":
        return _err(f"{pid} is {meta.get('status')} — only pending "
                    "proposals can be retracted")
    meta["status"] = "retracted"
    meta["settled"] = int(time.time())
    try:
        (STATE_DIR / pid / "meta.json").write_text(json.dumps(meta, indent=2))
    except OSError as e:
        return _err(f"retract failed: {e}")
    _journal(f"retract\t{pid}\t{meta['path']}")
    _audit("propose-retracted", id=pid, path=meta["path"])
    return {"ok": True, "id": pid, "status": "retracted"}


TOOLS = {
    "propose_host_change": tool_propose_host_change,
    "list_proposals": tool_list_proposals,
    "proposal_status": tool_proposal_status,
    "retract_proposal": tool_retract_proposal,
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "propose_host_change",
            "description": (
                "Propose a change to an existing host file under the "
                "proposal allowlist (~/scripts). You supply the FULL new "
                "file content — produce and verify it in the sandbox "
                "first (sandbox_exec), then ship the proven content. The "
                "server generates the review diff; a human approves or "
                "denies it (phone /trust or agent-approve); approval "
                "applies it inside the guarded-change envelope "
                "(snapshot → dead-man revert → verify → probation). You "
                "cannot apply host changes yourself — this tool IS the "
                "only write path to the host. Scaffold/protected files "
                "and paths outside the allowlist are rejected. verify "
                "must be one of: syntax (bash -n, shell scripts), "
                "pycompile (python files), health (rig-health-check, "
                "default for anything else)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Existing file under ~/scripts",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full new file content (the version "
                                       "verified in the sandbox)",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "What the change does and why — "
                                       "reviewed by the human on their "
                                       "phone",
                    },
                    "verify": {
                        "type": "string",
                        "enum": ["health", "syntax", "pycompile"],
                        "description": "Post-apply verification: syntax "
                                       "for .sh, pycompile for .py, "
                                       "health otherwise",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "Optional: the sandbox proof "
                                       "(tests passed, repro output)",
                    },
                },
                "required": ["path", "content", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_proposals",
            "description": "List proposals (optionally filtered by "
                           "status: pending/approved/probation/promoted/"
                           "rejected/retracted/reverted).",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proposal_status",
            "description": "Get one proposal's state and settlement "
                           "outcome by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "p-<ts>-<hex>"},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retract_proposal",
            "description": "Withdraw a pending proposal you made "
                           "(only pending can be retracted).",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                },
                "required": ["id"],
            },
        },
    },
]

OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "proposal-tools", "version": "1.0.0"},
    "paths": {
        "/propose_host_change": {"post": {"operationId":
                                          "propose_host_change"}},
        "/list_proposals": {"post": {"operationId": "list_proposals"}},
        "/proposal_status": {"post": {"operationId": "proposal_status"}},
        "/retract_proposal": {"post": {"operationId": "retract_proposal"}},
    },
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *a: Any) -> None:
        log.info(fmt, *a)

    def _send(self, code: int, obj: Any) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/openapi.json":
            return self._send(200, OPENAPI)
        if self.path == "/health":
            return self._send(200, {
                "ok": True,
                "roots": [str(r) for r in PROPOSAL_ROOTS],
                "pending": len(_pending_ids()),
                "state_writable": os.access(STATE_DIR, os.W_OK)
                if STATE_DIR.exists() else True,
            })
        return self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        tool = self.path.lstrip("/")
        fn = TOOLS.get(tool)
        if not fn:
            return self._send(404, {"error": f"unknown tool {tool}"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            args = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"ok": False, "error": f"bad json: {e}"})
        try:
            result = fn(args if isinstance(args, dict) else {})
        except Exception as e:  # fail closed, honestly
            log.exception("tool %s crashed", tool)
            _audit("tool-crash", tool=tool, error=str(e))
            return self._send(500, {"ok": False,
                                    "error": f"internal: {e}"})
        return self._send(200, result)


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    srv = HTTPServer((HOST, PORT), Handler)
    log.info("proposal-tools on %s:%d roots=%s", HOST, PORT,
             [str(r) for r in PROPOSAL_ROOTS])
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
