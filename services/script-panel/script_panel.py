#!/usr/bin/env python3
"""script-panel — web control panel for the accumulated script library.

Single-file, stdlib only, no external dependencies. Serves a sortable
catalog UI plus a gated "run" endpoint for scripts explicitly marked
``runnable: true`` in catalog.json.

Endpoints:
  GET  /                  HTML UI
  GET  /api/catalog       catalog entries + live status + last-run merged
  GET  /api/runs          recent run history (tail of runs.jsonl)
  POST /api/run/<id>      execute an allowlisted script, record the result

Safety model (mirrors local_tools_server.py):
  - Binds 127.0.0.1 only.
  - No shell: scripts execute as argv arrays; no user-supplied args.
  - Only catalog entries with runnable:true can execute; sudo-flagged
    entries are never runnable from the panel regardless.
  - confirm:true entries still execute on request — the *UI* shows the
    confirm dialog; the API just runs. Keep the panel localhost-only.
  - Every launch is appended to ~/.local/share/script-panel/runs.jsonl.
"""
from __future__ import annotations

import json
import os
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
tr.missing td{opacity:.45}
</style></head><body>
<h1>Script Panel</h1>
<p class="sub" id="subnote"></p>
<div id="toolbar">
  <input type="search" id="q" placeholder="filter…">
  <span id="chips"></span>
  <span style="flex:1"></span>
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
async function load(){
  const r=await fetch('/api/catalog'); const d=await r.json();
  DATA=d.scripts; CAT=d.categories||{};
  document.getElementById('subnote').textContent=d.note||'';
  renderChips(); renderRows();
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
document.getElementById('q').oninput=e=>{search=e.target.value.toLowerCase();renderRows()};
document.getElementById('refresh').onclick=load;
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; sortDir=sortKey===k?-sortDir:1; sortKey=k; renderRows();});
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
        elif self.path.startswith("/api/catalog"):
            self._json(catalog_with_status())
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
