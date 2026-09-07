"""Integration test: SearXNG → Open-WebUI web-search pipeline (Sept 2026).

Covers the full "local, free, model-decided web search" stack:

    user message
      → Open-WebUI (native function calling)
        → builtin ``search_web`` tool (model decides when to call it)
          → SearXNG metasearch on 127.0.0.1:8081 (no API keys)
        → ``fetch_url`` companion tool for reading a result page
      → cited answer

Layers, cheapest first:

  1. Static config tests — docker-compose.yml carries the tool-call
     iteration cap, and fix_tooling.py enables the web_search builtin
     for the intended models. These catch "config reverted on the next
     apply-model-config run" regressions without needing any services.
  2. Live service tests — SearXNG JSON endpoint, OWUI health/config,
     and OWUI's retrieval endpoint exercised over its real HTTP API
     using a JWT minted inside the container (WEBUI_AUTH=false still
     requires a bearer token for /api/*).
  3. Model-metadata test — the `model` table row for the pilot model
     carries builtinTools.web_search=true and 'web_search' in
     default_feature_ids (the 4-condition gate in
     open_webui/utils/tools.py: builtin flag AND web.search.enable AND
     capability AND request feature).
  4. Backend-dependent tests — skipped unless llama-server is up on
     :8080. The e2e chat test verifies a real completion round-trips.

Known gate: builtin tools (search_web, fetch_url, etc.) are only injected
for UI-originated requests — i.e. requests carrying a top-level
`session_id` field (middleware.py use_builtin_tools). Raw API calls
without `session_id` get only tool_ids-based tools; the model then
reasonably reaches for run_safe_command/curl for web access. To exercise
the UI path over the API, pass `"session_id": "<any>"` and
`"features": {"web_search": true}` in the request body.

Defends: the Sept 2026 local web-search enablement (SearXNG replaces
paid search APIs; model-callable search replaces always-on injection).

Traces to: docs/operations.md (service map), .devin/SLICE_LOG.md
(SearXNG migration), services/searchrouter/docker-compose.yml.
"""
import importlib.util
import json
import os
import subprocess
import urllib.request
import urllib.error

import pytest

# --- Constants -----------------------------------------------------------

AIOS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OWUI_BASE = "http://127.0.0.1:3000"
SEARXNG_URL = "http://127.0.0.1:8081/search"
LLAMA_BASE = "http://127.0.0.1:8080"
OWUI_CONTAINER = os.environ.get("OPENWEBUI_CONTAINER", "open_webui_aios")
PILOT_MODEL = "qwen-dsh-low"
COMPOSE_FILE = os.path.join(AIOS_DIR, "services", "open-webui", "docker-compose.yml")
FIX_TOOLING = os.path.join(AIOS_DIR, "services", "open-webui", "fix_tooling.py")

HTTP_TIMEOUT = 30


# --- Helpers -------------------------------------------------------------

def _http_json(method: str, url: str, body=None, token: str = None, timeout: int = HTTP_TIMEOUT):
    """Minimal JSON-over-HTTP helper (stdlib only, no requests dep)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            return e.code, json.loads(payload)
        except json.JSONDecodeError:
            return e.code, {"raw": payload}


def _mint_token() -> str:
    """Mint an OWUI session JWT inside the container (secret lives in the
    data volume, not env)."""
    out = subprocess.run(
        [
            "docker", "exec", OWUI_CONTAINER, "python3", "-c",
            "import sqlite3, os;"
            "os.environ['WEBUI_SECRET_KEY']=open('/app/backend/data/.webui_secret_key').read().strip();"
            "con=sqlite3.connect('/app/backend/data/webui.db');"
            "uid=con.execute(\"SELECT id FROM user WHERE role='admin' LIMIT 1\").fetchone()[0];"
            "from open_webui.utils.auth import create_token;"
            "print(create_token({'id':uid}))",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, f"token mint failed: {out.stderr[-500:]}"
    return out.stdout.strip().splitlines()[-1].strip()


def _model_meta(model_id: str) -> dict:
    """Read params+meta for a model row straight from webui.db."""
    out = subprocess.run(
        [
            "docker", "exec", OWUI_CONTAINER, "python3", "-c",
            "import sqlite3, json, sys;"
            "con=sqlite3.connect('/app/backend/data/webui.db');"
            f"row=con.execute(\"SELECT params, meta FROM model WHERE id='{model_id}'\").fetchone();"
            "print(json.dumps({'params': json.loads(row[0] or '{}'), 'meta': json.loads(row[1] or '{}')}) if row else 'MISSING')",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, f"db read failed: {out.stderr[-500:]}"
    payload = out.stdout.strip().splitlines()[-1]
    assert payload != "MISSING", f"model '{model_id}' not found in model table"
    return json.loads(payload)


def _load_fix_tooling():
    """Import fix_tooling.py as a module (its dir name has a hyphen)."""
    spec = importlib.util.spec_from_file_location("fix_tooling", FIX_TOOLING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _llama_up() -> bool:
    try:
        with urllib.request.urlopen(f"{LLAMA_BASE}/v1/models", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def _docker_running(name: str) -> bool:
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name={name}"],
        capture_output=True, text=True, timeout=15,
    )
    return name in out.stdout


# --- Fixtures ------------------------------------------------------------

@pytest.fixture(scope="module")
def token():
    if not _docker_running(OWUI_CONTAINER):
        pytest.skip(f"container {OWUI_CONTAINER} not running")
    return _mint_token()


# --- 1. Static config tests (TDD red targets) ----------------------------

class TestStaticConfig:
    """Config-as-code assertions. These fail until the change is applied."""

    def test_compose_has_tool_call_iteration_cap(self):
        """CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS must be set low — the
        OWUI default is 256, which lets a confused local model spin
        through hundreds of tool calls (observed as 'tool call loops')."""
        text = open(COMPOSE_FILE).read()
        assert "CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS" in text, (
            "compose is missing the tool-call iteration cap"
        )
        for line in text.splitlines():
            if "CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS" in line:
                value = int(line.split("=")[-1].strip().strip('"\''))
                assert 1 <= value <= 8, f"cap should be small (1-8), got {value}"
                return
        pytest.fail("cap mentioned but no value parsed")

    def test_fix_tooling_enables_web_search_for_pilot(self):
        """fix_tooling.py is the canonical model-config writer; if the
        web_search enablement isn't encoded there, the next
        apply-model-config run silently reverts it."""
        mod = _load_fix_tooling()
        assert hasattr(mod, "WEB_SEARCH_MODELS"), (
            "fix_tooling.py lacks a WEB_SEARCH_MODELS set"
        )
        assert PILOT_MODEL in mod.WEB_SEARCH_MODELS
        builtins = mod.builtin_tools_for(PILOT_MODEL)
        features = mod.default_features_for(PILOT_MODEL)
        assert builtins.get("web_search") is True
        assert "web_search" in features

    def test_armed_models_have_search_rules(self):
        """Every armed model's system prompt must carry the search-behavior
        rules, including the explicit boundary that keeps the model from
        burning tool calls on run_safe_command+curl for web requests
        (observed 2026-09-07: model chose curl over search_web)."""
        mod = _load_fix_tooling()
        for mid in mod.WEB_SEARCH_MODELS:
            prompt = mod.system_prompt_for(mid)
            assert "search_web" in prompt, f"{mid}: no search_web rule"
            assert "fetch_url" in prompt, f"{mid}: no fetch_url rule"
            assert "run_safe_command" in prompt, f"{mid}: no boundary rule"
            for needle in ("cite", "never invent", "could not be verified"):
                assert needle in prompt.lower(), f"{mid}: missing '{needle}'"

    def test_all_interactive_models_armed(self):
        """Every interactive model gets model-decided web search; the
        batch-ingestion preset stays unarmed (non-interactive, bulk jobs
        shouldn't wander the web)."""
        mod = _load_fix_tooling()
        armed = mod.WEB_SEARCH_MODELS
        assert "qwen-256k-batch" not in armed, "batch model must stay unarmed"
        for mid in mod.ALL_QWEN_DEVSTRAL:
            if mid == "qwen-256k-batch":
                continue
            assert mid in armed, f"{mid} not in WEB_SEARCH_MODELS"
            assert mod.builtin_tools_for(mid).get("web_search") is True
            assert "web_search" in mod.default_features_for(mid)

    def test_model_display_names_are_short(self):
        """Mobile selector got crowded — display names must be ≤3 words
        and unique."""
        mod = _load_fix_tooling()
        names = list(mod.MODEL_NAMES.values())
        assert len(names) == len(set(names)), "display names must be unique"
        for mid, name in mod.MODEL_NAMES.items():
            words = len(name.split())
            assert words <= 3, f"{mid}: '{name}' is {words} words"


# --- 2. Live service tests ------------------------------------------------

class TestLiveServices:
    def test_searxng_json_search(self):
        status, body = _http_json(
            "GET", f"{SEARXNG_URL}?q=ubuntu+releases&format=json"
        )
        assert status == 200
        assert body.get("results"), "SearXNG returned no results"

    def test_owui_health(self):
        status, _ = _http_json("GET", f"{OWUI_BASE}/health")
        assert status == 200

    def test_owui_config_reports_expected_version(self):
        status, body = _http_json("GET", f"{OWUI_BASE}/api/config")
        assert status == 200
        assert body.get("status") is True
        assert body.get("version"), "no version reported"

    def test_owui_retrieval_web_search_e2e(self, token):
        """OWUI → SearXNG through the real retrieval API."""
        status, body = _http_json(
            "POST",
            f"{OWUI_BASE}/api/v1/retrieval/process/web/search",
            body={"queries": ["llama.cpp latest release github"]},
            token=token,
            timeout=60,
        )
        assert status == 200, body
        assert body.get("status") is True
        assert body.get("filenames"), "web search returned zero sources"
        assert any(
            "github" in f or "llama" in f.lower() for f in body["filenames"]
        ), f"unexpected sources: {body['filenames'][:5]}"


# --- 3. Model metadata (the 4-condition tool gate) ------------------------

class TestModelMetadata:
    def test_armed_models_have_search_web_tool_enabled(self):
        if not _docker_running(OWUI_CONTAINER):
            pytest.skip(f"container {OWUI_CONTAINER} not running")
        mod = _load_fix_tooling()
        for mid in sorted(mod.WEB_SEARCH_MODELS):
            data = _model_meta(mid)
            meta = data["meta"]
            bt = meta.get("builtinTools", {})
            feats = meta.get("default_feature_ids", [])
            caps = meta.get("capabilities", {})
            assert caps.get("web_search") is True, f"{mid}: capability missing"
            assert bt.get("web_search") is True, f"{mid}: builtinTools.web_search off"
            assert "web_search" in feats, f"{mid}: not in default_feature_ids"

    def test_display_names_in_db_are_short(self):
        """Live model.name values match the short names in fix_tooling."""
        if not _docker_running(OWUI_CONTAINER):
            pytest.skip(f"container {OWUI_CONTAINER} not running")
        mod = _load_fix_tooling()
        out = subprocess.run(
            [
                "docker", "exec", OWUI_CONTAINER, "python3", "-c",
                "import sqlite3, json;"
                "con=sqlite3.connect('/app/backend/data/webui.db');"
                "print(json.dumps(dict(con.execute('SELECT id, name FROM model').fetchall())))",
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert out.returncode == 0, out.stderr[-400:]
        names = json.loads(out.stdout.strip().splitlines()[-1])
        for mid, expected in mod.MODEL_NAMES.items():
            assert names.get(mid) == expected, (
                f"{mid}: db has '{names.get(mid)}', want '{expected}'"
            )


# --- 4. Backend-dependent tests -------------------------------------------

requires_llama = pytest.mark.skipif(
    not _llama_up(), reason="llama-server not running on :8080"
)


class TestLlamaBackend:
    @requires_llama
    def test_router_exposes_aliases(self):
        status, body = _http_json("GET", f"{LLAMA_BASE}/v1/models")
        assert status == 200
        ids = {m.get("id") for m in body.get("data", [])}
        assert "qwen-fast" in ids, f"router aliases missing: {sorted(ids)[:10]}"

    @requires_llama
    def test_owui_sees_models(self, token):
        """If this fails while the router is up, OWUI's OpenAI connection
        is broken (this was the state on 2026-09-07 with llama-server
        down: /api/models == [] and every chat = 'Model not found')."""
        status, body = _http_json(
            "GET", f"{OWUI_BASE}/api/models", token=token
        )
        assert status == 200
        ids = {m.get("id") for m in body.get("data", [])}
        assert ids, "OWUI model list is empty"
        assert PILOT_MODEL in ids, f"{PILOT_MODEL} not visible: {sorted(ids)}"

    @requires_llama
    def test_e2e_chat_completes(self, token):
        """Real completion through OWUI → llama-server. Proves the whole
        path; does not assert the model *chose* to search (nondeterministic
        by design — search-when-needed is model judgment)."""
        status, body = _http_json(
            "POST",
            f"{OWUI_BASE}/api/chat/completions",
            body={
                "model": PILOT_MODEL,
                "messages": [
                    {"role": "user", "content": "Reply with exactly: PONG"}
                ],
                "stream": False,
            },
            token=token,
            timeout=300,
        )
        assert status == 200, body
        choices = body.get("choices") or []
        assert choices, f"no choices in response: {json.dumps(body)[:400]}"
        content = (choices[0].get("message") or {}).get("content") or ""
        assert content.strip(), "empty completion"
