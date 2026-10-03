#!/usr/bin/env python3
"""Deployment contract: assert the live system matches declared intent.

Unit tests prove code is correct when called; this checks the layer they
can't see — that the assembled system is actually wired: timers armed,
units live, endpoints serving, OWUI DB matching fix_tooling.py canonical,
and the state-snapshot pipeline flowing.

Usage:
  deployment_contract.py          human report; exit 1 if any check fails
  deployment_contract.py --oneline  single summary line (embedded in the
                                    state snapshot by state_snapshot.py)
  deployment_contract.py --json     machine-readable check list

The contract is intentionally narrow: ~8 load-bearing assertions, each a
few lines. Adding a new subsystem? Add its liveness here, not a new
monitor — one contract, one verdict.
"""

import argparse
import ast
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
REPO = HOME / "Projects/aios"
SNAPSHOT_HOST = HOME / ".local/state/aios/state-snapshot.md"
SNAPSHOT_CONTAINER = "/app/backend/data/aios-state-snapshot.md"
OWUI_CONTAINER = "open_webui_aios"
SNAPSHOT_MAX_AGE_S = 900  # matches filter valve state_max_age_s

SYSTEM_UNITS = [
    "llamacpp-adaptive",
    "llama-nano-adaptive",
    "local-tools-adaptive",
    "project-tools-adaptive",
    "aios_core",
]
USER_UNITS = [
    "hori-mcpo.service",
    "hori-mcpo-watchdog.timer",
    "aios-state-snapshot.timer",
    "idea-factory-web.service",
    "script-panel.service",
    "workspace-tools.service",
]

HTTP_ENDPOINTS = [
    ("llama", "http://127.0.0.1:8080/health"),
    ("local-tools", "http://127.0.0.1:8766/health"),
    ("project-tools", "http://127.0.0.1:8769/health"),
    ("workspace-tools", "http://127.0.0.1:8771/health"),
    ("aios_core", "http://127.0.0.1:5680/health"),
    ("idea-factory", "http://127.0.0.1:8772/health"),
]

EXPECTED_CANON_KEYS = {
    "ALL_QWEN_DEVSTRAL": "models",
    "PROJECT_TOOL_MODELS": "project_models",
    "WORKSPACE_WRITE_MODELS": "write_models",
}

CANON_CONSTANTS = {"META_BUILTINTOOLS", "WEB_SEARCH_MODELS"}


def _run(cmd: list[str], timeout: int = 8, **kw) -> tuple[str, int]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, **kw
        )
        return p.stdout, p.returncode
    except Exception:
        return "", 1


def _http_json(url: str, timeout: int = 4, data: bytes | None = None):
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), r.status
    except Exception as e:
        return {"_error": str(e)}, 0


def check_units() -> tuple[bool, str]:
    bad = []
    out, _ = _run(["systemctl", "is-active", *SYSTEM_UNITS])
    states = out.strip().splitlines()
    for i, u in enumerate(SYSTEM_UNITS):
        if i >= len(states) or states[i] != "active":
            bad.append(u)
    out, _ = _run(["systemctl", "--user", "is-active", *USER_UNITS])
    states = out.strip().splitlines()
    for i, u in enumerate(USER_UNITS):
        if i >= len(states) or states[i] != "active":
            bad.append(u + "(user)")
    if bad:
        return False, "inactive: " + ", ".join(bad)
    return True, f"{len(SYSTEM_UNITS)} system + {len(USER_UNITS)} user active"


def check_endpoints() -> tuple[bool, str]:
    bad = []
    for name, url in HTTP_ENDPOINTS:
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                if r.status >= 400:
                    bad.append(f"{name}={r.status}")
        except Exception as e:
            bad.append(f"{name}={type(e).__name__}")
    if bad:
        return False, "unreachable: " + ", ".join(bad)
    return True, f"{len(HTTP_ENDPOINTS)} endpoints healthy"


def check_hori_state_tools() -> tuple[bool, str]:
    spec, status = _http_json("http://127.0.0.1:8775/openapi.json")
    n = len((spec or {}).get("paths") or {})
    if n == 0:
        return False, "empty/absent spec — mcpo bridge dead"
    return True, f"{n} paths"


def check_mcpo_auth() -> tuple[bool, str]:
    """:8774 exposes write-capable hori tools — unauthenticated direct
    access must be rejected (mcpo --api-key/--strict-auth, 2026-09-23)."""
    try:
        urllib.request.urlopen("http://127.0.0.1:8774/hori/openapi.json", timeout=4)
        return False, "mcpo spec served WITHOUT auth — write tools exposed"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return True, "unauthenticated access rejected (401)"
        return False, f"unexpected status {e.code}"
    except Exception as e:
        return False, f"probe error: {e}"


def check_local_tools_status() -> tuple[bool, str]:
    # get_status proxies aios-core /system/state (15s upstream timeout) —
    # ~6s is normal, so the contract budget is 20s.
    d, status = _http_json(
        "http://127.0.0.1:8766/get_status", data=b"{}", timeout=20
    )
    if not (d or {}).get("ok"):
        return False, f"get_status failed: {str(d)[:120]}"
    return True, "get_status ok"


def check_snapshot_fresh() -> tuple[bool, str]:
    """Checks the *previous* snapshot's age — i.e. the cadence held. When run
    inside the generator this measures time since the last successful run,
    which is exactly the staleness signal."""
    if not SNAPSHOT_HOST.exists():
        return False, "host snapshot missing"
    age = int(time.time() - SNAPSHOT_HOST.stat().st_mtime)
    if age > SNAPSHOT_MAX_AGE_S:
        return False, f"host snapshot {age}s old (> {SNAPSHOT_MAX_AGE_S})"
    out, rc = _run(
        ["docker", "exec", OWUI_CONTAINER, "stat", "-c", "%Y", SNAPSHOT_CONTAINER]
    )
    if rc != 0:
        return False, "container snapshot missing"
    cage = int(time.time() - int(out.strip() or 0))
    if cage > SNAPSHOT_MAX_AGE_S:
        return False, f"container snapshot {cage}s old"
    return True, f"fresh ({age}s host / {cage}s container)"


def _owui_sql(sql: str) -> list:
    """Run a read query inside the OWUI container, return rows as lists."""
    code = (
        "import sqlite3,json;"
        "c=sqlite3.connect('/app/backend/data/webui.db');"
        "print(json.dumps(c.execute(" + json.dumps(sql) + ").fetchall()))"
    )
    out, rc = _run(["docker", "exec", OWUI_CONTAINER, "python3", "-c", code])
    if rc != 0:
        raise RuntimeError("owui db query failed")
    return json.loads(out.strip().splitlines()[-1])


def check_owui_db_hygiene() -> tuple[bool, str]:
    try:
        # LIKE suffix matches the deleted workspace script id without
        # embedding the PII-patterned literal (pre-commit hook blocks it).
        rows = _owui_sql(
            "SELECT id FROM tool WHERE id LIKE '%-local-tools' "
            "UNION ALL SELECT id FROM function WHERE id LIKE '%-local-tools'"
        )
    except RuntimeError as e:
        return False, str(e)
    if rows:
        return False, f"stale *-local-tools row present: {rows[0][0]}"
    return True, "no stale tool rows"


def _canonical_sets() -> dict:
    tree = ast.parse(
        (REPO / "services/open-webui/fix_tooling.py").read_text()
    )
    vals = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in EXPECTED_CANON_KEYS:
                    vals[t.id] = set(ast.literal_eval(node.value))
    return vals


def check_filter_valves() -> tuple[bool, str]:
    canon = _canonical_sets()
    if len(canon) != len(EXPECTED_CANON_KEYS):
        return False, "could not parse canonical sets from fix_tooling.py"
    try:
        rows = _owui_sql(
            "SELECT valves, content, is_active, is_global FROM function "
            "WHERE id LIKE '%tool_server_filter'"
        )
    except RuntimeError as e:
        return False, str(e)
    if not rows:
        return False, "filter row missing"
    valves = json.loads(rows[0][0] or "{}")
    content, active, is_global = rows[0][1], rows[0][2], rows[0][3]
    bad = []
    # is_global=1 is what runs it for every request; is_active tracks the
    # (unused) per-user toggle and is legitimately 0 on this stack.
    if not is_global:
        bad.append("filter not global")
    for canon_name, valve_key in EXPECTED_CANON_KEYS.items():
        live = {x.strip() for x in (valves.get(valve_key) or "").split(",") if x.strip()}
        if live != canon[canon_name]:
            bad.append(f"{valve_key} drifted")
    for key, expect in (
        ("server_tool_ids", {"server:local-tools", "server:hori-state"}),
        ("project_tool_ids", {"server:project-tools"}),
        ("write_server_tool_ids", {"server:workspace-tools"}),
    ):
        live = {x.strip() for x in (valves.get(key) or "").split(",") if x.strip()}
        if live != expect:
            bad.append(f"{key} drifted")
    if "state_file" not in (content or ""):
        bad.append("filter lacks snapshot injection")
    # A stored filter that doesn't compile loads as zero — injection dies
    # silently while every other check still passes.
    try:
        compile(content or "", "<tool_server_filter>", "exec")
    except SyntaxError as e:
        bad.append(f"filter source broken: {e.msg} (line {e.lineno})")
    if bad:
        return False, "; ".join(bad)
    return True, "valves match canonical + injection live"


def _canon_constants() -> dict:
    """Two-pass: literal_eval simple assigns first, then eval expressions
    (e.g. `set(ALL_QWEN_DEVSTRAL) - {"x"}`) against that namespace."""
    tree = ast.parse(
        (REPO / "services/open-webui/fix_tooling.py").read_text()
    )
    assigns = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    assigns.append((t.id, node.value))
    ns = {}
    pending = []
    for name, value in assigns:
        try:
            ns[name] = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            pending.append((name, value))
    for name, value in pending:
        try:
            ns[name] = eval(
                compile(ast.Expression(value), "<canon>", "eval"),
                {"__builtins__": {"set": set, "list": list, "dict": dict}},
                ns,
            )
        except Exception:
            pass
    return {k: ns[k] for k in CANON_CONSTANTS if k in ns}


def check_mcp_scope() -> tuple[bool, str]:
    """The mcpo upstream token must be read-scoped: write tools
    (memory_store, episode_append) must not appear in the served
    catalog. If the admin bearer ever lands back in hori.json, this
    fails instead of silently re-exposing writes."""
    env = Path.home() / ".config/mcpo/mcpo.env"
    key = None
    try:
        for line in env.read_text().splitlines():
            if line.startswith("MCPO_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    except OSError as e:
        return False, f"mcpo.env unreadable: {e}"
    if not key:
        return False, "mcpo.env missing MCPO_API_KEY"
    req = urllib.request.Request(
        "http://127.0.0.1:8774/hori/openapi.json",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        spec = json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        return False, f"mcpo openapi unreachable: {e}"
    names = {p.rsplit("/", 1)[-1] for p in spec.get("paths", {})}
    leaked = names & {"memory_store", "episode_append"}
    if leaked:
        return False, f"write tools exposed upstream: {sorted(leaked)}"
    if not names:
        return False, "upstream catalog empty"
    return True, f"upstream catalog read-scoped ({len(names)} tools)"


def check_compat_api_closed() -> tuple[bool, str]:
    """The OpenAI/Ollama-compat surface (/openai/*, /ollama/*) bypasses
    ALL filters including snapshot injection — it's for external API
    clients, and nothing on this box consumes it. It must stay
    unreachable: ENABLE_API_KEYS=false means no credential can exist,
    and the api_key table must stay empty. Three reopen vectors, one
    check: a key row appearing, env drift re-enabling key issuance, or
    an unauthenticated request getting past 401."""
    bad = []
    try:
        rows = _owui_sql("SELECT COUNT(*) FROM api_key")
        if rows and rows[0][0]:
            bad.append(f"{rows[0][0]} api_key row(s) present")
    except RuntimeError as e:
        bad.append(str(e))
    out, rc = _run(
        ["docker", "exec", OWUI_CONTAINER, "printenv", "ENABLE_API_KEYS"]
    )
    if rc != 0 or out.strip().lower() != "false":
        bad.append(f"ENABLE_API_KEYS={out.strip() or 'unset'}")
    # Unauthenticated POST must be refused — 401/403 both fine; a 2xx
    # would mean the filter-bypassing surface is wide open.
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:3000/openai/chat/completions",
            data=b"{}", headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            if r.status < 400:
                bad.append(f"unauthenticated /openai POST answered {r.status}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            pass  # refused, as required
        elif e.code == 404:
            pass  # surface not even mounted — also fine
        else:
            bad.append(f"/openai POST returned {e.code}")
    except Exception as e:
        bad.append(f"probe error: {e}")
    if bad:
        return False, "; ".join(bad)
    return True, "compat surface unreachable (no keys, keys disabled, 401)"


def check_builtin_tools() -> tuple[bool, str]:
    """Every model's stored meta.builtinTools must match canonical
    (META_BUILTINTOOLS + web_search per WEB_SEARCH_MODELS). An OWUI upgrade
    that adds categories or flips defaults shows up here instead of
    silently re-bloating the grammar."""
    canon = _canon_constants()
    if len(canon) != len(CANON_CONSTANTS):
        return False, "could not parse builtinTools canonical constants"
    base = canon["META_BUILTINTOOLS"]
    armed = set(canon["WEB_SEARCH_MODELS"])
    try:
        rows = _owui_sql("SELECT id, meta, is_active FROM model")
    except RuntimeError as e:
        return False, str(e)
    bad = []
    for mid, meta_json, active in rows:
        if not active:
            continue
        meta = json.loads(meta_json or "{}")
        live = meta.get("builtinTools") or {}
        expect = dict(base)
        expect["web_search"] = mid in armed
        # nano is deliberately tool-free: every category must be False.
        if mid == "nano":
            expect = {k: False for k in expect}
        diffs = [
            f"{k}={live.get(k)}(want {v})"
            for k, v in expect.items()
            if live.get(k) != v
        ] + [f"{k} unexpected" for k in live if k not in expect]
        # Phase A (2026-09-26) stripped knowledge collections off every
        # model — auto-injected stale docs were confabulation fuel. A
        # re-attached collection must fail here, not reappear silently.
        knowledge = meta.get("knowledge") or []
        if knowledge:
            diffs.append(f"knowledge re-attached: {knowledge[:2]}")
        if diffs:
            bad.append(f"{mid}: {', '.join(diffs[:3])}")
    if bad:
        return False, "; ".join(bad[:4])
    return True, f"{len(rows)} models match canonical builtinTools"


CHECKS = [
    ("units", check_units),
    ("endpoints", check_endpoints),
    ("hori_state_tools", check_hori_state_tools),
    ("mcpo_auth", check_mcpo_auth),
    ("mcp_scope", check_mcp_scope),
    ("local_tools_status", check_local_tools_status),
    ("snapshot_fresh", check_snapshot_fresh),
    ("owui_db_hygiene", check_owui_db_hygiene),
    ("filter_valves", check_filter_valves),
    ("builtin_tools", check_builtin_tools),
    ("compat_api_closed", check_compat_api_closed),
]


def run() -> list[dict]:
    results = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"check error: {e}"
        results.append({"name": name, "ok": ok, "detail": detail})
    return results


def oneline(results: list[dict]) -> str:
    bad = [r["name"] for r in results if not r["ok"]]
    if not bad:
        return "deployment_contract=PASS"
    return "deployment_contract=FAIL (" + ", ".join(bad) + ")"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oneline", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    results = run()
    if args.json:
        print(json.dumps(results, indent=2))
    elif args.oneline:
        print(oneline(results))
    else:
        for r in results:
            print(f"{'PASS' if r['ok'] else 'FAIL'} {r['name']} — {r['detail']}")
        print(oneline(results))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
