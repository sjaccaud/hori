#!/usr/bin/env python3
"""script-panel — web control panel for the accumulated script library.

Single-file, stdlib only, no external dependencies. Serves a sortable
catalog UI plus a gated "run" endpoint for scripts explicitly marked
``runnable: true`` in catalog.json.

Endpoints:
  GET  /                  HTML UI (with guardrail posture strip)
  GET  /trust             trust console — guardrail posture + approve buttons
  GET  /api/catalog       catalog entries + live status + last-run merged
  GET  /api/runs          recent run history (tail of runs.jsonl)
  GET  /api/trust         guardrail posture JSON (denylist/guards/probation)
  POST /api/run/<id>      execute an allowlisted script, record the result
  POST /api/guard/<act>?gid=  approve/deny a LIVE guard unit (promote|revert)

Safety model (mirrors local_tools_server.py):
  - Binds 127.0.0.1 only; tailnet exposure is via `tailscale serve` —
    every tailnet device is the owner's (ACL decision 2026-09-23).
  - No shell: scripts execute as argv arrays; no user-supplied args.
  - Only catalog entries with runnable:true can execute; sudo-flagged
    entries are never runnable from the panel regardless.
  - Guard actions only touch LIVE agent-guard-* units, verified server-side
    before dispatch — stale notification links and id sprays settle to
    "already settled", never act on arbitrary units.
  - confirm:true entries still execute on request — the *UI* shows the
    confirm dialog; the API just runs.
  - Every launch is appended to ~/.local/share/script-panel/runs.jsonl.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
CATALOG_PATH = Path(os.environ.get("SCRIPT_PANEL_CATALOG", BASE / "catalog.json"))

HOST = os.environ.get("SCRIPT_PANEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("SCRIPT_PANEL_PORT", "8770"))
MAX_OUTPUT = int(os.environ.get("SCRIPT_PANEL_MAX_OUTPUT", "50000"))
DEFAULT_TIMEOUT = int(os.environ.get("SCRIPT_PANEL_TIMEOUT", "120"))
STATE_DIR = Path(os.environ.get(
    "SCRIPT_PANEL_STATE_DIR",
    os.path.expanduser("~/.local/share/script-panel"),
))
RUNS_LOG = STATE_DIR / "runs.jsonl"
STATUS_TTL = 20  # seconds; probes are cached this long

# Directories scanned for auto-discovery. New .sh/.py files not already in
# the catalog become 'inbox' stub entries (runnable=false — always safe).
# Globs stay shallow on purpose: recursing into services/*/ or llama-builds/*
# would sweep up package source and test files, not just ops scripts.
# (root, origin label, glob patterns)
# /opt/scripts is deliberately absent: it mirrors ~/scripts (deployed
# copies), so scanning it would only produce duplicate stubs.
SCAN_DIRS = [
    ("~/scripts", "~/scripts", ("*.sh", "*.py")),
    ("~/.local/bin", "~/.local/bin", ("*.sh", "*.py")),
    ("~/Games/Heroic/Scripts", "Heroic scripts", ("*.sh", "*.py")),
    ("~/llama-builds", "llama-builds", ("*/*.sh", "*/*.py")),
    ("~/Projects/aios/scripts", "aios/scripts", ("*.sh", "*.py")),
    ("~/Projects/aios/scripts/hardening", "aios/hardening", ("*.sh", "*.py")),
    ("~/Projects/aios/services", "aios services", ("*/*.sh", "*/*.py")),
]
SCAN_SKIP_PREFIXES = ("test_", "conftest")
SCAN_SKIP_NAMES = {"__init__.py"}


def _looks_like_script(f: Path) -> bool:
    """Only count files that are actually invocable as scripts: *.sh always;
    *.py only when executable or carrying a shebang — otherwise it's a
    package module (config.py, intent.py), not an ops script."""
    if f.suffix == ".sh":
        return True
    if f.suffix != ".py":
        return False
    try:
        if f.stat().st_mode & 0o111:
            return True
        with open(f, "rb") as fh:
            return fh.readline().startswith(b"#!")
    except OSError:
        return False


def load_catalog() -> dict:
    with open(CATALOG_PATH) as f:
        cat = json.load(f)
    cat["scripts"] = cat.get("scripts", []) + discover_stubs(cat["scripts"])
    return cat


def _first_comment_lines(path: Path, max_lines: int = 2) -> str:
    """Pull the leading comment/docstring lines as a one-line 'what' guess."""
    out = []
    try:
        with open(path, errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("#!") or not line:
                    continue
                if line.startswith("#"):
                    line = line.lstrip("#").strip()
                elif line.startswith('"""') or line.startswith("'''"):
                    line = line.strip("\"'").strip()
                else:
                    break
                if line and not line.startswith("!"):
                    out.append(line)
                if len(out) >= max_lines:
                    break
    except OSError:
        pass
    return " ".join(out)[:200]


def discover_stubs(existing: list[dict]) -> list[dict]:
    """Find script files on disk that aren't cataloged yet; return stub
    entries. Dedupes by expanded absolute path — a real catalog entry for
    the same path always wins over the stub."""
    known = {os.path.abspath(expand(e.get("path", ""))) for e in existing}
    stubs = []
    for root, origin, patterns in SCAN_DIRS:
        root_p = Path(expand(root))
        if not root_p.is_dir():
            continue
        files = set()
        for pat in patterns:
            files.update(f for f in root_p.glob(pat) if f.is_file())
        for f in sorted(files):
            if f.name.startswith(SCAN_SKIP_PREFIXES) or f.name in SCAN_SKIP_NAMES:
                continue
            if not _looks_like_script(f):
                continue
            ap = os.path.abspath(f)
            if ap in known:
                continue
            sid = "auto-" + f.stem.lower().replace("_", "-")
            if any(s["id"] == sid for s in existing) or any(s["id"] == sid for s in stubs):
                continue
            home = os.path.expanduser("~")
            rel = "~" + ap[len(home):] if ap.startswith(home) else ap
            stubs.append({
                "id": sid,
                "name": f.name,
                "path": rel,
                "category": "inbox",
                "what": _first_comment_lines(f) or "(auto-discovered — no header comment)",
                "why": "",
                "created": time.strftime("%Y-%m-%d", time.localtime(f.stat().st_mtime)),
                "kind": "script",
                "runnable": False,
                "auto": True,
                "notes": f"Auto-discovered in {origin}; copy into catalog.json and enrich to curate.",
                "tags": ["auto"],
            })
    return stubs


# --------------------------------------------------------------------------
# Status probes
# --------------------------------------------------------------------------

def _run_probe(argv: list[str], timeout: int = 8) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        cp = subprocess.CompletedProcess(argv, 127, "", str(e))
        return cp


def probe_status(probe: dict | None) -> dict:
    """Return {'state': 'online'|'offline'|'unknown'|'n/a', 'detail': str}."""
    if not probe:
        return {"state": "n/a", "detail": ""}
    ptype = probe.get("type")

    if ptype == "systemd":
        unit, scope = probe["unit"], probe.get("scope", "system")
        cmd = ["systemctl"] + (["--user"] if scope == "user" else [])
        active = _run_probe(cmd + ["is-active", unit])
        state = active.stdout.strip()
        enabled = _run_probe(cmd + ["is-enabled", unit]).stdout.strip()
        online = state == "active"
        return {
            "state": "online" if online else ("offline" if state in ("inactive", "failed") else "unknown"),
            "detail": f"{unit}: {state or 'not found'} / {enabled or '?'}",
        }

    if ptype == "timer":
        unit, scope = probe["unit"], probe.get("scope", "system")
        cmd = ["systemctl"] + (["--user"] if scope == "user" else [])
        cp = _run_probe(cmd + ["list-timers", "--all", unit, "--no-legend", "--no-pager"])
        line = cp.stdout.strip().splitlines()
        if line:
            parts = "  ".join(line[0].split())
            # Inactive timers show '-' for NEXT; a schedule means armed.
            if line[0].split()[0] == "-":
                return {"state": "offline", "detail": f"{unit}: loaded but INACTIVE — not scheduled"}
            # NEXT column is e.g. 'Tue 2026-09-08 02:00:00 EDT' — keep the
            # sortable tail (date+time) for the Scheduled column.
            toks = line[0].split()
            next_run = " ".join(toks[1:4]) if len(toks) >= 4 else ""
            return {"state": "online", "detail": parts[:140], "next": next_run}
        enabled = _run_probe(cmd + ["is-enabled", unit]).stdout.strip()
        if enabled:
            return {"state": "offline", "detail": f"{unit}: {enabled}, not in timer list"}
        return {"state": "unknown", "detail": f"{unit}: no timer entry"}

    if ptype == "docker":
        cp = _run_probe(["docker", "inspect", "-f", "{{.State.Status}}", probe["container"]])
        status = cp.stdout.strip()
        return {
            "state": "online" if status == "running" else ("offline" if status else "unknown"),
            "detail": f"{probe['container']}: {status or cp.stderr.strip() or 'not found'}",
        }

    if ptype == "port":
        import socket
        try:
            with socket.create_connection(("127.0.0.1", int(probe["port"])), timeout=2):
                return {"state": "online", "detail": f"127.0.0.1:{probe['port']} accepting"}
        except OSError as e:
            return {"state": "offline", "detail": f"127.0.0.1:{probe['port']}: {e.strerror or e}"}

    return {"state": "unknown", "detail": f"unhandled probe type {ptype!r}"}


# --------------------------------------------------------------------------
# Run log
# --------------------------------------------------------------------------

def _append_run(record: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUNS_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_runs(limit: int = 200) -> list[dict]:
    if not RUNS_LOG.exists():
        return []
    lines = RUNS_LOG.read_text().strip().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def last_run_by_id() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for r in load_runs(500):
        latest[r["id"]] = r
    return latest


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def interpreter_for(entry: dict) -> list[str]:
    interp = entry.get("interpreter")
    if interp:
        return [interp]
    if entry["path"].endswith(".sh"):
        return ["bash"]
    if entry["path"].endswith(".py"):
        return ["python3"]
    return []


def expand(path: str) -> str:
    """Catalog paths use ~ for portability (the repo's PII hook blocks
    literal home paths)."""
    return os.path.expanduser(path)


def run_script(entry: dict) -> dict:
    argv = interpreter_for(entry) + [expand(entry["path"])] + entry.get("args", [])
    env = dict(os.environ)
    env.update(entry.get("env", {}))
    timeout = int(entry.get("timeout", DEFAULT_TIMEOUT))
    started = time.time()
    try:
        cp = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=env,
            cwd=expand(entry["cwd"]) if entry.get("cwd") else None,
        )
        record = {
            "id": entry["id"],
            "ts": int(started),
            "exit": cp.returncode,
            "duration_s": round(time.time() - started, 1),
            "stdout": cp.stdout[-MAX_OUTPUT:],
            "stderr": cp.stderr[-MAX_OUTPUT:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        record = {
            "id": entry["id"],
            "ts": int(started),
            "exit": None,
            "duration_s": round(time.time() - started, 1),
            "stdout": (e.stdout or "")[-MAX_OUTPUT:] if isinstance(e.stdout, str) else "",
            "stderr": f"TIMED OUT after {timeout}s\n" + ((e.stderr or "")[-MAX_OUTPUT:] if isinstance(e.stderr, str) else ""),
            "timed_out": True,
        }
    except OSError as e:
        record = {
            "id": entry["id"], "ts": int(started), "exit": 127,
            "duration_s": 0.0, "stdout": "", "stderr": str(e), "timed_out": False,
        }
    _append_run(record)
    return record


# --------------------------------------------------------------------------
# Trust console — read-only posture for the rig scaffold (denylist, armed
# revert guards, probationary changes, invariants, deny hits, snapshots).
# Mirrors rig-guard-status.sh; all data is read from the same state files.
# --------------------------------------------------------------------------

RIG_STATE = Path(os.path.expanduser("~/.local/state/rig-guard"))
PROPOSAL_STATE = Path(
    os.path.expanduser("~/.local/state/proposals"))
RIG_DENY_CONF = Path(os.path.expanduser("~/.config/rig/deny.conf"))
RIG_SCRIPTS = Path(os.path.expanduser("~/scripts"))


def _journal_tag(tag: str, since: str = "-7 days", limit: int = 25) -> list[dict]:
    """Recent journal entries for a syslog tag, oldest→newest."""
    cp = _run_probe(["journalctl", "-t", tag, "--since", since,
                     "-n", str(limit), "-o", "json", "--no-pager"], timeout=10)
    out = []
    for line in cp.stdout.splitlines():
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            ts = int(j.get("__REALTIME_TIMESTAMP", "0")) // 1_000_000
        except (TypeError, ValueError):
            ts = 0
        out.append({"ts": ts, "msg": str(j.get("MESSAGE", ""))})
    return out


def trust_posture() -> dict:
    deny_rules = 0
    try:
        deny_rules = sum(
            1 for l in RIG_DENY_CONF.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#"))
    except OSError:
        pass

    guards = []
    cp = _run_probe(["systemctl", "list-timers", "--all", "agent-guard-*",
                     "--no-legend", "--no-pager"])
    for line in cp.stdout.splitlines():
        toks = line.split()
        timer = next((t for t in toks if t.endswith(".timer")), "")
        if timer:
            guards.append({"unit": timer, "fires": " ".join(toks[1:4])})

    promoted: set[str] = set()
    try:
        for l in (RIG_STATE / "promotions.log").read_text().splitlines():
            if " PROMOTED " in l:
                promoted.add(l.split(" PROMOTED ", 1)[1].strip())
    except OSError:
        pass
    probation = []
    deadlines = {}
    try:
        for l in (RIG_STATE / "prob-deadlines.log").read_text().splitlines():
            toks = l.split()
            if len(toks) >= 2:
                deadlines[toks[0]] = int(toks[1])
    except OSError:
        pass
    try:
        for gid in (RIG_STATE / "probationary.log").read_text().splitlines():
            gid = gid.strip()
            if not gid:
                continue
            state = "promoted" if gid in promoted else (
                "pending" if _run_probe(
                    ["systemctl", "is-active",
                     f"agent-guard-{gid}-prob.timer"]
                ).stdout.strip() == "active" else "expired/reverted")
            entry = {"gid": gid, "state": state,
                     "deadline": deadlines.get(gid)}
            # human label: the proposal this guard came from, if any
            meta_path = PROPOSAL_STATE.glob("*/meta.json")
            for mp in meta_path:
                try:
                    m = json.loads(mp.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                if m.get("guard_id") == gid:
                    entry["path"] = m.get("path")
                    entry["label"] = os.path.basename(m.get("path", ""))
                    break
            probation.append(entry)
    except OSError:
        pass

    inv = _run_probe([str(RIG_SCRIPTS / "rig-invariant-check.sh")], timeout=30)
    inv_checks = [l.strip() for l in inv.stdout.splitlines() if l.strip()]

    snapshots = []
    try:
        for l in (RIG_STATE / "snapshots.log").read_text().splitlines()[-8:]:
            try:
                snapshots.append(json.loads(l))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass

    deny_hits = _journal_tag("agent-deny")
    inv_fails = [j for j in _journal_tag("rig-invariant") if "FAIL" in j["msg"]]
    pending = [p for p in probation if p["state"] == "pending"]

    proposals = load_proposals()
    prop_pending = [p for p in proposals
                    if p["status"] == "pending"
                    and p.get("origin") not in SYNTHETIC_ORIGINS]
    judge = judge_agreement(
        proposals, {p["gid"]: p["state"] for p in probation})

    return {
        "deny_rules": deny_rules,
        "guards_armed": guards,
        "probation": probation,
        "probation_pending": len(pending),
        "proposals": proposals,
        "judge": judge,
        "proposals_pending": len(prop_pending),
        "invariants": {"rc": inv.returncode, "checks": inv_checks},
        "snapshots": snapshots,
        "deny_hits": deny_hits,
        "invariant_failures": inv_fails,
        "verdict": "PASS" if inv.returncode == 0 else "FAIL",
        "warnings": len(guards) + len(pending) + len(inv_fails),
    }


_GID_RE = re.compile(r"g\d+-\d+")
_PID_RE = re.compile(r"p-\d+-[a-z0-9]{6}")
_PROP_DIFF_LINES = 120
# Proposals tagged by synthetic callers (the eval harness marks
# fixtures "eval") are measurement artifacts, not human decisions:
# excluded from the open-decision count, the agreement stats, and the
# phone surface — they render under a collapsed /trust section.
SYNTHETIC_ORIGINS = frozenset({"eval"})


def judge_agreement(proposals: list[dict],
                    probation_states: dict) -> dict:
    """Correlate judge verdicts with human outcomes — is the second
    opinion measuring anything?

    Outcome join: rejected/expired-reverted = negative (the change
    died), promoted = positive (approved and kept). pending /
    probation-pending / retracted (model-side) are unsettled — only
    human verdicts count. Abstain/unavailable/disabled verdicts carry
    no signal either way.

    Cells: clean+positive or flagged+negative = agreed;
    clean+negative = missed (judge said fine, it wasn't);
    flagged+positive = cried_wolf (judge balked, human kept it).
    """
    out = {"judged": 0, "settled": 0, "agreed": 0, "missed": 0,
           "cried_wolf": 0, "agreement": None, "cells": []}
    for p in proposals:
        if p.get("origin") in SYNTHETIC_ORIGINS:
            continue                     # eval fixtures aren't ground truth
        js = (p.get("judge") or {}).get("status")
        if js not in ("clean", "flagged"):
            continue
        out["judged"] += 1
        outcome = None
        status = p.get("status")
        if status == "rejected":
            outcome = "negative"
        elif status in ("probation", "approved"):
            st = probation_states.get(p.get("guard_id"), "")
            if st == "promoted":
                outcome = "positive"
            elif st and st != "pending":
                outcome = "negative"
            # pending timer / unknown guard -> still unsettled
        if outcome is None:
            continue
        out["settled"] += 1
        cell = {"id": p["id"], "judge": js, "outcome": outcome}
        if (js == "clean") == (outcome == "positive"):
            out["agreed"] += 1
            cell["verdict"] = "agreed"
        elif js == "clean":
            out["missed"] += 1
            cell["verdict"] = "missed"
        else:
            out["cried_wolf"] += 1
            cell["verdict"] = "cried_wolf"
        out["cells"].append(cell)
    if out["settled"]:
        out["agreement"] = round(100 * out["agreed"] / out["settled"])
    return out


def load_proposals() -> list[dict]:
    """Pending/settled proposals from the proposal-tools state dir —
    Band-3 staged host changes awaiting human approve/deny."""
    out = []
    try:
        dirs = sorted(
            (d for d in PROPOSAL_STATE.iterdir()
             if d.is_dir() and (d / "meta.json").exists()),
            key=lambda d: d.name, reverse=True)[:15]
    except OSError:
        return out
    for d in dirs:
        try:
            meta = json.loads((d / "meta.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        entry = {k: meta.get(k) for k in
                 ("id", "status", "path", "created", "adds", "dels",
                  "rationale", "summary", "evidence", "verify",
                  "guard_id", "judge", "origin", "fail_detail",
                  "settled")}
        if meta.get("status") == "pending":
            try:
                diff = (d / "diff.patch").read_text().splitlines()
                entry["diff"] = "\n".join(diff[:_PROP_DIFF_LINES])
                entry["diff_truncated"] = len(diff) > _PROP_DIFF_LINES
            except OSError:
                entry["diff"] = ""
        out.append(entry)
    return out


def proposal_action(pid: str, action: str) -> dict:
    """Human approve/deny on a PENDING proposal (Band-3 approve-from-phone).

    Safety model mirrors guard_action: the id must match the strict
    proposal-id shape AND have a pending meta on disk — sprayed ids
    can't act on anything. approve runs agent-approve.sh, which feeds
    the staged content through guarded-change (the scaffold's own
    envelope — snapshot, dead-man revert, probation). deny marks it
    rejected. Neither path touches mutation code beyond the scaffold's.
    """
    if action not in ("approve", "deny"):
        return {"ok": False, "error": f"unknown action {action!r}"}
    if not pid or not _PID_RE.fullmatch(pid):
        return {"ok": False, "error": "bad proposal id"}
    meta_path = PROPOSAL_STATE / pid / "meta.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": f"no proposal {pid}"}
    if meta.get("status") != "pending":
        return {"ok": False,
                "error": f"{pid} is {meta.get('status')} — not pending"}

    if action == "deny":
        cp = _run_probe(
            [str(RIG_SCRIPTS / "agent-approve.sh"), "--reject", pid],
            timeout=15)
    else:
        # approve → guarded-change: restic snapshot + apply + verify +
        # probation arm; this takes tens of seconds, not milliseconds.
        cp = _run_probe(
            [str(RIG_SCRIPTS / "agent-approve.sh"), pid], timeout=180)
    ok = cp.returncode == 0
    detail = (cp.stdout or cp.stderr).strip()[-300:]

    sys.stderr.write(
        f"[proposal-action] {action} {pid} -> {'ok' if ok else 'FAIL'} {detail[:120]}\n")
    _run_probe(["logger", "-t", "proposal-action",
                f"{action} {pid} ok={ok} :: {detail[:120]}"])
    return {"ok": ok, "action": action, "id": pid, "detail": detail}


def guard_action(gid: str, action: str) -> dict:
    """Human approve/deny on a LIVE guard unit (Phase 4.5 approve-from-phone).

    Safety model: the gid must match the strict guard-id shape AND have a
    live systemd unit — a stale notification or sprayed id can't act on
    anything. promote disarms the probation timer (change stays); revert
    fires the armed revert payload NOW by starting its sibling .service.
    Both are the scaffold's own mechanisms, not new mutation code.
    """
    if action not in ("promote", "revert"):
        return {"ok": False, "error": f"unknown action {action!r}"}
    if not gid or not _GID_RE.fullmatch(gid):
        return {"ok": False, "error": "bad guard id"}

    prob_active = _run_probe(
        ["systemctl", "is-active", f"agent-guard-{gid}-prob.timer"]
    ).stdout.strip() == "active"
    ttl_active = _run_probe(
        ["systemctl", "is-active", f"agent-guard-{gid}.timer"]
    ).stdout.strip() == "active"
    if not (prob_active or ttl_active):
        return {"ok": False, "error": f"no live guard for {gid} — already settled"}

    if action == "promote":
        if not prob_active:
            return {"ok": False, "error": f"{gid} is armed, not in probation"}
        cp = _run_probe([str(RIG_SCRIPTS / "agent-promote.sh"), gid], timeout=20)
        ok = cp.returncode == 0
        detail = (cp.stdout or cp.stderr).strip()[:200]
    else:  # revert — fire the armed payload now, then disarm the pending
        # timer so it doesn't fire a second revert. Order matters: stopping
        # the timer first garbage-collects the sibling .service.
        timer = (f"agent-guard-{gid}-prob.timer" if prob_active
                 else f"agent-guard-{gid}.timer")
        unit = timer[:-len(".timer")] + ".service"
        cp = _run_probe(["sudo", "-n", "systemctl", "start", unit], timeout=20)
        _run_probe(["sudo", "-n", "systemctl", "stop", timer], timeout=10)
        ok = cp.returncode == 0
        detail = f"fired {unit}" if ok else (cp.stderr or cp.stdout).strip()[:200]

    sys.stderr.write(
        f"[guard-action] {action} {gid} -> {'ok' if ok else 'FAIL'} {detail}\n")
    _run_probe(["logger", "-t", "guard-action",
                    f"{action} {gid} ok={ok} :: {detail[:120]}"])
    return {"ok": ok, "action": action, "gid": gid, "detail": detail}


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

_status_cache: dict = {"ts": 0.0, "data": {}}
_status_lock = threading.Lock()


def catalog_with_status() -> dict:
    cat = load_catalog()
    with _status_lock:
        if time.time() - _status_cache["ts"] > STATUS_TTL:
            _status_cache["data"] = {}
            _status_cache["ts"] = time.time()
        cache = _status_cache["data"]

    last = last_run_by_id()
    entries = []
    for e in cat["scripts"]:
        eid = e["id"]
        if eid not in cache:
            cache[eid] = probe_status(e.get("probe"))
        lr = last.get(eid)
        entries.append({
            **e,
            "exists": Path(expand(e["path"])).exists() if "/" in e.get("path", "") or e.get("path", "").startswith("~") else None,
            "status": cache[eid],
            "next_run": cache[eid].get("next", ""),
            "last_run": {
                "ts": lr["ts"], "exit": lr["exit"], "duration_s": lr["duration_s"],
            } if lr else None,
        })
    return {"categories": cat.get("categories", {}), "scripts": entries, "note": cat.get("note", "")}


def catalog_fresh() -> dict:
    """Manual refresh path: bypass the probe-status cache so a clicked
    'refresh' actually re-checks service/timer state."""
    with _status_lock:
        _status_cache["ts"] = 0.0
    return catalog_with_status()


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Script Panel</title>
<style>
:root{--bg:#0a0a0b;--panel:#121214;--border:#232327;--fg:#e6e6e8;--dim:#9a9aa2;--acc:#4f9cf9;--ok:#3fb950;--bad:#f85149;--warn:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;padding:20px}
h1{font-size:17px;font-weight:600;margin:0 0 4px}p.sub{color:var(--dim);margin:0 0 14px;font-size:12px}
#toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
input[type=search]{background:var(--panel);border:1px solid var(--border);color:var(--fg);padding:6px 10px;border-radius:6px;font:inherit;font-size:13px;min-width:220px}
.chip{background:var(--panel);border:1px solid var(--border);color:var(--dim);padding:5px 10px;border-radius:14px;cursor:pointer;font-size:12px}
.chip.on{color:var(--fg);border-color:var(--acc)}
button{border:0;border-radius:6px;cursor:pointer;font:inherit}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden}
th{position:sticky;top:0;background:#17171a;text-align:left;padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:var(--fg)}td{padding:8px 10px;border-top:1px solid var(--border);vertical-align:top;font-size:13px}
tr[data-id]{cursor:pointer}tr[data-id]:hover td{background:#17171b}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:baseline}
.dot.online{background:var(--ok)}.dot.offline{background:var(--bad)}.dot.unknown{background:var(--warn)}.dot.na{background:#3a3a40}
.name{font-weight:600;white-space:nowrap}.alias{color:var(--dim);font-size:11px}
.desc{max-width:420px}.why{color:var(--dim);max-width:340px;font-size:12px}
.path{color:var(--dim);font-size:11px;word-break:break-all;max-width:260px}
.runbtn{background:#1d2a3a;color:var(--acc);border:1px solid #2c4a6b;padding:4px 12px;font-size:12px}
.runbtn:hover{background:#24405e}.runbtn:disabled{opacity:.4;cursor:not-allowed}
.norun{color:#4a4a52;font-size:12px}
.badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:10px;border:1px solid var(--border);color:var(--dim)}
#drawer{position:fixed;right:0;top:0;bottom:0;width:min(560px,92vw);background:var(--panel);border-left:1px solid var(--border);padding:18px;overflow:auto;display:none;z-index:10}
#drawer.open{display:block}#drawer h2{font-size:15px;margin:0 0 2px}
#drawer .meta{color:var(--dim);font-size:12px;margin-bottom:12px}
.kv{display:grid;grid-template-columns:88px 1fr;gap:4px 10px;font-size:12px;margin-bottom:14px}
.kv div:nth-child(odd){color:var(--dim)}
pre{background:#0c0c0e;border:1px solid var(--border);border-radius:6px;padding:10px;font-size:11.5px;white-space:pre-wrap;word-break:break-word;max-height:320px;overflow:auto}
#toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:#17171a;border:1px solid var(--border);padding:8px 16px;border-radius:8px;font-size:12px;display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.spin{display:inline-block;animation:spin .7s linear infinite}
tr.missing td{opacity:.45}
</style></head><body>
<h1>Script Panel</h1>
<p class="sub" id="subnote"></p>
<div id="trustbar" style="margin-bottom:12px;font-size:12px"></div>
<div id="toolbar">
  <input type="search" id="q" placeholder="filter…">
  <span id="chips"></span>
  <span style="flex:1"></span>
  <span id="refreshed" style="color:var(--dim);font-size:11px"></span>
  <button class="chip" id="refresh">↻ refresh</button>
</div>
<table><thead><tr>
<th data-k="status">●</th><th data-k="name">Script</th><th data-k="category">Category</th>
<th data-k="what">What</th><th data-k="why">Why</th><th data-k="created">Created</th>
<th data-k="schedule">Scheduled</th><th data-k="last_run">Last run</th><th></th>
</tr></thead><tbody id="rows"></tbody></table>
<div id="drawer"></div><div id="toast"></div>
<script>
let DATA=[], CAT={}, sortKey='category', sortDir=1, filterCat=null, onlyRunnable=false, search='';
const stateOrder={online:0,offline:1,unknown:2,'n/a':3};
async function load(bust){
  const btn=document.getElementById('refresh'), stamp=document.getElementById('refreshed');
  btn.disabled=true; btn.classList.add('on'); btn.innerHTML='<span class="spin">⟳</span> refreshing…';
  try{
    const r=await fetch('/api/catalog'+(bust?'?bust=1':'')); const d=await r.json();
    DATA=d.scripts; CAT=d.categories||{};
    document.getElementById('subnote').textContent=d.note||'';
    renderChips(); renderRows();
    stamp.textContent='updated '+new Date().toLocaleTimeString()+' · '+DATA.length+' entries';
  }catch(e){stamp.textContent='refresh failed: '+e;}
  finally{btn.disabled=false;btn.classList.remove('on');btn.innerHTML='↻ refresh';}
}
function renderChips(){
  const c=document.getElementById('chips'); c.innerHTML='';
  const cats=[...new Set(DATA.map(s=>s.category))];
  const nr=DATA.filter(s=>s.runnable).length;
  const rb=document.createElement('button'); rb.className='chip'+(onlyRunnable?' on':'');
  rb.textContent='▶ runnable ('+nr+')';
  rb.onclick=()=>{onlyRunnable=!onlyRunnable;renderChips();renderRows()}; c.appendChild(rb);
  const all=document.createElement('button'); all.className='chip'+(filterCat?'':' on');
  all.textContent='all ('+DATA.length+')'; all.onclick=()=>{filterCat=null;renderChips();renderRows()}; c.appendChild(all);
  for(const k of cats){const b=document.createElement('button');b.className='chip'+(filterCat===k?' on':'');
    b.textContent=k+' ('+DATA.filter(s=>s.category===k).length+')';
    b.onclick=()=>{filterCat=filterCat===k?null:k;renderChips();renderRows()};c.appendChild(b);}
}
function fmtTs(ts){if(!ts)return '—';const d=new Date(ts*1000);return d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
function val(s,k){
  if(k==='status')return stateOrder[s.status?.state??'n/a']??9;
  if(k==='last_run')return s.last_run?s.last_run.ts:0;
  if(k==='schedule')return s.next_run||s.schedule||'';
  if(k==='category')return s.category;
  return (s[k]||'').toLowerCase();
}
function renderRows(){
  const tb=document.getElementById('rows'); tb.innerHTML='';
  let rows=DATA.filter(s=>(!filterCat||s.category===filterCat)&&(!onlyRunnable||s.runnable)&&
    (!search||JSON.stringify(s).toLowerCase().includes(search)));
  rows.sort((a,b)=>{const x=val(a,sortKey),y=val(b,sortKey);return (x<y?-1:x>y?1:0)*sortDir;});
  for(const s of rows){
    const tr=document.createElement('tr'); tr.dataset.id=s.id;
    if(s.exists===false)tr.className='missing';
    const st=s.status||{state:'n/a'};
    const lr=s.last_run;
    tr.innerHTML=`<td title="${esc(st.detail||'')}"><span class="dot ${st.state==='n/a'?'na':st.state}"></span></td>
      <td><div class="name">${esc(s.name)}${s.auto?' <span class="badge" style="border-color:var(--warn);color:var(--warn)">new</span>':''}</div><div class="alias">${esc(s.id)}${s.kind?' · '+s.kind:''}</div></td>
      <td><span class="badge">${esc(s.category)}</span></td>
      <td class="desc">${esc(s.what)}</td>
      <td class="why">${esc(s.why||'')}</td>
      <td style="white-space:nowrap;color:var(--dim)">${esc(s.created||'—')}</td>
      <td style="white-space:nowrap">${s.schedule?esc(s.schedule)+(s.next_run?'<div class="alias">next '+esc(s.next_run)+'</div>':''):'<span class="norun">—</span>'}</td>
      <td style="white-space:nowrap;color:var(--dim)">${lr?fmtTs(lr.ts)+(lr.exit!=null?' · exit '+lr.exit:''):'—'}</td>
      <td></td>`;
    const td=tr.lastElementChild;
    if(s.runnable){
      const b=document.createElement('button');b.className='runbtn';b.textContent='▶ run';
      b.onclick=ev=>{ev.stopPropagation();
        if(s.confirm&&!confirm(s.confirm_text||('Run '+s.name+'?')))return;
        b.disabled=true;b.textContent='…';
        fetch('/api/run/'+encodeURIComponent(s.id),{method:'POST'}).then(r=>r.json())
          .then(()=>{toast('finished '+s.id);load();})
          .catch(e=>toast('error: '+e)).finally(()=>{b.disabled=false;b.textContent='▶ run'});
      };
      td.appendChild(b);
    }else{td.innerHTML='<span class="norun" title="'+(esc(s.notes||'not runnable from panel'))+'">—</span>';}
    tr.onclick=()=>openDrawer(s);
    tb.appendChild(tr);
  }
}
function esc(t){return String(t??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function openDrawer(s){
  const d=document.getElementById('drawer');
  const st=s.status||{};
  d.innerHTML=`<h2>${esc(s.name)}</h2><div class="meta">${esc(s.id)} · ${esc(s.category)} · ${esc(s.kind||'script')}</div>
  <div class="kv">
    <div>path</div><div class="path">${esc(s.path)}</div>
    <div>exists</div><div>${s.exists===false?'<b style="color:var(--bad)">missing</b>':'yes'}</div>
    <div>what</div><div>${esc(s.what)}</div>
    <div>why</div><div>${esc(s.why||'—')}</div>
    <div>created</div><div>${esc(s.created||'—')}</div>
    ${s.schedule?`<div>schedule</div><div>${esc(s.schedule)}${s.next_run?' · next '+esc(s.next_run):''}</div>`:''}
    <div>status</div><div>${esc(st.detail||st.state||'n/a')}</div>
    <div>runnable</div><div>${s.runnable?'yes':'no'}${s.sudo?' · sudo (never via panel)':''}</div>
    ${s.notes?`<div>notes</div><div>${esc(s.notes)}</div>`:''}
    ${s.tags?`<div>tags</div><div>${s.tags.map(t=>'<span class="badge">'+esc(t)+'</span>').join(' ')}</div>`:''}
  </div>
  <div id="runout"><i style="color:var(--dim)">loading last run…</i></div>
  <button class="chip" style="margin-top:14px" onclick="document.getElementById('drawer').classList.remove('open')">close</button>`;
  d.classList.add('open');
  try{
    const r=await (await fetch('/api/runs?id='+encodeURIComponent(s.id))).json();
    const el=document.getElementById('runout');
    if(!r.length){el.innerHTML='<i style="color:var(--dim)">never run from the panel</i>';return;}
    const last=r[r.length-1];
    el.innerHTML=`<div class="meta">last run: ${fmtTs(last.ts)} · exit ${last.exit} · ${last.duration_s}s${last.timed_out?' · TIMED OUT':''}</div>
      <pre>${esc((last.stdout||'')+(last.stderr?('\n--- stderr ---\n'+last.stderr):''))||'(no output)'}</pre>`;
  }catch(e){document.getElementById('runout').innerHTML='';}
}
function toast(t){const e=document.getElementById('toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',4000);}
async function trustBar(){
  const el=document.getElementById('trustbar');
  try{
    const d=await(await fetch('/api/trust')).json();
    const bad=d.verdict!=='PASS', warn=bad||d.guards_armed.length||d.probation_pending;
    el.innerHTML=`<a href="/trust" style="color:${bad?'var(--bad)':(warn?'var(--warn)':'var(--ok)')}">`+
      `guardrails: denylist=${d.deny_rules} · armed=${d.guards_armed.length} · `+
      `probation=${d.probation_pending} · invariants=${d.verdict} · `+
      `deny_hits_7d=${d.deny_hits.length}</a>`;
  }catch(e){el.innerHTML='<span style="color:var(--dim)">guardrails: posture unavailable</span>';}
}
document.getElementById('q').oninput=e=>{search=e.target.value.toLowerCase();renderRows()};
document.getElementById('refresh').onclick=()=>load(true);
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; sortDir=sortKey===k?-sortDir:1; sortKey=k; renderRows();});
load(); trustBar();
</script></body></html>
"""


TRUST_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trust Console</title>
<style>
:root{--bg:#0a0a0b;--panel:#121214;--border:#232327;--fg:#e6e6e8;--dim:#9a9aa2;--acc:#4f9cf9;--ok:#3fb950;--bad:#f85149;--warn:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;padding:20px;max-width:960px}
h1{font-size:17px;font-weight:600;margin:0 0 4px}a{color:var(--acc);text-decoration:none}
p.sub{color:var(--dim);margin:0 0 16px;font-size:12px}
.verdict{font-size:15px;font-weight:600;padding:8px 14px;border-radius:8px;display:inline-block;margin-bottom:16px}
.verdict.ok{background:#12261a;color:var(--ok);border:1px solid #1f4a2e}
.verdict.bad{background:#2b1215;color:var(--bad);border:1px solid #5b1d22}
section{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px 14px;margin-bottom:12px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin:0 0 8px;font-weight:600}
table{width:100%;border-collapse:collapse}td{padding:4px 8px;font-size:12.5px;vertical-align:top;border-top:1px solid var(--border)}
tr:first-child td{border-top:0}
.dim{color:var(--dim)}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.empty{color:var(--dim);font-size:12px;font-style:italic}
button.chip{background:var(--panel);border:1px solid var(--border);color:var(--dim);padding:5px 10px;border-radius:14px;cursor:pointer;font:inherit;font-size:12px}
button.chip:hover{color:var(--fg);border-color:var(--acc)}
button.act{background:#1a1d23;border:1px solid var(--border);color:var(--fg);padding:3px 10px;border-radius:6px;cursor:pointer;font:inherit;font-size:11.5px;margin-left:6px}
button.act:hover{border-color:var(--acc)}
button.act.go{color:var(--ok)}button.act.stop{color:var(--bad)}
button.act.arm{color:var(--warn);border-color:var(--warn)}
button.act:disabled{opacity:.4;cursor:default}
/* action-first layout (2026-10-11): cards with status left-border,
   deadline countdowns, audit collapsed under details. Phone-first. */
.card{background:#151518;border-left:3px solid var(--warn);border-radius:6px;padding:11px 13px;margin-bottom:9px}
.card.ok{border-left-color:var(--ok)}.card.bad{border-left-color:var(--bad)}
.card .head{font-size:14.5px;font-weight:600;margin-bottom:3px;line-height:1.3}
.card .meta{color:var(--dim);font-size:11.5px;margin-bottom:7px}
.card .deadline{color:var(--warn);font-size:12px;font-weight:600;margin-bottom:7px}
.card .deadline.over{color:var(--bad)}
button.act{padding:9px 15px;font-size:13px;margin-left:0;margin-right:8px;border-radius:8px}
details{margin:4px 0}
details>summary{cursor:pointer;color:var(--dim);font-size:12px;padding:6px 2px;user-select:none}
details>summary:hover{color:var(--fg)}
.jchip{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;border:1px solid var(--border);margin-left:6px}
.jchip.clean{color:var(--ok);border-color:#1f4a2e}.jchip.flagged{color:var(--bad);border-color:#5b1d22}
.jchip.abstain,.jchip.unavailable{color:var(--warn);border-color:#4a3a1f}
</style></head><body>
<h1>Trust Console <span style="color:var(--dim);font-weight:400">· rig guardrails</span></h1>
<p class="sub">what needs you, by when — approvals act on live guard units only; enforcement stays in the exec layer. <a href="/">← panel</a></p>
<div id="verdict" class="verdict dim">loading…</div>
<section><h2>Needs you</h2><div id="needs"></div></section>
<section><h2>Health</h2><div id="health"></div></section>
<section><h2>Audit</h2>
<details><summary>armed revert guards</summary><div id="guards"></div></details>
<details><summary>probation history</summary><div id="probhist"></div></details>
<details><summary>failed applies</summary><div id="failed"></div></details>
<details><summary>deny hits (7d)</summary><div id="deny"></div></details>
<details><summary>invariant failures (7d)</summary><div id="invf"></div></details>
<details><summary>recent snapshots</summary><div id="snap"></div></details>
</section>
<div style="margin-top:8px"><button class="chip" onclick="load()">↻ refresh</button>
<span id="stamp" class="dim" style="font-size:11px;margin-left:10px"></span></div>
<script>
function esc(t){return String(t??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function fmt(ts){return ts?new Date(ts*1000).toLocaleString():'—';}
function rows(list,html){return list.length?'<table>'+list.map(html).join('')+'</table>':'<div class="empty">none</div>';}
function gidOf(u){const m=String(u).match(/agent-guard-(g\d+-\d+)/);return m?m[1]:'';}
function base(p){return p?p.split('/').pop():'';}
function left(deadline){
  if(!deadline)return '';
  const s=deadline-Math.floor(Date.now()/1000);
  if(s<=0)return 'overdue — reverts any moment';
  if(s<3600)return 'auto-reverts in ~'+Math.max(1,Math.round(s/60))+'m';
  return 'auto-reverts in ~'+Math.round(s/3600)+'h';
}
function firstline(t){
  if(!t)return '';
  const i=t.search(/[.\n]/);
  return i>0&&i<160?t.slice(0,i+1):t.slice(0,160);
}
async function gact(a,gid,btn){
  btn.disabled=true;
  try{
    const r=await fetch('/api/guard/'+a+'?gid='+encodeURIComponent(gid),{method:'POST'});
    const d=await r.json();
    if(!d.ok)alert((d.error||'action failed'));
  }catch(e){alert('request failed: '+e);}
  load();
}
async function pact(a,pid,btn){
  // approve is the load-bearing tap — first tap arms for 8s, second
  // confirms. deny stays one-tap: a rejected proposal is recoverable
  // (the model re-proposes), an applied host change is the blast radius.
  if(a==='approve'&&!btn.dataset.armed){
    btn.dataset.armed='1';const t=btn.textContent;
    btn.textContent='confirm apply?';btn.className='act arm';
    setTimeout(()=>{btn.dataset.armed='';btn.textContent=t;btn.className='act go';},8000);
    return;
  }
  btn.disabled=true;
  try{
    const r=await fetch('/api/proposal/'+a+'?id='+encodeURIComponent(pid),{method:'POST'});
    const d=await r.json();
    if(!d.ok)alert((d.error||'action failed'));
    else if(d.detail)alert(d.action+' '+d.id+': '+d.detail.slice(0,200));
  }catch(e){alert('request failed: '+e);}
  load();
}
async function load(){
  const r=await fetch('/api/trust');const d=await r.json();
  const v=document.getElementById('verdict');

  // --- needs you: probation deadlines first (they self-revert), then
  // pending proposals. Everything else is ambient. ---
  const probPend=(d.probation||[]).filter(p=>p.state==='pending');
  const pendAll=(d.proposals||[]).filter(p=>p.status==='pending');
  const pendReal=pendAll.filter(p=>p.origin!=='eval');
  const pendEval=pendAll.filter(p=>p.origin==='eval');
  const needs=probPend.length+pendReal.length;
  v.className='verdict '+(needs===0&&d.verdict==='PASS'?'ok':'bad');
  v.textContent=needs===0&&d.verdict==='PASS'
    ?'✓ all quiet — nothing needs you'
    :`⚠ ${needs} need${needs===1?'s':''} you`+(d.verdict!=='PASS'?' · invariants FAIL':'');

  const probCards=probPend.map(p=>{
    const dl=left(p.deadline);
    return `<div class="card warn"><div class="head">probation: ${esc(p.label||p.gid)}</div>`+
      `<div class="meta">${esc(p.path||p.gid)}</div>`+
      (dl?`<div class="deadline${dl.startsWith('overdue')?' over':''}">⏳ ${esc(dl)} — promote to keep</div>`:'')+
      `<button class="act go" onclick="gact('promote','${esc(p.gid)}',this)">✓ promote — keep it</button>`+
      `<button class="act stop" onclick="gact('revert','${esc(p.gid)}',this)">↩ revert now</button></div>`;
  }).join('');

  const propCards=pendReal.map(p=>{
    const headline=p.summary||firstline(p.rationale)||base(p.path);
    const j=p.judge?`<span class="jchip ${esc(p.judge.status)}">judge: ${esc(p.judge.status)}</span>`:'';
    return `<div class="card ${p.judge&&p.judge.status==='flagged'?'bad':''}" id="prop-${esc(p.id)}">`+
      `<div class="head">${esc(headline)}</div>`+
      `<div class="meta">${esc(base(p.path))} <span class="dim">+${p.adds}/-${p.dels} · ${esc(p.verify||'')}</span>${j}</div>`+
      `<button class="act go" onclick="pact('approve','${esc(p.id)}',this)">✓ approve</button>`+
      `<button class="act stop" onclick="pact('deny','${esc(p.id)}',this)">✗ deny</button>`+
      `<details><summary>why + diff</summary>`+
      `<div class="dim" style="font-size:11.5px;padding:4px 0">${esc(p.rationale||'')}</div>`+
      (p.evidence?`<div class="dim" style="font-size:11px;padding:2px 0">evidence: ${esc(p.evidence)}</div>`:'')+
      (p.judge&&p.judge.answers?`<div class="dim" style="font-size:11px;padding:2px 0">`+
        `judge: ${esc(Object.entries(p.judge.answers).map(([q,a])=>q+'='+(a.abstained?'?':a.chosen)+'('+a.prob+')').join(' · '))}</div>`:'')+
      (p.diff?`<pre style="margin:6px 0;font-size:10.5px;max-height:280px;overflow:auto;background:#0d0d10;padding:8px;border-radius:6px">${esc(p.diff)}${p.diff_truncated?'\n… (truncated)':''}</pre>`:'')+
      `<div class="dim" style="font-size:10.5px">${esc(p.id)} · ${esc(p.path)}</div>`+
      `</details></div>`;
  }).join('');

  document.getElementById('needs').innerHTML=
    (probCards+propCards||'<div class="empty">nothing pending — all quiet</div>')
    +(pendEval.length?`<details style="margin-top:6px"><summary class="dim">eval fixtures ×${pendEval.length} — corpus artifacts, not decisions; deny dismisses</summary>`+
      rows(pendEval,
        p=>`<tr><td class="dim">${esc(p.id)}</td><td class="dim">${esc(base(p.path))}</td>`+
           `<td><button class="act stop" onclick="pact('deny','${esc(p.id)}',this)">✗ dismiss</button></td></tr>`)
      +`</details>`:'');

  // --- health: a line, not a wall. Failures surface; passes compress. ---
  const invFails=(d.invariants.checks||[]).filter(c=>!/^ok/.test(c));
  const healthBits=[`<span class="${invFails.length?'bad':'ok'}">invariants ${invFails.length?'FAIL ('+invFails.length+')':'✓ all pass'}</span>`,
    `<span class="dim">deny hits: ${d.deny_hits.length} (7d)</span>`];
  if(d.judge&&d.judge.judged)healthBits.push(`<span class="dim">judge: ${d.judge.settled?d.judge.agreed+'/'+d.judge.settled+' ('+d.judge.agreement+'%)':'awaiting'}</span>`);
  healthBits.push(`<span class="dim">${d.deny_rules} deny rules · ${d.guards_armed.length} armed</span>`);
  document.getElementById('health').innerHTML=
    healthBits.join(' · ')+
    (invFails.length?rows(invFails,c=>`<tr><td class="bad">${esc(c)}</td></tr>`):'');

  // --- audit: collapsed by default ---
  document.getElementById('guards').innerHTML=rows(d.guards_armed,
    g=>`<tr><td class="warn">${esc(g.unit)}</td><td class="dim">fires ${esc(g.fires)}</td>`+
       `<td><button class="act stop" onclick="gact('revert',gidOf('${esc(g.unit)}'),this)">↩ revert now</button></td></tr>`);
  const probHist=(d.probation||[]).filter(p=>p.state!=='pending');
  document.getElementById('probhist').innerHTML=rows(probHist,
    p=>`<tr><td>${esc(p.label||p.gid)}</td><td class="${p.state==='promoted'?'ok':'dim'}">${esc(p.state)}</td></tr>`);
  const failed=(d.proposals||[]).filter(p=>p.status==='apply_failed');
  document.getElementById('failed').innerHTML=rows(failed,
    p=>`<tr><td class="bad">${esc(base(p.path))}</td><td class="dim" style="font-size:11px;white-space:pre-wrap">${esc(p.fail_detail||'(no detail recorded)')}</td></tr>`);
  document.getElementById('deny').innerHTML=rows(d.deny_hits.slice().reverse(),
    h=>`<tr><td class="dim" style="white-space:nowrap">${fmt(h.ts)}</td><td class="bad">${esc(h.msg)}</td></tr>`);
  document.getElementById('invf').innerHTML=rows(d.invariant_failures.slice().reverse(),
    h=>`<tr><td class="dim" style="white-space:nowrap">${fmt(h.ts)}</td><td class="bad">${esc(h.msg)}</td></tr>`);
  document.getElementById('snap').innerHTML=rows(d.snapshots.slice().reverse(),
    s=>`<tr><td class="dim" style="white-space:nowrap">${esc(s.ts)}</td><td>${esc(s.tag)}</td><td class="dim">${esc(s.snapshot||'')}</td><td class="dim">${esc(s.why||'')}</td></tr>`);
  document.getElementById('stamp').textContent='updated '+new Date().toLocaleTimeString();
}
load();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "script-panel/1.0"

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str) -> None:
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._html(INDEX_HTML)
        elif self.path in ("/trust", "/trust.html"):
            self._html(TRUST_HTML)
        elif self.path.startswith("/api/trust"):
            self._json(trust_posture())
        elif self.path.startswith("/api/catalog"):
            self._json(catalog_fresh() if "bust" in self.path else catalog_with_status())
        elif self.path.startswith("/api/runs"):
            runs = load_runs()
            if "id=" in self.path:
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                want = q.get("id", [""])[0]
                runs = [r for r in runs if r["id"] == want]
            self._json(runs[-50:])
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/api/proposal/"):
            from urllib.parse import parse_qs, urlparse
            u = urlparse(self.path)
            action = u.path[len("/api/proposal/"):]
            pid = parse_qs(u.query).get("id", [""])[0]
            result = proposal_action(pid, action)
            self._json(result, 200 if result["ok"] else 409)
            return
        if self.path.startswith("/api/guard/"):
            from urllib.parse import parse_qs, urlparse
            u = urlparse(self.path)
            action = u.path[len("/api/guard/"):]
            gid = parse_qs(u.query).get("gid", [""])[0]
            if not gid:
                try:
                    body = self.rfile.read(
                        int(self.headers.get("Content-Length", 0) or 0))
                    gid = (json.loads(body) or {}).get("gid", "")
                except (json.JSONDecodeError, ValueError):
                    gid = ""
            result = guard_action(gid, action)
            self._json(result, 200 if result["ok"] else 409)
            return
        if not self.path.startswith("/api/run/"):
            self._json({"error": "not found"}, 404)
            return
        sid = self.path[len("/api/run/"):]
        cat = load_catalog()
        entry = next((e for e in cat["scripts"] if e["id"] == sid), None)
        if not entry:
            self._json({"error": f"unknown script id {sid!r}"}, 404)
            return
        if not entry.get("runnable") or entry.get("sudo"):
            self._json({"error": f"{sid} is not runnable from the panel"}, 403)
            return
        if not Path(expand(entry["path"])).exists():
            self._json({"error": f"{entry['path']} does not exist"}, 410)
            return
        record = run_script(entry)
        self._json(record)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"script-panel on http://{HOST}:{PORT} (catalog: {CATALOG_PATH})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
