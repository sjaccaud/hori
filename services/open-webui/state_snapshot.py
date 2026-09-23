#!/usr/bin/env python3
"""Generate a compact AIOS state snapshot for OWUI prompt injection.

Runs on a systemd timer (aios-state-snapshot.timer, every 5 min). Writes:
  - host copy:   ~/.local/state/aios/state-snapshot.md
  - container:   /app/backend/data/aios-state-snapshot.md  (via docker exec,
                 read per-request by the tool-server inlet filter)

Design notes:
- This is *injection*, not retrieval — the filter inserts the file as a
  system message in every request, so current state cannot be missed the
  way a knowledge/RAG document can.
- The snapshot self-reports outages: it probes the :8775 filtered spec so
  a dead mcpo shows up as "DOWN" in the prompt instead of silent absence.
- Stdlib only; every probe is best-effort so partial failures still render.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
THREADS_DIR = HOME / ".local/share/hori/threads"
HOST_OUT = HOME / ".local/state/aios/state-snapshot.md"
CONTAINER_PATH = "/app/backend/data/aios-state-snapshot.md"
CONTAINER = "open_webui_aios"
AIOS_REPO = HOME / "Projects/aios"

SERVICES_SYSTEM = [
    "llamacpp-adaptive",
    "llama-nano-adaptive",
    "local-tools-adaptive",
    "project-tools-adaptive",
    "aios_core",
]
SERVICES_USER = ["hori-mcpo", "hori-mcpo-watchdog.timer", "idea-factory-web", "script-panel"]
CONTAINERS = [
    "open_webui_aios",
    "searchrouter_aios",
    "aios_embedding",
    "qdrant_aios",
    "prometheus_aios",
    "grafana_aios",
]


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except Exception:
        return ""


def _http(url: str, timeout: int = 3) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _systemctl(scope: str, units: list[str]) -> str:
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + [
        "is-active",
        *units,
    ]
    states = _run(cmd).strip().splitlines()
    parts = []
    for i, u in enumerate(units):
        short = u.replace("-adaptive", "")
        s = states[i] if i < len(states) else "?"
        parts.append(short if s == "active" else f"{short}={s}")
    return " ".join(parts)


def _docker_states() -> str:
    out = _run(
        ["docker", "ps", "--format", "{{.Names}} {{.Status}}"], timeout=5
    )
    running = {}
    for line in out.splitlines():
        name, _, status = line.partition(" ")
        running[name] = status
    parts = []
    for c in CONTAINERS:
        short = c.replace("_aios", "")
        if c in running:
            parts.append(short)
        else:
            parts.append(f"{short}=DOWN")
    return " ".join(parts)


def _llama() -> str:
    health = _http("http://127.0.0.1:8080/health", 2)
    loaded = "none"
    models = _http("http://127.0.0.1:8080/v1/models", 3)
    if models:
        for m in models.get("data", []):
            if m.get("status", {}).get("value") == "loaded":
                loaded = m.get("id", "?")
                break
    h = "ok" if (health or {}).get("status") == "ok" else "DOWN"
    return f"llama={h} loaded_model={loaded}"


def _hori_state_tools() -> str:
    spec = _http("http://127.0.0.1:8775/openapi.json", 3)
    if spec is None:
        return "hori_state_tools=DOWN (no :8775 spec — mcpo dead?)"
    n = len(spec.get("paths") or {})
    if n == 0:
        return "hori_state_tools=DOWN (empty spec — mcpo lost MCP session)"
    return f"hori_state_tools=OK ({n} paths)"


def _resources() -> str:
    mem = _run(["free", "-h"]).strip().splitlines()
    mem_s = ""
    if len(mem) >= 2:
        p = mem[1].split()
        if len(p) >= 7:
            mem_s = f"mem {p[6]} avail/{p[1]}"
    disk = _run(["df", "-h", "/"]).strip().splitlines()
    disk_s = ""
    if len(disk) >= 2:
        p = disk[1].split()
        if len(p) >= 5:
            disk_s = f"/ {p[4]} used"
    vram_s = ""
    out = _run(["rocm-smi", "--showmeminfo", "VRAM", "--json"], timeout=5)
    try:
        d = json.loads(out)
        for v in d.values():
            if isinstance(v, dict) and "VRAM Total Memory (B)" in v:
                total = int(v["VRAM Total Memory (B)"]) / 1e9
                used = int(v.get("VRAM Total Used Memory (B)", 0)) / 1e9
                vram_s = f"VRAM {used:.0f}/{total:.0f}G"
                break
    except Exception:
        pass
    return " · ".join(x for x in (mem_s, disk_s, vram_s) if x)


def _deployment_contract() -> str:
    """Run the deployment contract and return its one-line verdict. Checks
    the previous snapshot's age among other things, so a stalled timer
    shows up as FAIL here on the next successful run."""
    script = Path(__file__).with_name("deployment_contract.py")
    try:
        p = subprocess.run(
            [sys.executable, str(script), "--oneline"],
            capture_output=True, text=True, timeout=45,
        )
        return p.stdout.strip() or "deployment_contract=ERROR (no output)"
    except Exception as e:
        return f"deployment_contract=ERROR ({e})"


def _threads() -> list[str]:
    rows = []
    if not THREADS_DIR.is_dir():
        return ["_threads dir missing_"]
    for state in sorted(
        THREADS_DIR.glob("*/state.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        name = state.parent.name
        try:
            head = state.read_text(errors="replace")[:1500]
        except Exception:
            rows.append(f"- {name} — unreadable")
            continue
        status = re.search(r'^status:\s*"?([^"\n]+)"?', head, re.M)
        updated = re.search(r'^updated:\s*"?([^"\n]+)"?', head, re.M)
        meta = ", ".join(
            x for x in (status.group(1).strip() if status else None,
                        f"updated {updated.group(1).strip()}" if updated else None)
            if x
        )
        rows.append(f"- {name}" + (f" — {meta}" if meta else ""))
    return rows


def _git_head() -> str:
    out = _run(
        ["git", "-C", str(AIOS_REPO), "log", "--oneline", "-3"], timeout=5
    ).strip()
    return out or "unavailable"


def render() -> str:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    lines = [
        "# AIOS live state",
        f"generated: {ts}",
        "",
        _llama(),
        f"systemd: {_systemctl('system', SERVICES_SYSTEM)}",
        f"user:    {_systemctl('user', SERVICES_USER)}",
        f"docker:  {_docker_states()}",
        _hori_state_tools(),
        _deployment_contract(),
        f"resources: {_resources()}",
        "",
        "## aios HEAD",
        _git_head(),
        "",
        "## workstreams (hori threads — thread_read for detail)",
        *_threads(),
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    text = render()
    HOST_OUT.parent.mkdir(parents=True, exist_ok=True)
    HOST_OUT.write_text(text)
    # Push into the OWUI volume so the in-container filter can read it.
    p = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "sh", "-c",
         f"cat > {CONTAINER_PATH}.tmp && mv {CONTAINER_PATH}.tmp {CONTAINER_PATH}"],
        input=text, text=True, capture_output=True, timeout=15,
    )
    if p.returncode != 0:
        print(f"container write failed: {p.stderr.strip()}", flush=True)
        return 1
    print(f"snapshot written ({len(text)} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
