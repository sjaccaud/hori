import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Response, Header, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import (
    DESTRUCTIVE_PATTERNS,
    LLM_API_URL,
    LLM_ENABLE_THINKING,
    LLM_MODEL,
    SERVICE_HOST,
    SERVICE_PORT,
)
from .intent import parse_intent
from .memory import ensure_collections, retrieve_memory, retrieve_conversation_turns, store_memory
from .memory_guard import can_store_memory
from .state import build_context_block
from services.tool_daemon.response_verification import verify_response
from .safety_events import verify_and_log
from services.tool_daemon.output_parser import parse_tool_call, strip_tool_call_artifacts
from services.tool_daemon.registry import get_tool_prompt_block
from services.tool_daemon.tool_client import get_tool_client

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_collections()
        logger.info("Qdrant collections ensured")
    except Exception as e:
        logger.warning(f"Qdrant not available on startup: {e}")
    yield


app = FastAPI(
    title="AIOS Core",
    description="The shared intelligence layer",
    lifespan=lifespan,
)

# Mount static files for voice web app
from fastapi.staticfiles import StaticFiles
from pathlib import Path as PathLib
_static_dir = PathLib(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/voice")
async def voice_app():
    """Voice web app - accessible from a phone browser.
    Add to home screen for app-like experience."""
    html_path = _static_dir / "voice.html"
    if html_path.exists():
        return Response(content=html_path.read_text(), media_type="text/html")
    return {"error": "Voice app not found"}


@app.get("/chat")
async def chat_app():
    """Keyboard-first text chat web app (UX-1.2).
    Optimized for laptop typing — one input box, Enter to send,
    streaming response. Uses the existing /v1/chat/completions endpoint.
    Traces to: Manifesto Pillar IV (multimodal), UX Gameplan UX-1.2."""
    html_path = _static_dir / "chat.html"
    if html_path.exists():
        return Response(content=html_path.read_text(), media_type="text/html")
    return {"error": "Chat app not found"}


# --- Models ---

class ChatRequest(BaseModel):
    text: str
    conversation_id: Optional[str] = None
    surface: str = "open_webui"
    context: str = ""


class ChatResponse(BaseModel):
    response: str
    work_order: Optional[Dict[str, Any]] = None
    red_team_report: Optional[Dict[str, Any]] = None
    memory_used: list = []
    conversation_id: str


# --- OpenAI-compatible models ---

class OAIMessage(BaseModel):
    role: str
    content: str

class OAIChatRequest(BaseModel):
    model: str = "aios-core"
    messages: List[OAIMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # Optional: client-supplied conversation ID for chaining turns.
    # If absent, a new UUID is generated (default behaviour). When
    # present, all turns with the same conversation_id are stored
    # under the same Qdrant conversation cluster, which lets the
    # memory consolidation distill multi-turn insights.
    conversation_id: Optional[str] = None
    # Opt-in elastic context window (docs/elastic_context_window.md).
    # When True AND conversation_id is set, the server semantically
    # retrieves past turns from this conversation (filtered Qdrant query
    # on aios_working) and assembles an elastic context: retrieved older
    # turns (chronological) + recent turns + current prompt. Default off
    # — existing clients get _trim_conversation (last 6 messages) and
    # behave identically. Falls back to _trim_conversation on any
    # retrieval failure (embedding server down, no Qdrant, etc.).
    elastic_context: bool = False


# --- Endpoints ---

@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/voice")


@app.get("/health")
async def health():
    return {"status": "ok"}


# --- Admin Panel Auth (PoC 16.3 audit fix) ---
# The admin panel exposes privileged operations: service restart (via
# sudoers), stress test execution, memory consolidation, and log viewing.
# Without auth, any device on the Tailnet can trigger these. The bearer
# token (AIOS_ADMIN_TOKEN from /etc/aios/secrets.env) narrows the trust
# boundary from "Tailnet" to "knows the token". Fail-closed: if no token
# is configured, all admin API calls are rejected.
#
# Traces to: docs/roadmap.md Gate Criteria — unauthenticated privileged
# surface is an unmitigated safety incident.

def _check_admin_auth(authorization: str = Header(None)) -> None:
    """FastAPI dependency: require a valid admin bearer token.

    Fail-closed: if the admin token is not configured, reject with 403.
    If the Authorization header is missing or malformed, reject with 401.
    If the token doesn't match, reject with 403.

    The token is read at call time (not import time) so tests can set
    it via os.environ without reloading the config module.
    """
    import hmac

    # Read at call time: env var takes priority, then config file.
    # This allows tests to set AIOS_ADMIN_TOKEN without reloading config.
    expected = os.environ.get("AIOS_ADMIN_TOKEN") or os.environ.get("HORI_ADMIN_TOKEN")
    if not expected:
        from hori.config import ADMIN_TOKEN
        expected = ADMIN_TOKEN
    if not expected:
        raise HTTPException(403, "Admin API disabled (admin token not set)")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization[7:]
    if not hmac.compare_digest(token, expected):
        raise HTTPException(403, "Invalid admin token")


# --- Admin Panel (Tailscale-only, NOT exposed via Funnel) ---

@app.get("/admin")
async def admin_panel():
    """Admin panel - system vitals, tests, logs, service control.

    SECURITY: This endpoint is reachable from the Tailscale mesh
    (http://localhost:5680/admin) but is NOT exposed via the
    Tailscale Funnel to the public internet. The Funnel hardening
    script (PoC 1.0.1) only proxies /voice, /health, /static, and
    /v1/voice/chat*. /admin and /admin/api/* return 404 from the
    public Funnel URL.
    """
    html_path = _static_dir / "admin.html"
    if html_path.exists():
        return Response(content=html_path.read_text(), media_type="text/html")
    return {"error": "Admin panel not found"}


@app.get("/admin/api/state")
async def admin_system_state(_admin_auth: None = Depends(_check_admin_auth)):
    """System state snapshot for the admin panel. Wraps /system/state."""
    return await system_state()


@app.get("/admin/api/gate")
async def admin_gate_status(_admin_auth: None = Depends(_check_admin_auth)):
    """Gate criteria status for the admin panel.

    Reads the tool audit log and safety events log, computes the same
    metrics as `sudo scripts/audit_review.py --gate`. The audit log is
    permission-separated (root:aios-worker 0620) — if aios-core can't
    read it, this returns what it can (safety events) with a note.
    """
    import json as _json
    from collections import Counter as _Counter

    tool_audit_log = "/var/log/aios/tool_audit.jsonl"
    safety_events_log = "/var/log/aios/safety_events.jsonl"

    def _read_jsonl(path):
        entries = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(_json.loads(line))
                        except _json.JSONDecodeError:
                            continue
        except (FileNotFoundError, PermissionError):
            pass
        return entries

    audit_entries = _read_jsonl(tool_audit_log)
    safety_entries = _read_jsonl(safety_events_log)

    # Sherpa blocks
    sherpa_blocks = [
        e for e in audit_entries
        if isinstance(e.get("result"), dict) and e["result"].get("sherpa_blocked")
    ]
    sherpa_level_3plus = [
        e for e in sherpa_blocks
        if e["result"].get("sherpa_level", 0) >= 3
    ]
    sherpa_level_2 = [
        e for e in sherpa_blocks
        if e["result"].get("sherpa_level", 0) == 2
    ]

    # Hallucination interception rate
    verified_events = [
        e for e in safety_entries
        if e.get("event_type") == "response_verified"
    ]
    legacy_intercepts = [
        e for e in safety_entries
        if e.get("event_type") == "hallucination_intercepted"
    ]
    new_intercepts = [e for e in verified_events if e.get("intercepted")]
    all_intercepts = legacy_intercepts + new_intercepts

    halluc_rate = None
    halluc_claims_without_tool = 0
    if verified_events:
        claims_without_tool = [
            e for e in verified_events
            if e.get("claim_detected") and not e.get("tool_was_called")
        ]
        halluc_claims_without_tool = len(claims_without_tool)
        if claims_without_tool:
            halluc_rate = round(len(new_intercepts) / len(claims_without_tool) * 100, 1)

    # Tool call stats
    tool_counts = _Counter(e.get("tool_name", "?") for e in audit_entries)
    total_calls = len(audit_entries)
    successful_calls = sum(1 for e in audit_entries if e.get("success"))

    return {
        "sherpa_level_3plus": len(sherpa_level_3plus),
        "sherpa_level_2": len(sherpa_level_2),
        "hallucination_interceptions": len(all_intercepts),
        "hallucination_rate": halluc_rate,
        "hallucination_claims_without_tool": halluc_claims_without_tool,
        "verified_events": len(verified_events),
        "tool_calls_total": total_calls,
        "tool_calls_successful": successful_calls,
        "tool_calls_failed": total_calls - successful_calls,
        "tool_counts": dict(tool_counts.most_common()),
        "audit_log_readable": len(audit_entries) > 0 or total_calls == 0,
    }


@app.post("/admin/api/test")
async def admin_run_tests(suite: str = "all", _admin_auth: None = Depends(_check_admin_auth)):
    """Run test suites. Returns streamed output.

    Suites: all, unit, integration, regression, adversarial,
    stress-entropy, stress-safety, stress.
    Runs via subprocess, returns stdout. Timeout 120s (enough
    for the 10-turn smoke stress tests).
    """
    import asyncio as _asyncio

    suite_map = {
        "all": "test",
        "unit": "test-unit",
        "integration": "test-integration",
        "regression": "test-regression",
        "adversarial": "test-adversarial",
        "stress-entropy": "test-stress-entropy",
        "stress-safety": "test-stress-safety",
        "stress": "test-stress",
    }
    target = suite_map.get(suite, "test")
    proc = await _asyncio.create_subprocess_exec(
        "make", target,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.STDOUT,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    try:
        stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=120)
        return {
            "suite": suite,
            "exit_code": proc.returncode,
            "output": stdout.decode("utf-8", errors="replace")[-8000:],
        }
    except _asyncio.TimeoutError:
        proc.kill()
        return {"suite": suite, "exit_code": -1, "output": "TIMEOUT (120s)"}


# --- Background stress runner ---
# For long stress runs (100+ turns) that can't fit in the 120s test
# timeout. Kicks off a detached process, writes output to a log file,
# and the admin panel polls /admin/api/stress/status for progress.

_STRESS_LOG_DIR = Path("/tmp/aios-stress")
_STRESS_PID_FILE = _STRESS_LOG_DIR / "current.pid"
_STRESS_LOG_FILE = _STRESS_LOG_DIR / "current.log"
_STRESS_META_FILE = _STRESS_LOG_DIR / "current.meta.json"


@app.post("/admin/api/stress/start")
async def admin_stress_start(
    _admin_auth: None = Depends(_check_admin_auth),
    test: str = "entropy",
    turns: int = 100,
    delay: float = 1.0,
):
    """Start a background stress test run.

    Args:
        test: "entropy" or "safety"
        turns: number of turns (100-10000)
        delay: delay between turns in seconds

    Returns metadata about the started run. The admin panel polls
    /admin/api/stress/status for progress. Only one stress run at a
    time — returns 409 if one is already running.
    """
    import json as _json
    import subprocess as _subprocess

    if test not in ("entropy", "safety"):
        return {"error": f"Unknown test '{test}' — use 'entropy' or 'safety'"}
    if turns < 1 or turns > 10000:
        return {"error": f"turns must be 1-10000, got {turns}"}

    # Check if a run is already in progress
    if _STRESS_PID_FILE.exists():
        try:
            old_pid = int(_STRESS_PID_FILE.read_text().strip())
            _subprocess.run(
                ["kill", "-0", str(old_pid)],
                capture_output=True, timeout=5,
            )
            # If we get here, the process is still alive
            return {"error": "A stress run is already in progress", "pid": old_pid}
        except (ValueError, FileNotFoundError, _subprocess.CalledProcessError):
            pass  # stale PID file, proceed

    _STRESS_LOG_DIR.mkdir(parents=True, exist_ok=True)

    script = "test_ten_thousand_turns.py" if test == "entropy" else "test_safety_stress.py"
    endpoint_arg = ""
    if test == "safety":
        endpoint_arg = "--endpoint both"

    cmd = (
        f"cd {Path(__file__).resolve().parents[2]} && "
        f"PYTHONPATH=. ./venv/bin/python3 -u tests/stress/{script} "
        f"--turns {turns} --delay {delay} --max-tokens 100 {endpoint_arg}"
    )

    proc = _subprocess.Popen(
        ["bash", "-c", cmd],
        stdout=open(_STRESS_LOG_FILE, "w"),
        stderr=_subprocess.STDOUT,
        start_new_session=True,
    )

    _STRESS_PID_FILE.write_text(str(proc.pid))
    meta = {
        "pid": proc.pid,
        "test": test,
        "turns": turns,
        "delay": delay,
        "script": script,
        "started_at": datetime.now().isoformat(),
        "log_file": str(_STRESS_LOG_FILE),
    }
    _STRESS_META_FILE.write_text(_json.dumps(meta, indent=2))

    return meta


@app.get("/admin/api/stress/status")
async def admin_stress_status(_admin_auth: None = Depends(_check_admin_auth)):
    """Check the status of a background stress run.

    Returns the metadata, whether the process is still running,
    the last 50 lines of output, and the latest checkpoint (if any).
    """
    import json as _json
    import subprocess as _subprocess

    if not _STRESS_META_FILE.exists():
        return {"running": False, "message": "No stress run has been started"}

    meta = _json.loads(_STRESS_META_FILE.read_text())
    pid = meta.get("pid")

    # Check if process is still alive
    running = False
    if pid:
        try:
            _subprocess.run(
                ["kill", "-0", str(pid)],
                capture_output=True, timeout=5,
            )
            running = True
        except (FileNotFoundError, _subprocess.CalledProcessError):
            running = False
            # Clean up stale PID file
            try:
                _STRESS_PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass

    # Read last 50 lines of log
    log_tail = ""
    if _STRESS_LOG_FILE.exists():
        try:
            lines = _STRESS_LOG_FILE.read_text().splitlines()
            log_tail = "\n".join(lines[-50:])
        except Exception:
            pass

    # Find latest checkpoint
    results_dir = Path(__file__).resolve().parents[2] / "tests" / "stress" / "results"
    latest_checkpoint = None
    latest_report = None
    if results_dir.exists():
        checkpoints = sorted(results_dir.glob("checkpoint_turn_*.json"))
        if checkpoints:
            latest_checkpoint = checkpoints[-1].name
        # Find latest report file
        reports = sorted(results_dir.glob("entropy_*turns_*.json"))
        reports += sorted(results_dir.glob("safety_stress_*turns_*.json"))
        if reports:
            latest_report = reports[-1].name

    return {
        "running": running,
        "pid": pid,
        "test": meta.get("test"),
        "turns": meta.get("turns"),
        "started_at": meta.get("started_at"),
        "log_tail": log_tail,
        "latest_checkpoint": latest_checkpoint,
        "latest_report": latest_report,
    }


@app.post("/admin/api/stress/stop")
async def admin_stress_stop(_admin_auth: None = Depends(_check_admin_auth)):
    """Stop a running background stress test."""
    import subprocess as _subprocess

    if not _STRESS_PID_FILE.exists():
        return {"error": "No stress run is in progress"}

    try:
        pid = int(_STRESS_PID_FILE.read_text().strip())
        _subprocess.run(["kill", str(pid)], capture_output=True, timeout=5)
        return {"stopped": True, "pid": pid}
    except Exception as e:
        return {"error": str(e)}
    finally:
        # Clean up PID file so a new run can start
        try:
            _STRESS_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


@app.post("/admin/api/consolidate")
async def admin_trigger_consolidation(max_clusters: int = 10, _admin_auth: None = Depends(_check_admin_auth)):
    """Trigger memory consolidation on-demand.

    This runs the same "sleep & dream" cycle as the nightly systemd
    timer (scripts/memory_consolidation.py) but on-demand. Useful for
    stress testing: run consolidation between checkpoints to see how
    it affects recall quality and state file growth.

    Args:
        max_clusters: max number of conversation clusters to distill
    """
    import asyncio as _asyncio
    import os as _os

    proc = await _asyncio.create_subprocess_exec(
        str(Path(__file__).resolve().parents[2] / "venv" / "bin" / "python3"),
        str(Path(__file__).resolve().parents[2] / "scripts" / "memory_consolidation.py"),
        str(max_clusters),
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.STDOUT,
        cwd=str(Path(__file__).resolve().parents[2]),
        env={
            **_os.environ,
            "PYTHONPATH": ".",
            "LLM_API_URL": "http://localhost:8080/v1/chat/completions",
            "LLM_MODEL": "Qwen3.6-27B",
            "EMBED_URL": "http://localhost:8081/v1/embeddings",
            "QDRANT_URL": "http://localhost:6333",
        },
    )
    try:
        stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=300)
        return {
            "exit_code": proc.returncode,
            "output": stdout.decode("utf-8", errors="replace")[-4000:],
        }
    except _asyncio.TimeoutError:
        proc.kill()
        return {"exit_code": -1, "output": "TIMEOUT (300s)"}


@app.post("/admin/api/service/{action}")
async def admin_service_control(action: str, service: str = "aios_core", _admin_auth: None = Depends(_check_admin_auth)):
    """Restart or check status of a systemd service.

    SECURITY: The service name is validated against an allowlist. The
    sudoers rule (/etc/sudoers.d/aios-admin, installed by
    scripts/hardening/setup_admin_sudoers.sh) allows the user to restart
    only these specific services without a password.

    SELF-RESTART: When restarting aios_core (itself), the response is
    sent BEFORE the restart happens. A detached subprocess sleeps 1.5s
    then runs the restart — enough time for the HTTP response to reach
    the browser before the process is killed.
    """
    import asyncio as _asyncio
    import subprocess as _subprocess

    allowed_services = {"aios_core", "aios-sherpa", "aios-tool-daemon", "llamacpp"}
    if service not in allowed_services:
        return {"error": f"Service '{service}' not in allowlist"}

    if action == "status":
        proc = await _asyncio.create_subprocess_exec(
            "systemctl", "status", service,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=10)
            return {
                "service": service,
                "action": action,
                "exit_code": proc.returncode,
                "output": stdout.decode("utf-8", errors="replace")[-4000:],
            }
        except _asyncio.TimeoutError:
            proc.kill()
            return {"service": service, "action": action, "exit_code": -1, "output": "TIMEOUT"}

    elif action == "restart":
        # For self-restart (aios_core), we must send the response first,
        # then restart in a detached process that sleeps briefly.
        # For other services, we can restart synchronously and return
        # the result.
        is_self = service == "aios_core"
        if is_self:
            # Spawn a detached process: sleep 1.5s, then restart.
            # start_new_session=True detaches it from our process group
            # so it survives when aios-core is killed by the restart.
            _subprocess.Popen(
                ["bash", "-c", f"sleep 1.5 && sudo systemctl restart {service}"],
                start_new_session=True,
                stdout=_subprocess.DEVNULL,
                stderr=_subprocess.DEVNULL,
            )
            return {
                "service": service,
                "action": action,
                "exit_code": 0,
                "output": f"Restarting {service} in 1.5s... (response sent before restart)",
            }
        else:
            proc = await _asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", service,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=30)
                return {
                    "service": service,
                    "action": action,
                    "exit_code": proc.returncode,
                    "output": stdout.decode("utf-8", errors="replace")[-4000:],
                }
            except _asyncio.TimeoutError:
                proc.kill()
                return {"service": service, "action": action, "exit_code": -1, "output": "TIMEOUT"}

    else:
        return {"error": f"Action '{action}' not supported. Use 'restart' or 'status'."}


@app.get("/admin/api/logs/{log_type}")
async def admin_view_logs(log_type: str, lines: int = 50, _admin_auth: None = Depends(_check_admin_auth)):
    """View recent log entries. Types: aios-core, sherpa, tool-daemon, safety.

    Uses journalctl for systemd services, reads files for safety events.
    """
    import asyncio as _asyncio

    log_configs = {
        "aios-core": (["journalctl", "-u", "aios_core", "--no-pager", "-n", str(lines)], None),
        "sherpa": (["journalctl", "-u", "aios-sherpa", "--no-pager", "-n", str(lines)], None),
        "tool-daemon": (["journalctl", "-u", "aios-tool-daemon", "--no-pager", "-n", str(lines)], None),
        "safety": (None, "/var/log/aios/safety_events.jsonl"),
        "tool-audit": (None, "/var/log/aios/tool_audit.jsonl"),
    }

    if log_type not in log_configs:
        return {"error": f"Unknown log type '{log_type}'. Available: {list(log_configs.keys())}"}

    cmd, file_path = log_configs[log_type]

    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            return {
                "log_type": log_type,
                "lines": len(all_lines[-lines:]),
                "content": "".join(all_lines[-lines:]),
            }
        except (FileNotFoundError, PermissionError) as e:
            return {"log_type": log_type, "lines": 0, "content": f"Cannot read: {e}"}

    proc = await _asyncio.create_subprocess_exec(
        *cmd,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=10)
        return {
            "log_type": log_type,
            "lines": lines,
            "content": stdout.decode("utf-8", errors="replace"),
        }
    except _asyncio.TimeoutError:
        proc.kill()
        return {"log_type": log_type, "lines": 0, "content": "TIMEOUT"}


# --- Incident Memory (PoC 12.5) ---

INCIDENTS_FILE = Path(__file__).resolve().parents[2] / "core" / "state" / "incidents.json"


def _load_incidents() -> list:
    if INCIDENTS_FILE.exists():
        try:
            return json.loads(INCIDENTS_FILE.read_text())
        except Exception:
            return []
    return []


def _save_incidents(incidents: list):
    INCIDENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    INCIDENTS_FILE.write_text(json.dumps(incidents[-100:], indent=2))


class IncidentReport(BaseModel):
    service: str
    incident_type: str  # "service_down", "vram_overflow", "restart_attempted", "manual"
    severity: str = "warning"  # "info", "warning", "critical"
    description: str
    action_taken: Optional[str] = None
    resolved: bool = False


@app.post("/system/incident")
async def report_incident(report: IncidentReport):
    """Report an incident to AIOS. Stored in incident memory for later review."""
    incident = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "service": report.service,
        "type": report.incident_type,
        "severity": report.severity,
        "description": report.description,
        "action_taken": report.action_taken,
        "resolved": report.resolved,
    }
    incidents = _load_incidents()
    incidents.append(incident)
    _save_incidents(incidents)
    logger.warning(f"Incident reported: {report.service} - {report.incident_type} - {report.description[:100]}")
    # Also store in working memory so it surfaces in consolidation
    try:
        store_memory(
            content=f"[INCIDENT] {report.service}: {report.description}",
            role="system",
            conversation_id="incidents",
            surface="watchdog",
            tier="working",
        )
    except Exception:
        pass
    return {"status": "logged", "incident_id": incident["id"]}


@app.get("/system/incidents")
async def get_incidents(limit: int = 20, unresolved_only: bool = False):
    """Retrieve recent incidents. Query with ?unresolved_only=true for open issues."""
    incidents = _load_incidents()
    if unresolved_only:
        incidents = [i for i in incidents if not i.get("resolved")]
    return {"incidents": incidents[-limit:], "total": len(incidents)}


@app.post("/system/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str):
    """Mark an incident as resolved."""
    incidents = _load_incidents()
    for inc in incidents:
        if inc.get("id") == incident_id:
            inc["resolved"] = True
            inc["resolved_at"] = datetime.now().isoformat()
            _save_incidents(incidents)
            return {"status": "resolved", "incident_id": incident_id}
    return {"error": "Incident not found"}


# --- Intent Graph (PoC 11.1) ---

GRAPH_PATH = Path(__file__).resolve().parents[2] / "core" / "state" / "intent_graph.json"


def _load_graph() -> dict:
    if GRAPH_PATH.exists():
        return json.loads(GRAPH_PATH.read_text())
    return {"nodes": {}, "edges": [], "stats": {}}


@app.get("/system/graph")
async def get_graph_stats():
    """Return intent graph statistics and structure."""
    graph_data = _load_graph()
    stats = graph_data.get("stats", {})
    # Also compute topic clusters on the fly
    try:
        from .intent_graph import IntentGraph
        ig = IntentGraph()
        if ig.load():
            stats["topic_clusters"] = ig.get_topic_clusters()[:10]
    except Exception:
        pass
    return {
        "stats": stats,
        "node_count": len(graph_data.get("nodes", {})),
        "edge_count": len(graph_data.get("edges", [])),
        "built_at": graph_data.get("built_at", ""),
    }


@app.get("/system/graph/node/{node_id}")
async def get_graph_node(node_id: str, depth: int = 1):
    """Get a node and its neighbors up to depth N."""
    from .intent_graph import IntentGraph
    ig = IntentGraph()
    if not ig.load():
        return {"error": "Graph not built. Run intent_graph build first."}
    return ig.get_neighbors(node_id, depth=depth)


@app.get("/system/graph/topics")
async def get_topic_clusters():
    """Get topic clusters from the intent graph."""
    from .intent_graph import IntentGraph
    ig = IntentGraph()
    if not ig.load():
        return {"error": "Graph not built"}
    return {"clusters": ig.get_topic_clusters()}


@app.post("/system/graph/rebuild")
async def rebuild_graph():
    """Rebuild the intent graph from Qdrant memory."""
    from .intent_graph import IntentGraph
    ig = IntentGraph()
    ig.build_from_qdrant()
    ig.save()
    return {"status": "rebuilt", "stats": ig.get_stats()}


@app.get("/system/state")
async def system_state():
    """
    System state snapshot - answers "how'd that upgrade go?" from a coffee shop.
    Reports service status, VRAM, disk, ROCm version, model loaded, uptime.
    """
    def _check_port(port: int) -> bool:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            return s.connect_ex(("127.0.0.1", port)) == 0

    def _get_vram():
        try:
            result = subprocess.run(
                ["/usr/bin/rocm-smi", "--showmeminfo", "vram"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                total = used = 0
                for line in result.stdout.splitlines():
                    if "VRAM Total Memory" in line:
                        total = int(line.split("(B):")[1].strip())
                    elif "VRAM Total Used" in line:
                        used = int(line.split("(B):")[1].strip())
                if total > 0:
                    return {"total_mb": total // (1024*1024), "used_mb": used // (1024*1024), "percent": round(used/total*100, 1)}
        except Exception:
            pass
        return None

    def _get_rocm_version():
        try:
            result = subprocess.run(["/opt/rocm/bin/hipcc", "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "HIP version:" in line:
                        return line.split("HIP version:")[1].strip()
        except Exception:
            pass
        try:
            result = subprocess.run(["hipcc", "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "HIP version:" in line:
                        return line.split("HIP version:")[1].strip()
        except Exception:
            pass
        return "unknown"

    def _get_loaded_model():
        try:
            resp = httpx.get("http://localhost:8080/v1/models", timeout=5)
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                if models:
                    return models[0].get("id", "unknown")
        except Exception:
            pass
        return "none"

    def _get_disk_usage(path="/"):
        stat = shutil.disk_usage(path)
        return {
            "total_gb": round(stat.total / (1024**3), 1),
            "used_gb": round(stat.used / (1024**3), 1),
            "free_gb": round(stat.free / (1024**3), 1),
            "percent": round(stat.used / stat.total * 100, 1),
        }

    # Get process uptime (approximate from /proc)
    def _get_uptime():
        try:
            with open("/proc/uptime") as f:
                uptime_s = float(f.readline().split()[0])
            hours = int(uptime_s // 3600)
            minutes = int((uptime_s % 3600) // 60)
            return f"{hours}h {minutes}m"
        except Exception:
            return "unknown"

    services = {
        "llama-server": {"port": 8080, "up": _check_port(8080)},
        "aios-core": {"port": 5680, "up": _check_port(5680)},
        "embedding-server": {"port": 8081, "up": _check_port(8081)},
        "qdrant": {"port": 6333, "up": _check_port(6333)},
        "open-webui": {"port": 3000, "up": _check_port(3000)},
    }

    all_up = all(s["up"] for s in services.values())

    return {
        "status": "healthy" if all_up else "degraded",
        "timestamp": datetime.now().isoformat() if 'datetime' in dir() else time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "uptime": _get_uptime(),
        "rocm_version": _get_rocm_version(),
        "gpu": "AMD Radeon AI PRO R9700 (gfx1201, 32GB)",
        "loaded_model": _get_loaded_model(),
        "vram": _get_vram(),
        "disk": _get_disk_usage(),
        "services": services,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    The main intelligence pipeline:
    1. Parse intent -> work_order
    2. Retrieve relevant memory
    3. Build prompt with state context + memory
    4. Red-team gate (if destructive patterns detected)
    5. Call LLM
    6. Persist the turn to memory
    7. Return response
    """
    conv_id = req.conversation_id or str(uuid.uuid4())
    logger.info(f"Chat request from {req.surface} (conv={conv_id}): {req.text[:80]}")

    # 1. Parse intent
    work_order = parse_intent(req.text, req.context)

    # 2. Retrieve memory from all tiers (working for recent context, project/longterm for durable)
    memory_hits = []
    try:
        memory_hits = retrieve_memory(req.text, tier="working", limit=3, score_threshold=0.3) + \
                       retrieve_memory(req.text, tier="project", limit=3, score_threshold=0.5) + \
                       retrieve_memory(req.text, tier="longterm", limit=2, score_threshold=0.5)
    except Exception as e:
        logger.warning(f"Memory retrieval failed: {e}")

    # 3. Build context
    state_context = build_context_block()
    memory_context = "\n".join(
        f"[{m.get('role', '?')}] {m.get('content', '')[:200]}"
        for m in memory_hits
    ) if memory_hits else "(no prior memory)"

    # 4. Red-team gate
    red_team_report = None
    if _is_destructive(req.text):
        logger.warning(f"Destructive pattern detected, invoking red team")
        red_team_report = _run_red_team(work_order or {"description": req.text})
        if red_team_report and red_team_report.get("verdict") in ("REJECTED", "YES_AND"):
            alternatives = red_team_report.get("alternatives", [])
            alt_text = "\n".join(f"  - {a}" for a in alternatives) if alternatives else ""
            response_text = (
                f"**Red Team Verdict: {red_team_report['verdict']}**\n\n"
                f"This request was flagged for review. "
            )
            if red_team_report["verdict"] == "REJECTED":
                response_text += "It cannot proceed as stated."
            else:
                response_text += "It can proceed with modification."
            if alt_text:
                response_text += f"\n\n**Yes, AND alternatives:**\n{alt_text}"
            # Persist the blocked turn
            _persist_turn(req, conv_id, response_text, work_order)
            return ChatResponse(
                response=response_text,
                work_order=work_order,
                red_team_report=red_team_report,
                memory_used=memory_hits,
                conversation_id=conv_id,
            )

    # 5. Call LLM with full context
    tools_available = get_tool_client().is_available()
    system_prompt = _build_system_prompt(
        state_context, memory_context, tools_enabled=tools_available,
    )
    response_text = _call_llm(system_prompt, req.text)

    # PoC 16.1: Check for tool calls and execute if found
    if tools_available:
        # _call_llm is sync, but _maybe_call_tool is async. For the text
        # /chat endpoint, run it in the event loop.
        messages = [OAIMessage(role="user", content=req.text)]
        response_text, tool_was_called = await _maybe_call_tool(
            response_text, system_prompt, messages, conv_id=conv_id,
        )
    else:
        tool_was_called = False

    # Safety net: strip any tool-call artifacts that leaked into the
    # response (parser may miss creative formats the LLM emits). This
    # does NOT execute tools — it just cleans the text for display.
    response_text = strip_tool_call_artifacts(response_text)

    # Anti-hallucination gate (PoC 15.14)
    response_text = verify_and_log(response_text, tool_was_called=tool_was_called,
                                   conversation_id=conv_id, endpoint="/chat")

    # 6. Persist the turn to working memory
    _persist_turn(req, conv_id, response_text, work_order)

    # 7. Return
    return ChatResponse(
        response=response_text,
        work_order=work_order,
        red_team_report=red_team_report,
        memory_used=memory_hits,
        conversation_id=conv_id,
    )


# --- OpenAI-compatible endpoints (so Open WebUI can use aios-core as a backend) ---

@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "aios-core",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "aios",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def openai_chat_completions(req: OAIChatRequest):
    """
    OpenAI-compatible chat completions endpoint.
    Runs the aios-core pipeline: memory retrieval + state context + red-team gate + LLM.
    Supports streaming (stream=true) so tokens appear immediately in Open WebUI.
    Intent parsing is skipped here to avoid doubling latency - it's a background concern.
    """
    user_messages = [m for m in req.messages if m.role == "user"]
    # UX-1.3: set presence to thinking while processing
    await set_presence("thinking")
    if not user_messages:
        return {"error": {"message": "No user message found", "type": "invalid_request_error"}}
    user_text = user_messages[-1].content

    conv_id = req.conversation_id or str(uuid.uuid4())
    logger.info(f"OpenAI-compat chat (conv={conv_id}): {user_text[:80]}")

    # Fast path: skip memory retrieval for very short messages (greetings, acknowledgements)
    is_trivial = len(user_text) < 15 and not any(c in user_text for c in "?@/\\")
    memory_hits = []
    if not is_trivial:
        memory_hits = await _retrieve_memory_batch(user_text)

    # Graph-RAG: find related topics via intent graph traversal
    graph_context = _graph_rag_expand(user_text, memory_hits)

    # State context (fast, file-based)
    state_context = build_context_block()
    memory_context = "\n".join(
        f"[{m.get('role', '?')}] {m.get('content', '')[:200]}"
        for m in memory_hits
    ) if memory_hits else ""
    if graph_context:
        memory_context = (memory_context + "\n" + graph_context) if memory_context else graph_context

    # System status injection: if the user is asking about system health/status,
    # inject the current system state and recent incidents so the LLM can answer naturally.
    system_status_context = _maybe_inject_system_status(user_text)

    # Red-team gate (only for destructive patterns)
    if _is_destructive(user_text):
        logger.warning("Destructive pattern detected, invoking red team")
        red_team_report = _run_red_team({"description": user_text})
        if red_team_report and red_team_report.get("verdict") in ("REJECTED", "YES_AND"):
            alternatives = red_team_report.get("alternatives", [])
            alt_text = "\n".join(f"  - {a}" for a in alternatives) if alternatives else ""
            response_text = f"**Red Team Verdict: {red_team_report['verdict']}**\n\nThis request was flagged for review. "
            if red_team_report["verdict"] == "REJECTED":
                response_text += "It cannot proceed as stated."
            else:
                response_text += "It can proceed with modification."
            if alt_text:
                response_text += f"\n\n**Yes, AND alternatives:**\n{alt_text}"
            asyncio.create_task(_persist_raw_async(user_text, response_text, conv_id, None))
            return _oai_response(response_text, req.model, conv_id)

    # Build system prompt (trimmed if no memory hits)
    system_prompt = _build_system_prompt(state_context, memory_context)
    if system_status_context:
        system_prompt += f"\n\n=== CURRENT SYSTEM STATUS ===\n{system_status_context}"

    # Web search: if the query needs current info, search and inject results
    web_search_context = ""
    if not is_trivial:
        web_search_context = await _maybe_web_search(user_text, memory_hits)
    if web_search_context:
        system_prompt += (
            f"\n\n=== WEB SEARCH RESULTS (CURRENT - USE THIS) ===\n"
            f"{web_search_context}\n"
            f"=== END WEB SEARCH ===\n"
            f"NOTE: The web search results above are current as of today. "
            f"Use them to answer the question. Do not rely on older memory "
            f"if it conflicts with these results."
        )

    # Manage context window: keep only recent turns, summarize older ones
    # This is what makes "turn 10000 = turn 1" - the LLM always gets curated context,
    # not raw history. Memory retrieval + state files carry the distilled knowledge.
    #
    # Elastic context window (docs/elastic_context_window.md): when the
    # client opts in with elastic_context=True AND supplies a
    # conversation_id, retrieve past turns from this conversation
    # semantically instead of using the fixed 6-turn window. This fixes
    # the deflection collapse at long turn counts ("I don't know what
    # 'that' is") — the relevant older context is pulled in by
    # similarity. Default off; falls back to _trim_conversation on any
    # retrieval failure or when the flag isn't set (backwards compat).
    if req.elastic_context and req.conversation_id:
        trimmed_messages = await _elastic_context(req.messages, user_text, conv_id)
    else:
        trimmed_messages = _trim_conversation(req.messages, max_messages=6, max_chars=8000)

    if req.stream:
        async def _stream_with_presence():
            """Wrap _stream_llm to set presence to idle when streaming completes (UX-1.3)."""
            try:
                async for chunk in _stream_llm(system_prompt, trimmed_messages, user_text, conv_id, req.model):
                    yield chunk
            finally:
                await set_presence("idle")
        return StreamingResponse(
            _stream_with_presence(),
            media_type="text/event-stream",
        )

    # Non-streaming: single async LLM call
    response_text = await _call_llm_with_messages(system_prompt, trimmed_messages)
    # Anti-hallucination gate (PoC 15.14): intercept hallucinated action claims
    response_text = verify_and_log(response_text, tool_was_called=False,
                                   conversation_id=conv_id, endpoint="/v1/chat/completions")
    # Persist in background - don't block the response
    asyncio.create_task(_persist_raw_async(user_text, response_text, conv_id, None))
    # UX-1.3: return to idle after response
    await set_presence("idle")
    return _oai_response(response_text, req.model, conv_id)


# --- TTS / Audio (PoC 9.2) ---

class TTSRequest(BaseModel):
    model: str = "kokoro"
    input: str
    voice: str = "bf_emma"
    response_format: str = "wav"
    speed: float = 1.0  # <1.0 = faster, >1.0 = slower


@app.post("/v1/audio/speech")
async def text_to_speech(req: TTSRequest):
    """OpenAI-compatible TTS endpoint. Converts text to speech using Kokoro (CPU)."""
    try:
        from .tts import synthesize_speech
        loop = asyncio.get_event_loop()
        length_scale = 1.0 / req.speed if req.speed != 1.0 else None
        wav_bytes = await loop.run_in_executor(
            None, lambda: synthesize_speech(req.input, req.voice, length_scale=length_scale)
        )
        if not wav_bytes:
            return {"error": {"message": "No audio generated", "type": "tts_error"}}
        return Response(content=wav_bytes, media_type="audio/wav")
    except FileNotFoundError as e:
        return {"error": {"message": f"Voice model not found: {e}", "type": "tts_error"}}
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        return {"error": {"message": str(e), "type": "tts_error"}}


# --- Ambient Presence (UX-1.3) ---
# A lightweight in-memory presence state manager that surfaces AIOS's
# state to the web surfaces (voice.html, chat.html) via SSE.
#
# States: idle, thinking, has_nudge.
# The stream is content-free — it emits state only, never user messages,
# response text, or nudge content. This is a privacy property.
#
# Traces to: Manifesto Pillar V (visible autonomy), Pillar IV (presence),
# UX Gameplan UX-1.3. See docs/ubiquitous_language.md "Ambient Presence".

import asyncio as _asyncio_presence

_presence_state = "idle"
_presence_state_lock = _asyncio_presence.Lock()
_presence_last_change = time.time()
_presence_subscribers: list = []  # asyncio.Queue per subscriber

VALID_PRESENCE_STATES = {"idle", "thinking", "has_nudge"}


async def set_presence(state: str) -> None:
    """Update the presence state and notify all SSE subscribers.

    Rate-limited to 1 change/second (Pillar VII — don't spam the client).
    Invalid transitions are ignored (e.g., thinking -> has_nudge is queued,
    not emitted while thinking).
    """
    global _presence_state, _presence_last_change
    if state not in VALID_PRESENCE_STATES:
        return
    # Don't transition from thinking to has_nudge — queue it
    if _presence_state == "thinking" and state == "has_nudge":
        return
    # Rate limit: skip if last change was <1s ago (and state is the same)
    now = time.time()
    if state == _presence_state and (now - _presence_last_change) < 1.0:
        return
    async with _presence_state_lock:
        _presence_state = state
        _presence_last_change = now
    # Notify all subscribers
    for queue in list(_presence_subscribers):
        try:
            queue.put_nowait(state)
        except _asyncio_presence.QueueFull:
            pass  # drop if subscriber is slow


async def _presence_event_generator():
    """SSE generator that yields presence state changes."""
    queue: _asyncio_presence.Queue = _asyncio_presence.Queue(maxsize=10)
    _presence_subscribers.append(queue)
    try:
        # Send current state immediately on connect
        yield f"event: state\ndata: {{\"state\": \"{_presence_state}\"}}\n\n"
        while True:
            try:
                state = await _asyncio_presence.wait_for(queue.get(), timeout=30.0)
                yield f"event: state\ndata: {{\"state\": \"{state}\"}}\n\n"
            except _asyncio_presence.TimeoutError:
                # Send a keepalive comment
                yield ": keepalive\n\n"
    except _asyncio_presence.CancelledError:
        pass
    finally:
        if queue in _presence_subscribers:
            _presence_subscribers.remove(queue)


@app.get("/v1/presence")
async def presence_stream():
    """SSE stream of AIOS presence state changes (UX-1.3).

    Emits: event: state, data: {"state": "idle"|"thinking"|"has_nudge"}
    Content-free — never emits user messages, response text, or nudge content.
    Rate-limited to 1 change/second.

    Traces to: Manifesto Pillar V (visible autonomy), UX Gameplan UX-1.3.
    """
    return StreamingResponse(
        _presence_event_generator(),
        media_type="text/event-stream",
    )


# --- Wake endpoint (UX-1.1: iOS "Hey AIOS" re-trigger) ---
# The iOS Vocal Shortcut hits this endpoint before opening the voice page.
# The voice page polls GET /v1/wake every 2s; if the wake timestamp is
# newer than the last check, it auto-arms listening. This is more reliable
# than relying on Safari's visibilitychange/focus events, which are
# inconsistent on iOS when the same URL is already open in a tab.
_wake_timestamp = 0.0


@app.post("/v1/wake")
async def wake():
    """Set the wake flag so the voice page auto-arms listening on next poll."""
    global _wake_timestamp
    _wake_timestamp = time.time()
    return {"ok": True, "timestamp": _wake_timestamp}


@app.get("/v1/wake")
async def wake_status():
    """Return the current wake timestamp so the voice page can poll for it."""
    return {"timestamp": _wake_timestamp}


@app.get("/v1/audio/voices")
async def list_tts_voices():
    """List available TTS voices."""
    try:
        from .tts import list_voices
        return {"voices": list_voices()}
    except Exception as e:
        return {"error": str(e)}


# --- Voice Chat (PoC 9.1: text in -> text + audio out) ---

class VoiceChatRequest(BaseModel):
    text: str
    voice: str = "bf_emma"
    speed: float = 1.0  # <1.0 = faster, >1.0 = slower
    history: list = []  # list of {"role": "user"/"assistant", "content": "..."}


@app.post("/v1/voice/chat")
async def voice_chat(req: VoiceChatRequest):
    """Combined endpoint: text in -> aios-core chat -> text response + TTS audio.
    Uses voice mode: conversational system prompt + markdown stripping for TTS.
    Accepts history for conversation continuity across turns.
    Includes web search and system status injection (same as text chat).
    Memory retrieval and web search run in parallel for speed."""
    # 1. Build state context (fast, no I/O)
    state_context = build_context_block()

    # 2. Run memory retrieval and web search in parallel
    # Web search decision is made speculatively from query text alone,
    # then refined with memory hits when they arrive.
    is_trivial = len(req.text) < 15 and not any(c in req.text for c in "?@/\\")

    memory_task = asyncio.create_task(
        _retrieve_memory_batch(req.text) if len(req.text) > 15 else _async_empty()
    )
    # Start web search speculatively based on query + history
    search_task = asyncio.create_task(
        _maybe_web_search(req.text, None, req.history) if not is_trivial else _async_empty_str()
    )

    memory_hits = await memory_task
    try:
        web_search_context = await asyncio.wait_for(search_task, timeout=3.0)
    except asyncio.TimeoutError:
        web_search_context = ""
        search_task.cancel()

    # 3. Build memory context from hits
    graph_context = _graph_rag_expand(req.text, memory_hits)
    memory_context = "\n".join(
        f"[{m.get('role', '?')}] {m.get('content', '')[:200]}"
        for m in memory_hits
    ) if memory_hits else ""
    if graph_context:
        memory_context = (memory_context + "\n" + graph_context) if memory_context else graph_context

    # Check if the tool daemon is available (PoC 16.1)
    tools_available = get_tool_client().is_available()

    system_prompt = _build_system_prompt(
        state_context, memory_context, voice_mode=True, tools_enabled=tools_available,
    )

    # 4. System status injection (if asking about system health)
    system_status_context = _maybe_inject_system_status(req.text)
    if system_status_context:
        system_prompt += f"\n\n=== CURRENT SYSTEM STATUS ===\n{system_status_context}"

    # 5. Add web search results (already retrieved in parallel)
    if web_search_context:
        system_prompt += (
            f"\n\n=== WEB SEARCH RESULTS (CURRENT - USE THIS) ===\n"
            f"{web_search_context}\n"
            f"=== END WEB SEARCH ===\n"
            f"NOTE: The web search results above are current as of today. "
            f"Use them to answer the question. Do not rely on older memory "
            f"if it conflicts with these results."
        )

    # 4. Build messages from history + current text
    messages = []
    for h in req.history[-6:]:  # keep last 6 turns for context
        messages.append(OAIMessage(role=h.get("role", "user"), content=h.get("content", "")))
    messages.append(OAIMessage(role="user", content=req.text))

    conv_id = str(uuid.uuid4())
    response_text = await _call_llm_with_messages(system_prompt, messages)

    # PoC 16.1: Check for tool calls and execute if found
    if tools_available:
        response_text, tool_was_called = await _maybe_call_tool(
            response_text, system_prompt, messages, conv_id=conv_id,
        )
    else:
        tool_was_called = False

    # Safety net: strip any tool-call artifacts that leaked into the
    # response (parser may miss creative formats the LLM emits).
    response_text = strip_tool_call_artifacts(response_text)

    # Anti-hallucination gate (PoC 15.14): intercept hallucinated action claims
    response_text = verify_and_log(response_text, tool_was_called=tool_was_called,
                                   conversation_id=conv_id, endpoint="/v1/voice/chat")

    # Persist in background
    asyncio.create_task(_persist_raw_async(req.text, response_text, conv_id, None))

    # 5. Strip markdown for TTS
    spoken_text = _strip_markdown_for_tts(response_text)

    # 6. Synthesize speech from the cleaned text
    try:
        from .tts import synthesize_speech
        import base64
        loop = asyncio.get_event_loop()
        length_scale = 1.0 / req.speed if req.speed != 1.0 else None
        wav_bytes = await loop.run_in_executor(
            None, lambda: synthesize_speech(spoken_text, req.voice, length_scale=length_scale)
        )
        audio_b64 = base64.b64encode(wav_bytes).decode("utf-8") if wav_bytes else ""
    except Exception as e:
        logger.warning(f"TTS in voice chat failed: {e}")
        audio_b64 = ""

    return {
        "text": response_text,
        "audio": audio_b64,
        "audio_format": "wav",
        "conversation_id": conv_id,
    }


@app.post("/v1/voice/chat/stream")
async def voice_chat_stream(req: VoiceChatRequest):
    """Streaming voice chat: streams text chunks as SSE, with audio chunks
    sent as they're synthesized. The first sentence is synthesized while
    the LLM is still generating the rest, cutting perceived latency."""
    _t0 = time.time()
    # 1. Build context (same as voice_chat but with parallel memory+search)
    state_context = build_context_block()
    is_trivial = len(req.text) < 15 and not any(c in req.text for c in "?@/\\")
    memory_task = asyncio.create_task(
        _retrieve_memory_batch(req.text) if len(req.text) > 15 else _async_empty()
    )
    # Memory is fast (~100ms) and needed for the prompt — wait for it.
    memory_hits = await memory_task
    # Web search is slow (~5-10s). We do NOT block on it — the reactive
    # search flow handles this: if the LLM says "Let me see what I can
    # find," the system searches and does a second turn. Blocking here
    # added a flat 3s penalty on every search-triggered query, and the
    # multi-source search almost never completed within the 3s timeout.
    # See the latency logs: payload built at 3.007s (with block) vs
    # 0.003s (without). Removing this saves 3s on every search query.
    web_search_context = ""

    graph_context = _graph_rag_expand(req.text, memory_hits)
    memory_context = "\n".join(
        f"[{m.get('role', '?')}] {m.get('content', '')[:200]}"
        for m in memory_hits
    ) if memory_hits else ""
    if graph_context:
        memory_context = (memory_context + "\n" + graph_context) if memory_context else graph_context

    # Check if the tool daemon is available (PoC 16.1)
    tools_available = get_tool_client().is_available()

    system_prompt = _build_system_prompt(
        state_context, memory_context, voice_mode=True, tools_enabled=tools_available,
    )
    system_status_context = _maybe_inject_system_status(req.text)
    if system_status_context:
        system_prompt += f"\n\n=== CURRENT SYSTEM STATUS ===\n{system_status_context}"
    if web_search_context:
        system_prompt += (
            f"\n\n=== WEB SEARCH RESULTS (CURRENT - USE THIS) ===\n"
            f"{web_search_context}\n=== END WEB SEARCH ===\n"
        )

    messages = []
    for h in req.history[-6:]:
        messages.append(OAIMessage(role=h.get("role", "user"), content=h.get("content", "")))
    messages.append(OAIMessage(role="user", content=req.text))

    conv_id = str(uuid.uuid4())

    # 2. Stream LLM, collect sentences, synthesize as they complete
    from .tts import synthesize_speech
    import base64 as b64mod
    import json as jsonmod

    async def event_stream():
        full_response = []
        sentence_buffer = ""
        sentence_count = 0
        loop = asyncio.get_event_loop()

        async def _synthesize_and_yield(sentence: str):
            """Synthesize one sentence and yield the audio SSE event."""
            nonlocal sentence_count
            spoken = _strip_markdown_for_tts(sentence)
            length_scale = 1.0 / req.speed if req.speed != 1.0 else None
            wav = await loop.run_in_executor(
                None,
                lambda s=spoken, v=req.voice, ls=length_scale: synthesize_speech(s, v, length_scale=ls)
            )
            if wav:
                audio_b64 = b64mod.b64encode(wav).decode("utf-8")
                idx = sentence_count
                sentence_count += 1
                return f"event: audio\ndata: {jsonmod.dumps({'audio': audio_b64, 'index': idx})}\n\n"
            return None

        def _flush_sentences(buffer: str, first_chunk: bool = False):
            """Split buffer at sentence boundaries, yield text+audio SSE.
            Returns (list_of_sse_strings, remaining_buffer).
            For first_chunk, also split on commas/semicolons to get audio
            flowing sooner (latency optimization)."""
            results = []
            boundaries = [". ", "! ", "? ", ".\n", "!\n", "?\n"]
            if first_chunk:
                # For the first audio chunk, also accept comma/semicolon breaks
                # to start TTS sooner. This cuts time-to-first-audio by ~0.5-1s.
                boundaries += [", ", "; ", " — ", " - "]
            while any(p in buffer for p in boundaries):
                min_idx = len(buffer)
                for p in boundaries:
                    idx = buffer.find(p)
                    if idx != -1 and idx < min_idx:
                        min_idx = idx
                # Determine boundary length (most are 2 chars)
                boundary_len = 2
                sentence = buffer[:min_idx + boundary_len].strip()
                buffer = buffer[min_idx + boundary_len:]
                min_len = 8 if first_chunk else 10
                if len(sentence) >= min_len:
                    results.append(sentence)
            return results, buffer

        # --- Unified streaming path ---
        # Always stream the LLM. If tools are available, buffer the first ~80
        # chars to check for a {"tool_call": pattern. If found, consume the
        # rest of the stream, execute the tool, and stream the second response.
        # If not found, flush the buffer and continue streaming normally.
        # This cuts time-to-first-text from ~5s (non-streaming) to ~1-2s.
        payload = _llm_payload(system_prompt, messages, stream=True)
        logger.info(f"VOICE LATENCY: LLM payload built at {time.time()-_t0:.3f}s (prompt={len(system_prompt)} chars, tools={tools_available})")

        TOOL_CALL_MARKER = '{"tool_call"'
        tool_call_detected = False
        full_llm_response = ""
        # Rolling buffer: hold back the last N chars to detect tool_call
        # markers before they're streamed to the client. The marker is
        # 13 chars, so we hold back 15 to be safe.
        HOLD_BACK = 15
        pending_buffer = ""

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                async with client.stream("POST", LLM_API_URL, json=payload) as response:
                    response.raise_for_status()
                    logger.info(f"VOICE LATENCY: LLM stream connected at {time.time()-_t0:.3f}s")

                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = jsonmod.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            text = delta.get("content", "")
                        except Exception:
                            continue
                        if not text:
                            continue

                        full_llm_response += text

                        if tools_available and not tool_call_detected:
                            # Check if the full response so far contains a tool call
                            if TOOL_CALL_MARKER in full_llm_response or \
                               TOOL_CALL_MARKER in full_llm_response.replace(' ', ''):
                                tool_call_detected = True
                                # Don't break — continue reading the stream to
                                # collect the full tool call JSON, but stop
                                # sending text/audio to the client.
                                continue

                            # Hold back last N chars to detect marker at boundary
                            pending_buffer += text
                            if len(pending_buffer) <= HOLD_BACK:
                                continue  # not enough to flush
                            # Flush everything except the hold-back tail
                            flush_text = pending_buffer[:-HOLD_BACK]
                            pending_buffer = pending_buffer[-HOLD_BACK:]
                            text = flush_text

                        # Stream text to client
                        if not tool_call_detected:
                            if not full_response:
                                logger.info(f"VOICE LATENCY: LLM first token at {time.time()-_t0:.3f}s")
                            full_response.append(text)
                            sentence_buffer += text
                            yield f"event: text\ndata: {jsonmod.dumps({'text': text})}\n\n"

                            sentences, sentence_buffer = _flush_sentences(
                                sentence_buffer, first_chunk=(sentence_count == 0)
                            )
                            for s in sentences:
                                audio_evt = await _synthesize_and_yield(s)
                                if audio_evt:
                                    yield audio_evt

                    # Stream ended — flush remaining pending buffer
                    # (unless a tool call was detected, in which case it's
                    # part of the tool call JSON and should not be sent)
                    if not tool_call_detected and pending_buffer:
                        if not full_response:
                            logger.info(f"VOICE LATENCY: LLM first token at {time.time()-_t0:.3f}s")
                        full_response.append(pending_buffer)
                        sentence_buffer += pending_buffer
                        yield f"event: text\ndata: {jsonmod.dumps({'text': pending_buffer})}\n\n"
                        sentences, sentence_buffer = _flush_sentences(
                            sentence_buffer, first_chunk=(sentence_count == 0)
                        )
                        for s in sentences:
                            audio_evt = await _synthesize_and_yield(s)
                            if audio_evt:
                                yield audio_evt
                        pending_buffer = ""

        except Exception as e:
            logger.error(f"Voice stream failed: {e}")
            yield f"event: error\ndata: {jsonmod.dumps({'error': str(e)})}\n\n"
            return

        # --- Handle tool call if detected ---
        if tool_call_detected:
            # The natural language before the tool call was already streamed.
            # Now execute the tool and stream the second LLM response.
            pre_tool_text = "".join(full_response)  # text already sent to client
            final_response, tool_was_called = await _maybe_call_tool(
                full_llm_response, system_prompt, messages, conv_id=conv_id,
            )
            final_response = strip_tool_call_artifacts(final_response)
            sentences = _split_sentences(final_response)
            for sentence in sentences:
                if not sentence.strip():
                    continue
                yield f"event: text\ndata: {jsonmod.dumps({'text': sentence + ' '})}\n\n"
                audio_evt = await _synthesize_and_yield(sentence)
                if audio_evt:
                    yield audio_evt

            full_text = strip_tool_call_artifacts(pre_tool_text) + final_response
            verified_text = verify_and_log(full_text, tool_was_called=True,
                                           conversation_id=conv_id, endpoint="/v1/voice/chat/stream")
            asyncio.create_task(_persist_raw_async(req.text, full_text, conv_id, None))
            yield f"event: done\ndata: {jsonmod.dumps({'conversation_id': conv_id, 'text': full_text})}\n\n"
            return

        # --- Finish normal streaming ---
        # Synthesize any remaining text
        if sentence_buffer.strip() and len(sentence_buffer.strip()) > 3:
            audio_evt = await _synthesize_and_yield(sentence_buffer.strip())
            if audio_evt:
                yield audio_evt

        full_text = "".join(full_response)

        # --- Reactive web search ---
        # If the LLM said "let me search" or similar but no search was already
        # done, do the search now and feed results back for a second response.
        # This creates a natural two-turn flow: "let me see what I can find"
        # → [searches] → "here's what I found..."
        if not web_search_context and _detect_search_intent(full_text):
            logger.info(f"Reactive search triggered by LLM response: {full_text[:80]!r}")
            yield f"event: searching\ndata: {jsonmod.dumps({'query': req.text})}\n\n"

            reactive_search = await _maybe_web_search(req.text, None, req.history, force=True)
            if reactive_search:
                # Reset audio index for the second response so the client
                # (which clears its queue on the 'searching' event) can start
                # from index 0 cleanly.
                sentence_count = 0
                # Second LLM turn with search results
                second_prompt = system_prompt + (
                    f"\n\n=== WEB SEARCH RESULTS (CURRENT - USE THIS) ===\n"
                    f"{reactive_search}\n=== END WEB SEARCH ===\n"
                )
                second_messages = list(messages) + [
                    OAIMessage(role="assistant", content=full_text),
                    OAIMessage(role="user", content="(The web search has completed. Please answer using the search results above.)"),
                ]

                second_payload = _llm_payload(second_prompt, second_messages, stream=True)
                second_response = []
                second_sentence_buffer = ""

                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                        async with client.stream("POST", LLM_API_URL, json=second_payload) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if not line or not line.startswith("data: "):
                                    continue
                                data = line[6:]
                                if data == "[DONE]":
                                    break
                                try:
                                    chunk = jsonmod.loads(data)
                                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                                    text = delta.get("content", "")
                                except Exception:
                                    continue
                                if not text:
                                    continue

                                second_response.append(text)
                                second_sentence_buffer += text
                                yield f"event: text\ndata: {jsonmod.dumps({'text': text})}\n\n"

                                sentences, second_sentence_buffer = _flush_sentences(
                                    second_sentence_buffer, first_chunk=(sentence_count == 0)
                                )
                                for s in sentences:
                                    audio_evt = await _synthesize_and_yield(s)
                                    if audio_evt:
                                        yield audio_evt

                except Exception as e:
                    logger.error(f"Reactive search second turn failed: {e}")

                # Flush remaining second response
                if second_sentence_buffer.strip() and len(second_sentence_buffer.strip()) > 3:
                    audio_evt = await _synthesize_and_yield(second_sentence_buffer.strip())
                    if audio_evt:
                        yield audio_evt

                second_text = "".join(second_response)
                full_text = full_text + " " + second_text

        verified_text = verify_and_log(full_text, tool_was_called=False,
                                       conversation_id=conv_id, endpoint="/v1/voice/chat/stream")
        if verified_text != full_text:
            yield f"event: correction\ndata: {jsonmod.dumps({'original': full_text, 'replacement': verified_text})}\n\n"
            full_text = verified_text
        asyncio.create_task(_persist_raw_async(req.text, full_text, conv_id, None))
        yield f"event: done\ndata: {jsonmod.dumps({'conversation_id': conv_id, 'text': full_text})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/v1/voice/chat/audio")
async def voice_chat_audio(req: VoiceChatRequest):
    """Same as /v1/voice/chat but returns raw WAV audio directly.
    Uses voice mode: conversational system prompt + markdown stripping for TTS.
    Memory retrieval and web search run in parallel for speed."""
    # 1. Build state context
    state_context = build_context_block()

    # 2. Parallel memory + web search
    is_trivial = len(req.text) < 15 and not any(c in req.text for c in "?@/\\")
    memory_task = asyncio.create_task(
        _retrieve_memory_batch(req.text) if len(req.text) > 15 else _async_empty()
    )
    search_task = asyncio.create_task(
        _maybe_web_search(req.text, None, req.history) if not is_trivial else _async_empty_str()
    )

    memory_hits = await memory_task
    try:
        web_search_context = await asyncio.wait_for(search_task, timeout=3.0)
    except asyncio.TimeoutError:
        web_search_context = ""
        search_task.cancel()

    # 3. Build memory context
    graph_context = _graph_rag_expand(req.text, memory_hits)
    memory_context = "\n".join(
        f"[{m.get('role', '?')}] {m.get('content', '')[:200]}"
        for m in memory_hits
    ) if memory_hits else ""
    if graph_context:
        memory_context = (memory_context + "\n" + graph_context) if memory_context else graph_context

    system_prompt = _build_system_prompt(state_context, memory_context, voice_mode=True)

    # 4. System status
    system_status_context = _maybe_inject_system_status(req.text)
    if system_status_context:
        system_prompt += f"\n\n=== CURRENT SYSTEM STATUS ===\n{system_status_context}"

    # 5. Web search results (already retrieved)
    if web_search_context:
        system_prompt += (
            f"\n\n=== WEB SEARCH RESULTS (CURRENT - USE THIS) ===\n"
            f"{web_search_context}\n"
            f"=== END WEB SEARCH ===\n"
            f"NOTE: The web search results above are current as of today. "
            f"Use them to answer the question. Do not rely on older memory "
            f"if it conflicts with these results."
        )

    # 3. Build messages from history + current text
    messages = []
    for h in req.history[-6:]:
        messages.append(OAIMessage(role=h.get("role", "user"), content=h.get("content", "")))
    messages.append(OAIMessage(role="user", content=req.text))

    response_text = await _call_llm_with_messages(system_prompt, messages)
    # Anti-hallucination gate (PoC 15.14): intercept hallucinated action claims
    response_text = verify_and_log(response_text, tool_was_called=False,
                                   endpoint="/v1/voice/chat")
    conv_id = str(uuid.uuid4())
    asyncio.create_task(_persist_raw_async(req.text, response_text, conv_id, None))

    # 4. Strip markdown for TTS
    spoken_text = _strip_markdown_for_tts(response_text)

    # 5. Synthesize speech and return raw WAV
    try:
        from .tts import synthesize_speech
        loop = asyncio.get_event_loop()
        length_scale = 1.0 / req.speed if req.speed != 1.0 else None
        wav_bytes = await loop.run_in_executor(
            None, lambda: synthesize_speech(spoken_text, req.voice, length_scale=length_scale)
        )
        if wav_bytes:
            return Response(content=wav_bytes, media_type="audio/wav",
                          headers={"X-AIOS-Text": response_text[:200]})
    except Exception as e:
        logger.warning(f"TTS in voice chat audio failed: {e}")

    return {"text": response_text, "audio": None}


async def _retrieve_memory_batch(query: str) -> list:
    """Retrieve from all 3 tiers using a single embedding call."""
    try:
        from .memory import retrieve_memory
        # Query all 3 tiers with appropriate thresholds
        tiers = [
            ("working", 0.3, 3),
            ("project", 0.5, 3),
            ("longterm", 0.5, 2),
        ]
        results = []
        for tier, threshold, limit in tiers:
            hits = retrieve_memory(query, tier=tier, limit=limit, score_threshold=threshold)
            results.extend(hits)
        return results
    except Exception as e:
        logger.warning(f"Memory retrieval failed: {e}")
        return []


def _llm_payload(system_prompt: str, messages: List[OAIMessage], stream: bool = False) -> dict:
    """Build LLM payload with optional thinking mode control."""
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": system_prompt}] + [
            {"role": m.role, "content": m.content} for m in messages
        ],
        "stream": stream,
        "max_tokens": 500,
    }
    if not LLM_ENABLE_THINKING:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


async def _stream_llm(system_prompt: str, messages: List[OAIMessage], user_text: str, conv_id: str, model: str):
    """Stream LLM tokens as SSE events using async httpx, then persist the full response.
    Forwards all delta fields (including reasoning_content) so reasoning models show progress."""
    full_response = []
    try:
        payload = _llm_payload(system_prompt, messages, stream=True)
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            async with client.stream("POST", LLM_API_URL, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_response.append(content)
                            # Forward all chunks (including reasoning_content) to the client
                            yield f"data: {json.dumps(chunk)}\n\n"
                        except json.JSONDecodeError:
                            continue

        yield "data: [DONE]\n\n"
    except Exception as e:
        logger.error(f"Streaming LLM failed: {e}")
        error_chunk = {
            "choices": [{"delta": {"content": f"\n\n[Error: {e}]"}, "finish_reason": None}]
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    # Persist after streaming completes (in background, don't block)
    response_text = "".join(full_response)
    asyncio.create_task(_persist_raw_async(user_text, response_text, conv_id, None))


def _oai_response(content: str, model: str, conv_id: str) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{conv_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _persist_raw(user_text: str, response_text: str, conv_id: str, work_order):
    try:
        wo_id = work_order.get("id") if work_order else None
        # Memory poisoning guard (red-team fix #5): only store if not
        # tool-originated data without user confirmation. Normal conversation
        # messages (user/assistant) pass through; tool results would be blocked.
        user_data = {"source": "user", "role": "user", "content": user_text}
        assistant_data = {"source": "assistant", "role": "assistant", "content": response_text}
        if can_store_memory(user_data, user_confirmed=True):
            store_memory(content=user_text, role="user", conversation_id=conv_id, surface="open_webui", tier="working", work_order_id=wo_id)
        if can_store_memory(assistant_data, user_confirmed=True):
            store_memory(content=response_text, role="assistant", conversation_id=conv_id, surface="open_webui", tier="working", work_order_id=wo_id)
    except Exception as e:
        logger.warning(f"Memory persistence failed: {e}")


async def _persist_raw_async(user_text: str, response_text: str, conv_id: str, work_order):
    """Run _persist_raw in a thread pool so it doesn't block the event loop."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _persist_raw, user_text, response_text, conv_id, work_order)


async def _async_empty():
    """Return empty list asynchronously (for parallel task patterns)."""
    return []


async def _async_empty_str():
    """Return empty string asynchronously (for parallel task patterns)."""
    return ""


# --- Helpers ---

def _is_destructive(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in DESTRUCTIVE_PATTERNS)


def _run_red_team(action: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the red-teaming engine."""
    try:
        from services.red_teaming.engine import RedTeamingEngine
        engine = RedTeamingEngine()
        return engine.evaluate_action(action)
    except Exception as e:
        logger.error(f"Red team invocation failed: {e}")
        return {"verdict": "ERROR", "alternatives": [], "error": str(e)}


async def _maybe_web_search(user_text: str, memory_hits: list, conversation_history: list = None, force: bool = False) -> str:
    """Check if the query needs web search, and if so, search and return context.
    Pass conversation_history so follow-up questions in a tech conversation
    also trigger fresh searches.

    If force=True, skip the needs_web_search heuristic and search regardless.
    Used by reactive search (when the LLM says 'let me see what I can find').

    Uses multi-source fan-out: DuckDuckGo + arXiv + GitHub + Reddit + HN +
    Semantic Scholar, all queried in parallel. See multi_search.py."""
    try:
        from .web_search import needs_web_search
        from .multi_search import multi_search, format_results_for_llm
        if not force and not needs_web_search(user_text, memory_hits, conversation_history):
            return ""

        # Build a richer search query if we have conversation context
        search_query = user_text
        if conversation_history and len(user_text) < 50:
            # Short follow-up: combine with recent USER messages for better search.
            # Only include user messages — assistant responses (e.g. "Let me see
            # what I can find...") pollute the query and make searches useless.
            recent = conversation_history[-4:] if len(conversation_history) >= 4 else conversation_history
            context_text = " ".join(
                m.get("content", "") for m in recent
                if isinstance(m, dict) and m.get("role") == "user"
            )
            if context_text:
                search_query = f"{context_text[:200]} {user_text}"

        logger.info(f"Multi-source search triggered for: {user_text[:80]} (query: {search_query[:80]})")

        # Fan out to all 6 sources in parallel
        results = await multi_search(search_query, max_results=8)
        if not results:
            logger.info("Multi-search returned no results from any source")
            return ""

        # Format results and summarize with the LLM
        context = format_results_for_llm(results)
        system_prompt = (
            "You are HORI, a local-first AI assistant. The user asked a question that requires "
            "current information from the web. Below are search results from multiple sources "
            "(web, academic papers, code repos, and community discussions). Summarize the key "
            "findings and answer the user's question directly. Be concise (2-4 sentences). "
            "Cite sources by number [1], [2], etc. If the search results don't answer the "
            "question, say so honestly. Do not mention that you searched the web - just answer naturally."
        )
        user_prompt = f"Question: {user_text}\n\nSearch results:\n{context}"

        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            None,
            lambda: _llm_call_simple(system_prompt, user_prompt)
        )

        if summary:
            # Persist the search result to memory for future recall
            try:
                store_memory(
                    content=f"[WEB SEARCH] Query: {user_text}\nResult: {summary[:500]}",
                    role="system",
                    conversation_id="web_search",
                    surface="web_search",
                    tier="working",
                    topics=["web_search", "current_info"],
                )
            except Exception:
                pass

            sources_text = "\n".join(
                f"  [{i+1}] ({r.source}) {r.title} - {r.url}" for i, r in enumerate(results[:5])
            )
            return f"{summary}\n\nSources:\n{sources_text}"
    except Exception as e:
        logger.warning(f"Multi-source web search failed: {e}")
    return ""


# Phrases that indicate the LLM wants to search but hasn't yet.
# Used by reactive search: if the first LLM response contains these
# and no search was already done, trigger a search and do a second turn.
# IMPORTANT: Only include explicit search-intent phrases. Do NOT include
# "I don't know" or "I can't answer" — those are genuine refusals for
# philosophical/opinion questions, not search intent. Including them
# causes the system to search for every "I don't know" response, adding
# 10+ seconds of latency to conversations that don't need search.
_SEARCH_INTENT_PHRASES = [
    "let me search",
    "let me look that up",
    "let me see what i can find",
    "let me find out",
    "let me check the web",
    "let me check online",
    "i'll search",
    "i'll look that up",
    "let me see if i can find",
    "i'd need to search",
    "let me try to find",
]


def _detect_search_intent(text: str) -> bool:
    """Check if the LLM response indicates it wants to search but hasn't.

    Triggers on phrases like 'let me search', 'let me look that up', etc.
    Also triggers on 'I don't know' style responses to external questions,
    but only if the response is short (under 150 chars) — long responses
    with 'I don't know' are usually genuine refusals, not search intent.
    """
    if not text:
        return False
    lower = text.lower().strip()
    # Direct search intent phrases — always trigger
    for phrase in _SEARCH_INTENT_PHRASES:
        if phrase in lower:
            return True
    return False


def _llm_call_simple(system_prompt: str, user_prompt: str) -> str:
    """Simple synchronous LLM call for web search summarization."""
    try:
        resp = httpx.post(
            LLM_API_URL,
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "max_tokens": 300,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"LLM call for web search failed: {e}")
        raise


def _graph_rag_expand(user_text: str, memory_hits: list) -> str:
    """Graph-RAG: Use the intent graph to find related topics and memories
    that semantic search might miss. Traverses co-occurrence edges from
    topics found in the initial memory hits."""
    try:
        from .intent_graph import IntentGraph
        ig = IntentGraph()
        if not ig.load():
            return ""

        # Collect topics from memory hits
        topics_found = set()
        for hit in memory_hits:
            for topic in hit.get("topics", []):
                topics_found.add(f"topic:{topic.lower()}")

        if not topics_found:
            return ""

        # Traverse co-occurrence edges to find related topics
        related_topics = set()
        for edge in ig.edges:
            if edge.source in topics_found and edge.type == "co_occurs_with":
                related_topics.add(edge.target)
            elif edge.target in topics_found and edge.type == "co_occurs_with":
                related_topics.add(edge.source)

        # Remove topics we already found
        new_topics = related_topics - topics_found
        if not new_topics:
            return ""

        # Find memories that have these related topics
        related_memories = []
        for node_id, node in ig.nodes.items():
            if node.type != "memory":
                continue
            # Check if this memory has any of the new topics
            for edge in ig.edges:
                if edge.source == node_id and edge.target in new_topics and edge.type == "has_topic":
                    related_memories.append(node)
                    break

        if not related_memories:
            return ""

        # Format as context
        topic_labels = [ig.nodes[t].label for t in new_topics if t in ig.nodes]
        lines = [f"[graph-rag] Related topics: {', '.join(topic_labels[:5])}"]
        for mem in related_memories[:3]:
            lines.append(f"  [{mem.metadata.get('role', '?')}] {mem.label}")

        logger.info(f"Graph-RAG expanded: {len(topic_labels)} related topics, {len(related_memories)} related memories")
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Graph-RAG expansion failed: {e}")
        return ""


def _maybe_inject_system_status(user_text: str) -> str:
    """If the user is asking about system status, return a context block with current state + incidents.
    This enables 'how'd that upgrade go?' type questions from a coffee shop."""
    status_keywords = [
        "how'd that", "how did that", "how'd it", "how did it",
        "system status", "system health", "what's running", "what is running",
        "is the server", "is everything", "any incidents", "any issues",
        "what happened", "did the upgrade", "did the update",
        "service status", "are you ok", "are you running",
        "vram", "gpu usage", "disk space", "what model",
        "rocm", "what version", "system state",
    ]
    lower = user_text.lower()
    if not any(kw in lower for kw in status_keywords):
        return ""

    # Gather current system state (inline, not via HTTP to avoid recursion)
    import socket
    def _check_port(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            return s.connect_ex(("127.0.0.1", port)) == 0

    parts = []
    services = {
        "llama-server": _check_port(8080),
        "aios-core": _check_port(5680),
        "embedding-server": _check_port(8081),
        "qdrant": _check_port(6333),
        "open-webui": _check_port(3000),
    }
    up = [k for k, v in services.items() if v]
    down = [k for k, v in services.items() if not v]
    parts.append(f"Services up: {', '.join(up) or 'none'}")
    if down:
        parts.append(f"Services DOWN: {', '.join(down)}")

    # Recent incidents
    incidents = _load_incidents()
    if incidents:
        recent = incidents[-5:]
        parts.append(f"Recent incidents ({len(incidents)} total):")
        for inc in recent:
            status = "resolved" if inc.get("resolved") else "OPEN"
            parts.append(f"  - [{status}] {inc.get('service', '?')}: {inc.get('description', '?')[:100]}")
    else:
        parts.append("No incidents recorded.")

    return "\n".join(parts)


def _build_system_prompt(state_context: str, memory_context: str, voice_mode: bool = False, tools_enabled: bool = False) -> str:
    from hori.persona import build_system_prompt
    return build_system_prompt(
        state_context=state_context,
        memory_context=memory_context,
        voice_mode=voice_mode,
        tools_enabled=tools_enabled,
    )
    return prompt


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences for streaming TTS.
    Handles common sentence boundaries: . ! ? followed by space or newline."""
    import re
    # Split on sentence boundaries, keeping the delimiter
    parts = re.split(r'([.!?]+(?:\s|\n|$))', text)
    sentences = []
    for i in range(0, len(parts) - 1, 2):
        sentence = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
        sentence = sentence.strip()
        if sentence:
            sentences.append(sentence)
    # Don't forget any trailing text without a sentence boundary
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1].strip())
    return sentences


def _strip_markdown_for_tts(text: str) -> str:
    """Strip markdown formatting from text before sending to TTS.
    Removes asterisks, hashes, backticks, brackets, and cleans up
    so the speech engine doesn't read out formatting characters."""
    import re
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Remove headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove link URLs, keep text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove image markers
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", text)
    # Remove horizontal rules
    text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
    # Remove blockquote markers
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    # Remove list markers
    text = re.sub(r"^[\s]*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Simplify common technical patterns for speech
    # .gguf, .onnx etc -> just say "model"
    text = re.sub(r"\b[\w.-]+\.gguf\b", "that model", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[\w.-]+\.onnx\b", "that model", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[\w.-]+\.(py|js|ts|json|yaml|yml|toml|md|txt|sh)\b", "that file", text, flags=re.IGNORECASE)
    # Remove file paths (but not shell redirections like 2>/dev/null)
    text = re.sub(r"(?<![<>\d])/[\w.-]+(?:/[\w.-]+)+", "that file", text)
    # "vs" -> "compared to"
    text = re.sub(r"\bvs\.?\b", "compared to", text, flags=re.IGNORECASE)
    # "e.g." -> "for example"
    text = re.sub(r"\be\.g\.\s*", "for example, ", text, flags=re.IGNORECASE)
    text = re.sub(r"\beg\.\s*", "for example, ", text, flags=re.IGNORECASE)
    # "i.e." -> "that is"
    text = re.sub(r"\bi\.e\.\s*", "that is, ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bie\.\s*", "that is, ", text, flags=re.IGNORECASE)
    # Clean up extra whitespace and newlines
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\s+", " ", text)
    # Remove trailing punctuation noise
    text = re.sub(r"\s*([.!?])\s*([.!?])+", r"\1", text)
    return text.strip()


# --- Vague-query enrichment (context_reference prompts) -------------------
# Context_reference prompts like "Tell me more about that." or "Connect that
# to the memory system." are pronoun-heavy: their meaning lives in the
# previous turn, not the prompt itself. Their embedding is therefore generic
# and matches nothing in Qdrant — exactly the prompts that need retrieval
# most get the least of it. This is docs/elastic_context_window.md "Remaining
# Issues" #3 (retrieval quality depends on the embedding model having
# something specific to embed).
#
# The fix: detect such prompts and enrich the query with the previous
# SUBSTANTIVE assistant reply so the embedding has a semantic anchor. The
# enriched text is used ONLY for the Qdrant query — the LLM still sees the
# raw user prompt.
#
# CRITICAL: the anchor must be a substantive reply, not a deflection. If the
# previous assistant reply is "I don't have access to past conversations",
# anchoring on it retrieves MORE deflections from Qdrant — a positive feedback
# loop that makes the deflection collapse worse. We scan backwards past
# deflections to find the last reply that actually contains content.

# Demonstrative pronouns / generic nouns that signal an anaphoric reference.
_VAGUE_DEICTICS = frozenset({
    "that", "this", "it", "they", "them", "those", "these",
    "one", "thing", "things", "stuff", "above",
})

# Function words stripped before counting content words. Kept deliberately
# small — only words that carry no topical signal. Specific nouns ("memory",
# "architecture", "system") are intentionally absent so they count as content.
_VAGUE_STOPWORDS = _VAGUE_DEICTICS | frozenset({
    # articles / prepositions / conjunctions
    "a", "an", "the", "to", "of", "in", "on", "at", "for", "with", "about",
    "into", "from", "by", "as", "and", "or", "but", "so", "than",
    # personal pronouns
    "we", "you", "i", "me", "my", "our", "your", "us", "ourself",
    # filler / auxiliary verbs
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "doing", "have", "has", "had",
    "tell", "say", "said", "says", "more", "please",
    "can", "could", "would", "should", "will", "shall", "may", "might",
    "just", "now", "then", "earlier", "before", "again", "back",
    "discussed", "discuss", "mentioned", "mention", "talked", "talk",
    "elaborate", "expand", "continue", "connect", "relate", "go",
    "what", "how", "why", "who", "when", "where",
})

# Phrases that signal an assistant reply is a deflection, not substantive
# content. Used by _is_deflection to skip deflections when choosing an
# enrichment anchor AND to filter deflections out of retrieved hits. Covers
# both contracted ("don't") and uncontracted ("do not") forms. Traces to the
# deflection collapse observed in the 500-turn stress test at turns 100+.
_DEFLECTION_PHRASES = [
    "i don't know",
    "i do not know",
    "i don't remember",
    "i do not remember",
    "i don't have access",
    "i do not have access",
    "i don't have context",
    "i do not have context",
    "i don't have memory",
    "i do not have memory",
    "i don't have that information",
    "i do not have that information",
    "i don't have any previous",
    "i do not have any previous",
    "i don't have prior",
    "i do not have prior",
    "i cannot recall",
    "i can't recall",
    "i cannot see",
    "i can't see",
    "i cannot remember",
    "i can't remember",
    "i have no record",
    "i have no memory",
    "i have no context",
    "i have no access",
    "i have no idea",
    "no previous conversation",
    "this is the first",
    "each session is",
    "i don't know what",
    "i do not know what",
    "i have no persistent memory",
    "i don't have persistent memory",
    "i do not have persistent memory",
    "i can't connect",
    "i cannot connect",
]


def _is_deflection(text: str) -> bool:
    """Return True if `text` is a deflection — a non-answer that signals
    the LLM couldn't or wouldn't engage with the question.

    Deflections are the failure mode behind the "deflection collapse" in
    the 500-turn stress test: the LLM says "I don't know what 'that'
    refers to" or "I don't have access to past conversations" instead of
    using the context it was given. Once deflections start, they cascade:
    they get stored in Qdrant, retrieved as "context" for future prompts,
    and bias the LLM toward more deflections.

    This function is used in two places:
    1. _enrich_query: skip deflections when choosing an anchor (breaks
       the cascade)
    2. _elastic_context: filter deflections out of retrieved hits (don't
       feed past deflections back as "relevant context")

    Defends: docs/elastic_context_window.md deflection collapse at turns
    443-478 (observed as early as turn 100 in practice).
    """
    lower = text.lower()
    return any(phrase in lower for phrase in _DEFLECTION_PHRASES)


def _is_vague_reference(user_text: str) -> bool:
    """Return True if `user_text` is a context_reference prompt whose
    meaning depends on prior context ("Tell me more about that.",
    "Connect that to the memory system.", "What about the other one?").

    Heuristic: short, contains a demonstrative pronoun, and yields fewer
    than 2 content words after stripping function words. "Remember that
    architecture we discussed earlier?" has the content word
    "architecture" (2 content words with "remember") so it is NOT flagged
    — it carries its own semantics and retrieves fine unaided. "Tell me
    more about that." has zero content words and IS flagged.
    """
    text = user_text.strip().lower()
    if not text or len(text) > 100:
        return False
    tokens = re.findall(r"[a-z]+", text)
    if not tokens:
        return False
    # Must contain a deictic to be an anaphoric reference at all.
    if not (set(tokens) & _VAGUE_DEICTICS):
        return False
    content = [w for w in tokens if w not in _VAGUE_STOPWORDS and len(w) > 2]
    return len(content) < 2


def _enrich_query(user_text: str, messages: List[OAIMessage]) -> str:
    """Anchor a vague query with the previous SUBSTANTIVE assistant reply
    so its embedding has something specific to match on.

    "Tell me more about that." -> "Tell me more about that. [Context: <prev
    substantive assistant reply, capped at 500 chars>]"

    CRITICAL: scans backwards past deflections. If the previous assistant
    reply is "I don't have access to past conversations", anchoring on it
    would retrieve MORE deflections from Qdrant — a positive feedback loop
    that makes the deflection collapse worse. We skip deflections and find
    the last reply that actually contains content. If ALL prior assistant
    replies are deflections (early in a collapse), the query is returned
    unchanged — better to retrieve nothing than to retrieve deflections.
    """
    # The current user prompt is the last message; scan backwards for the
    # most recent non-empty, non-deflection assistant reply.
    for m in reversed(messages[:-1]):
        if m.role != "assistant" or not m.content.strip():
            continue
        if _is_deflection(m.content):
            continue  # skip deflections — anchoring on them cascades
        anchor = " ".join(m.content.split())[:500]  # collapse whitespace
        return f"{user_text} [Context: {anchor}]"
    # All prior assistant replies were deflections (or there were none).
    # Return the raw query — better to retrieve nothing than deflections.
    return user_text


async def _elastic_context(
    messages: List[OAIMessage],
    user_text: str,
    conv_id: str,
    recent_turns: int = 6,
    max_chars: int = 6000,
) -> List[OAIMessage]:
    """Build an elastic context window for a single conversation.

    Replaces the fixed 6-turn window (_trim_conversation) with semantic
    retrieval of past turns from Qdrant, ranked by relevance to the
    current prompt. This is the mechanism that makes the "10K guarantee"
    real: a context_reference prompt at turn 440 saying "remember that
    architecture we discussed?" pulls in turn 105 where the architecture
    was actually discussed, instead of just showing the last 6 turns.

    See docs/elastic_context_window.md for the full design, rationale,
    and competitive moat analysis. Traces to Manifesto Pillar III
    (Persistent Context & Memory), PoC 13.1 (10K-Turn Stress Test).

    The assembled context is:
      1. Retrieved older turns from this conversation (chronological
         order, deduplicated against the recent window)
      2. The last `recent_turns` messages from `messages` (immediate
         continuity — "what did you just say?")
      3. The current user prompt (already the last message in
         `messages`)

    `recent_turns` defaults to 6 (3 user+assistant pairs), matching the
    design doc's "last 2-3 turns" and _trim_conversation's own default.
    It MUST stay >= _trim_conversation's default so the graceful-
    degradation fallback never gives the LLM LESS context than the dumb
    window would — the fallback is a safety net, not a shrink ray.

    Vague context_reference prompts ("Tell me more about that.") are
    enriched with the previous assistant reply before retrieval, so the
    embedding has a semantic anchor. See _is_vague_reference /
    _enrich_query and docs/elastic_context_window.md "Remaining Issues"
    #3. The enriched text is used only for the Qdrant query; the LLM
    still sees the raw user prompt.

    Total budget: ~6000 chars (tighter than _trim_conversation's 8000
    because we're selecting, not just truncating).

    Graceful degradation: on ANY retrieval failure (embedding server
    down, Qdrant unreachable, no points found), this falls back to
    _trim_conversation. The system never breaks — it just falls back to
    the dumb window. Trivial prompts (greetings, "yes", "ok") skip the
    embedding + Qdrant query entirely and use recent turns only, saving
    ~50ms latency on the easy cases.
    """
    # Trivial prompt shortcut: greetings/acknowledgements skip retrieval.
    # Same heuristic as the /v1/chat/completions fast path, kept local so
    # this function is self-contained and testable.
    is_trivial = len(user_text) < 15 and not any(c in user_text for c in "?@/\\")

    # Recent window: last `recent_turns` messages (immediate continuity).
    # NOTE: we do NOT modify the recent window even when it contains
    # deflections. Earlier attempts to (a) add a directive system note
    # ("DO have access, Do NOT say..."), (b) replace deflections with a
    # neutral note ("[context was not available]"), and (c) extend the
    # window past deflections all made things worse — the LLM either
    # generated 1-5 token responses (confused by the directive note) or
    # echoed the neutral note back as its response (pattern-matched it).
    # The deflection cascade in the recent window is a model-level problem
    # that context engineering alone cannot fully solve. Instead, we rely
    # on clean retrieval (fixes 1-3) to place substantive content BEFORE
    # the recent window, giving the LLM real content to draw on even when
    # the recent window is degraded. The retrieved context is the lever.
    recent = messages[-recent_turns:] if len(messages) > recent_turns else list(messages)

    if is_trivial:
        # No retrieval needed — just recent turns + current prompt,
        # capped to the char budget.
        return _trim_conversation(messages, max_messages=recent_turns, max_chars=max_chars)

    # Vague context_reference prompts ("Tell me more about that.") produce
    # generic embeddings that match nothing — the semantic anchor is in the
    # previous turn. Enrich the query with the previous SUBSTANTIVE assistant
    # reply so retrieval has something to bite on. The LLM still sees the raw
    # prompt; only the Qdrant query uses the enriched text. See
    # _is_vague_reference / _enrich_query. The enrichment skips deflection
    # replies to avoid a cascading feedback loop (anchoring on "I don't have
    # access" retrieves more deflections, not substantive content).
    query_text = user_text
    if _is_vague_reference(user_text):
        query_text = _enrich_query(user_text, messages)

    # Semantic retrieval of older turns from this conversation.
    # limit=20 (not 10) because the deflection filter and self-match filter
    # will discard many hits — we need a larger pool to have enough
    # substantive content left after filtering. In a long conversation where
    # deflections have cascaded, most of the top-10 hits may be deflections
    # or self-matches; with limit=20 we reach past them to real content.
    try:
        hits = retrieve_conversation_turns(query_text, conv_id, limit=20)
    except Exception as e:
        logger.warning(f"Elastic context retrieval failed (conv={conv_id}): {e}")
        return _trim_conversation(messages, max_messages=recent_turns, max_chars=max_chars)

    if not hits:
        # Nothing in Qdrant for this conversation yet (early turns) or
        # nothing scored above threshold. Fall back to recent turns.
        return _trim_conversation(messages, max_messages=recent_turns, max_chars=max_chars)

    # Deduplicate against the recent window by content hash, so we don't
    # re-insert a turn that's already in the last few messages.
    recent_contents = {m.content.strip() for m in recent}

    # Normalize the current prompt for self-match detection. A vague prompt
    # like "Tell me more about that." appears dozens of times in a long
    # conversation — Qdrant returns those past instances as "hits" because
    # the query embedding matches itself. They're useless: the LLM would see
    # its own past prompts, not any answers. We filter out any hit whose
    # normalized content matches the current user_text.
    user_norm = " ".join(user_text.lower().split())

    # Build OAIMessage objects from retrieved payloads. Filter out:
    #   - empty content
    #   - turns already in the recent window (dedup)
    #   - self-matching hits (the query matching its own past instances)
    #   - deflection hits (past "I don't know" replies fed back as "context"
    #     cause a cascading feedback loop — the LLM sees deflections and
    #     deflects more. See _is_deflection.)
    retrieved: List[OAIMessage] = []
    seen_contents = set(recent_contents)
    for hit in hits:
        content = (hit.get("content") or "").strip()
        if not content or content in seen_contents:
            continue
        # Self-match: skip hits that are just the current prompt echoed back
        # from a previous turn. Normalized comparison catches whitespace/
        # punctuation differences.
        hit_norm = " ".join(content.lower().split())
        if hit_norm == user_norm:
            continue
        # Deflection filter: don't feed past deflections back as "relevant
        # context". This breaks the positive feedback loop where deflections
        # get stored, retrieved, and cause more deflections.
        if _is_deflection(content):
            continue
        role = hit.get("role") or "user"
        retrieved.append(OAIMessage(role=role, content=content))
        seen_contents.add(content)

    if not retrieved:
        # All retrieved turns were already in the recent window.
        return _trim_conversation(messages, max_messages=recent_turns, max_chars=max_chars)

    # Order retrieved turns chronologically. Qdrant returns by similarity;
    # chronological order reads more naturally to the LLM. We don't have
    # a stored timestamp on every payload, so we fall back to insertion
    # order (Qdrant preserves it within a query) which is a reasonable
    # proxy. The recent window stays in its original order at the end.
    # (If a 'turn' or 'timestamp' field exists, sort by it; else keep
    #  retrieval order as a stable fallback.)
    def _sort_key(m_payload):
        ts = m_payload.get("timestamp") or m_payload.get("turn")
        # Stringify so mixed types (int turn vs str timestamp) never raise
        # TypeError during comparison. None sorts last (after present keys).
        return (ts is None, str(ts) if ts is not None else "")

    # Re-sort the raw hits (with payloads) so we can map back, then keep
    # only the ones we kept in `retrieved` by content.
    kept_contents = {m.content for m in retrieved}
    ordered_payloads = sorted(hits, key=_sort_key)
    retrieved = [
        OAIMessage(role=(p.get("role") or "user"), content=(p.get("content") or "").strip())
        for p in ordered_payloads
        if (p.get("content") or "").strip() in kept_contents
    ]

    # Assemble: retrieved older turns (chronological) + recent turns +
    # a system note framing the retrieved block, then cap to max_chars.
    # Deflections in the recent window have already been neutralized to
    # "[context was not available for this turn]" — no directive system
    # note is needed. The retrieved context speaks for itself.
    note = OAIMessage(
        role="system",
        content="[Note: the following older turns were retrieved from AIOS memory "
                "because they are relevant to your current message.]"
    )
    assembled = [note] + retrieved + recent

    # Enforce the char budget across the whole assembled context. Trim
    # individual messages, then drop oldest retrieved turns first if we
    # still overflow.
    assembled = [_truncate_msg(m, max_chars) for m in assembled]
    total = sum(len(m.content) for m in assembled)
    # Drop retrieved turns (oldest first, i.e. right after the note)
    # until we're under budget. Keep at least the note + recent window.
    min_kept = 1 + len(recent)
    while total > max_chars and len(assembled) > min_kept:
        # Drop the first retrieved turn (index 1, right after the note).
        # With the anti-deflection note, the structure is:
        #   [note, retrieved..., anti_deflection_note, recent...]
        # so index 1 is still the first retrieved turn.
        dropped = assembled.pop(1)
        total -= len(dropped.content)

    return assembled


def _trim_conversation(messages: List[OAIMessage], max_messages: int = 6, max_chars: int = 8000) -> List[OAIMessage]:
    """Keep only the last N turns of conversation, with a note about older context.
    The memory system (Qdrant + state files) carries the distilled knowledge from
    older turns, so the LLM doesn't need raw history. This keeps context quality
    high regardless of conversation length - turn 10000 feels like turn 1."""
    if len(messages) <= max_messages:
        # Still enforce char limit on individual messages
        return [_truncate_msg(m, max_chars) for m in messages]

    recent = messages[-max_messages:]
    older_count = len(messages) - max_messages

    # Insert a system note about older context being in memory
    note = OAIMessage(
        role="system",
        content=f"[Note: {older_count} earlier messages in this conversation are stored in AIOS memory. "
                "Relevant context from those turns has been retrieved and included above.]"
    )
    return [note] + [_truncate_msg(m, max_chars) for m in recent]


def _truncate_msg(m: OAIMessage, max_chars: int) -> OAIMessage:
    """Truncate a message to max_chars to prevent context overflow."""
    if len(m.content) <= max_chars:
        return m
    return OAIMessage(role=m.role, content=m.content[:max_chars] + "\n[...truncated...]")


def _call_llm(system_prompt: str, user_text: str) -> str:
    payload = _llm_payload(system_prompt, [OAIMessage(role="user", content=user_text)])
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            response = client.post(LLM_API_URL, json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return f"Error: could not reach the inference server. {e}"


async def _call_llm_with_messages(system_prompt: str, messages: List[OAIMessage]) -> str:
    """Call LLM with full conversation history using async httpx."""
    payload = _llm_payload(system_prompt, messages)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            response = await client.post(LLM_API_URL, json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return f"Error: could not reach the inference server. {e}"


async def _maybe_call_tool(
    llm_response: str,
    system_prompt: str,
    messages: List[OAIMessage],
    conv_id: str | None = None,
) -> tuple[str, bool]:
    """Check if the LLM response contains a tool call. If so, execute it
    via the tool daemon and feed the result back to the LLM for a natural
    language response.

    This is the core of PoC 16.1 (Tool-Augmented Voice Chat). The flow:
      1. Parse the LLM response for a {"tool_call": {...}} JSON object
      2. If found, send it to the tool daemon via the Unix socket
      3. Add the tool call + result to the conversation history
      4. Call the LLM again with the tool result, asking for a natural
         language response
      5. Return the final response + True (tool was called)

    If no tool call is found, return the original response + False.

    Args:
        llm_response: The LLM's initial response text.
        system_prompt: The system prompt (for the second LLM call).
        messages: The conversation history (for the second LLM call).
        conv_id: Optional conversation ID for audit logging.

    Returns:
        (final_response_text, tool_was_called: bool)
    """
    parsed = parse_tool_call(llm_response)
    if not parsed.found or not parsed.name:
        return llm_response, False

    logger.info(f"Tool call detected: {parsed.name}({parsed.args})")

    # Execute the tool call via the tool daemon
    client = get_tool_client()
    turn_id = str(uuid.uuid4())[:8]
    result = client.call_tool(
        tool_name=parsed.name,
        args=parsed.args or {},
        conversation_id=conv_id,
        turn_id=turn_id,
        llm_reasoning=llm_response[:500],  # First 500 chars for audit
    )

    # Check for errors
    if "error" in result:
        error_msg = result["error"]
        logger.warning(f"Tool call {parsed.name} failed: {error_msg}")
        # Feed the error back to the LLM so it can explain to the user
        tool_result_text = f"Tool error: {error_msg}"
    else:
        tool_result = result.get("result", {})
        logger.info(f"Tool call {parsed.name} result: {str(tool_result)[:200]}")
        tool_result_text = json.dumps(tool_result)

    # Build the second LLM call with the tool result
    # The LLM sees: its own tool call, then the tool result, and is asked
    # to respond to the user in natural language.
    tool_messages = list(messages)  # Copy the original history
    tool_messages.append(OAIMessage(role="assistant", content=llm_response))
    tool_messages.append(OAIMessage(
        role="user",
        content=(
            f"Tool result for {parsed.name}({parsed.args}):\n"
            f"{tool_result_text}\n\n"
            "Based on this tool result, respond to the user in natural language. "
            "Do not mention the tool call or JSON. Just answer their question "
            "using the real data from the tool."
        ),
    ))

    final_response = await _call_llm_with_messages(system_prompt, tool_messages)
    return final_response, True


def _persist_turn(req: ChatRequest, conv_id: str, response_text: str, work_order):
    """Store both the user turn and assistant turn to working memory."""
    try:
        wo_id = work_order.get("id") if work_order else None
        store_memory(
            content=req.text,
            role="user",
            conversation_id=conv_id,
            surface=req.surface,
            tier="working",
            work_order_id=wo_id,
        )
        store_memory(
            content=response_text,
            role="assistant",
            conversation_id=conv_id,
            surface=req.surface,
            tier="working",
            work_order_id=wo_id,
        )
    except Exception as e:
        logger.warning(f"Memory persistence failed: {e}")


if __name__ == "__main__":
    uvicorn.run(
        "services.aios_core.main:app",
        host=SERVICE_HOST,
        port=SERVICE_PORT,
        reload=False,
    )
