#!/usr/bin/env python3
"""Monkey-test the native /mcp/http lane on live aios_core.

Throws malformed transport payloads, auth edge cases, and hostile
tool-call arguments at the real endpoint. Read-only: every tool exposed
under the read token is a read by design, and the one write attempt is a
deny.conf-listed command that must come back refused.

No secrets are printed: the read token is loaded from the canonical
hori.yaml and only ever placed in a header.
"""
import concurrent.futures as cf
import json
import urllib.request
import urllib.error
from pathlib import Path

URL = "http://127.0.0.1:5680/mcp/http"


def _read_token() -> str:
    for line in Path("/etc/hori/hori.yaml").read_text().splitlines():
        s = line.strip()
        if s.startswith("read_token:"):
            return s.split(":", 1)[1].strip().strip("\"'")
    raise SystemExit("no mcp.read_token")


TOK = _read_token()


def post(body, token=None, ctype="application/json", extra=None):
    headers = {"Content-Type": ctype}
    if token is not None:
        headers["Authorization"] = token
    if extra:
        headers.update(extra)
    req = urllib.request.Request(URL, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(detail)[:100] if detail else ""))


def jrpc(idv, method, params=None):
    m = {"jsonrpc": "2.0", "id": idv, "method": method}
    if params is not None:
        m["params"] = params
    return m


auth = f"Bearer {TOK}"

# ---------- transport / protocol abuse ----------

s, _ = post(b"{not json at all", auth)
check("malformed json -> 4xx", s in (400, 422), f"status={s}")

s, _ = post(b"", auth)
check("empty body -> 4xx", s in (400, 415, 422), f"status={s}")

s, _ = post(json.dumps({"foo": "bar"}).encode(), auth)
check("non-RPC object -> handled", s in (200, 400, 422), f"status={s}")

s, b = post(json.dumps(jrpc(1, "no/such.method")).encode(), auth)
err = (json.loads(b) if s == 200 else {}).get("error", {})
check("unknown method -> rpc error", s == 200 and err.get("code") == -32601,
      f"status={s} err={err}")

s, b = post(json.dumps([]).encode(), auth)
check("empty batch -> handled", s in (200, 202, 400), f"status={s} body={b[:60]}")

# notification-only batch -> 202
s, b = post(json.dumps([
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}},
]).encode(), auth)
check("notif-only batch -> 202", s == 202, f"status={s}")

# mixed batch: notification + request
s, b = post(json.dumps([
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    jrpc(7, "ping"),
]).encode(), auth)
arr = json.loads(b) if s == 200 else []
check("mixed batch -> array w/o notif echo",
      s == 200 and isinstance(arr, list) and len(arr) == 1 and arr[0].get("id") == 7,
      f"status={s} n={len(arr) if isinstance(arr, list) else '?'}")

# big batch (50 pings)
s, b = post(json.dumps([jrpc(i, "ping") for i in range(50)]).encode(), auth)
arr = json.loads(b) if s == 200 else []
check("50x ping batch", s == 200 and isinstance(arr, list) and len(arr) == 50,
      f"status={s} n={len(arr) if isinstance(arr, list) else '?'}")

# huge-ish payload (~2MB) in a tools/call param
big = "x" * (2 * 1024 * 1024)
s, b = post(json.dumps(jrpc(9, "tools/call", {
    "name": "episode_search",
    "arguments": {"query": big, "limit": 1},
})).encode(), auth)
check("2MB arg payload -> no crash", s in (200, 400, 413, 422), f"status={s}")

# wrong methods on the route
for verb in ("GET", "DELETE", "PUT"):
    req = urllib.request.Request(URL, method=verb, headers={"Authorization": auth})
    try:
        urllib.request.urlopen(req, timeout=5)
        s = 200
    except urllib.error.HTTPError as e:
        s = e.code
    except Exception as e:
        s = type(e).__name__
    check(f"{verb} -> refused", s in (404, 405), f"status={s}")

# wrong content-type
s, _ = post(json.dumps(jrpc(1, "ping")).encode(), auth, ctype="text/plain")
check("text/plain ctype -> handled", s in (200, 400, 415, 422), f"status={s}")

# ---------- auth edge cases ----------

s, _ = post(json.dumps(jrpc(1, "ping")).encode(), None)
check("no auth -> 401", s == 401, f"status={s}")

s, _ = post(json.dumps(jrpc(1, "ping")).encode(), "Bearer garbage-token")
check("bad token -> 403", s == 403, f"status={s}")

s, _ = post(json.dumps(jrpc(1, "ping")).encode(), f"bearer {TOK}")
check("lowercase scheme -> refused (strict Bearer)", s in (401, 403), f"status={s}")

s, _ = post(json.dumps(jrpc(1, "ping")).encode(), f"Bearer {TOK} ")
check("trailing-space token -> refused", s in (401, 403), f"status={s}")

s, _ = post(json.dumps(jrpc(1, "ping")).encode(), f"Bearer  {TOK}")
check("double-space bearer -> refused", s in (401, 403), f"status={s}")

s, _ = post(json.dumps(jrpc(1, "ping")).encode(), f"Bearer x{TOK[:-1]}")
check("mutated token -> refused", s in (401, 403), f"status={s}")

# token smuggling attempts
s, _ = post(json.dumps(jrpc(1, "ping")).encode(), None,
            extra={"Authorization": f"Bearer {TOK}", "X-Token": TOK})
# second header overrides first in Request; just confirm it doesn't 500
check("extra token header -> sane", s in (200, 401, 403), f"status={s}")

# ---------- tool-call abuse (read scope) ----------

def call(name, args, idv=1):
    s, b = post(json.dumps(
        jrpc(idv, "tools/call", {"name": name, "arguments": args})
    ).encode(), auth)
    try:
        return s, json.loads(b)
    except Exception:
        return s, {"raw": b[:200]}


s, r = call("tools/call", {})
check("tools/call name=tools/call -> sane",
      s in (200, 400, 422) and (r.get("error") or r.get("result")), f"status={s}")

s, r = call("nonexistent_tool", {})
check("unknown tool -> error", r.get("error") or (r.get("result") or {}).get("isError"),
      f"status={s}")

s, r = call("thread_read", {"name": "no-such-thread-zzz"})
res = r.get("result") or {}
check("thread_read bogus name -> isError/graceful",
      s == 200 and (res.get("isError") or res.get("content")), f"status={s}")

s, r = call("episode_search", {"query": "'; DROP TABLE threads;--", "limit": 3})
check("episode_search sqli-ish query -> graceful", s == 200 and r.get("result"),
      f"status={s}")

s, r = call("episode_search", {"query": "mcp", "limit": "not-a-number"})
check("episode_search bad type -> graceful", s in (200, 400, 422), f"status={s}")

s, r = call("thread_list", {"unrelated_arg": 12345})
check("thread_list extra arg -> graceful", s == 200, f"status={s}")

s, r = call("memory_store", {"content": "monkey", "conversation_id": "x"})
err = r.get("error") or {}
check("memory_store read-scope -> refused",
      r.get("error") or (r.get("result") or {}).get("isError"), f"status={s}")

# write tool must not even appear in the read catalog
s, b = post(json.dumps(jrpc(1, "tools/list")).encode(), auth)
names = {t["name"] for t in json.loads(b)["result"]["tools"]}
check("read catalog hides write tools",
      "memory_store" not in names and "episode_append" not in names,
      f"n={len(names)}")

# run_safe_command through MCP with a deny.conf-listed command -> citation
s, r = call("run_safe_command", {"command": "dd" + "cutil detect"})
txt = json.dumps(r)
check("deny.conf command via MCP -> citation",
      "deny.conf" in txt or "denied" in txt.lower() or r.get("error"),
      f"status={s}")

# ---------- concurrency burst ----------

def one_ping(i):
    s, b = post(json.dumps(jrpc(i, "tools/list")).encode(), auth)
    return s == 200 and b'"tools"' in b

with cf.ThreadPoolExecutor(max_workers=16) as ex:
    oks = list(ex.map(one_ping, range(40)))
check("40 concurrent tools/list", all(oks) , f"{sum(oks)}/40 ok")

# session-id must NOT appear (stateless design)
req = urllib.request.Request(
    URL, data=json.dumps(jrpc(1, "initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {}, "clientInfo": {"name": "monkey", "version": "0"},
    })).encode(),
    headers={"Authorization": auth, "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as resp:
    sid = resp.headers.get("mcp-session-id")
check("no mcp-session-id issued", sid is None, f"sid={sid!r}")

fails = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(fails)}/{len(results)} pass" +
      (f" — FAILURES: {fails}" if fails else " — clean"))
