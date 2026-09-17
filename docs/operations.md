# HORI Operational Notes

This file is the canonical source of truth for hardware, runtime
details, build/test commands, and inference configuration. Paths shown
are examples — adjust to your own build locations.

> **Live stack as of 2026-09-17:** Qwen3.8-27B (Unsloth Dynamic v3.0)
> on `mainline-adaptive-mtp-10`, ROCm 10.0
> (`~/rocm-10.0/rocm/core-10.0`). Qwen3.6 / turbo4 / ROCm 7.14-era notes
> now live in **Appendix A — Historical** at the end of this file.

## Reference Hardware

HORI is designed to run on a local GPU server with remote access via
Tailscale. The reference setup:

- **GPU server:** Headless Linux box with an AMD Radeon AI PRO R9700
  (32GB VRAM), Ryzen 7800X3D, ROCm 10.0. This is where llama.cpp and
  aios-core run.
- **Laptop:** Any macOS/Linux/Windows laptop as a thin client (chat
  surface, IDE, admin panel). No GPU needed.
- **Phone:** Any smartphone for voice chat via the mobile web app.
- **Optional edge device:** A Raspberry Pi or similar SBC for
  wake-word detection and always-on voice.

All devices are connected via Tailscale mesh VPN. aios-core listens on
port 5680, accessible via Tailscale Serve (Tailnet-only HTTPS, no public
exposure).

## Build & Test Commands

```bash
# Run all tests
make test

# Run unit tests only
make test-unit

# Run integration tests
make test-integration

# Run regression tests
make test-regression

# Run specific test file
PYTHONPATH=. ./venv/bin/python3 -m pytest services/aios_core/test_main.py -v
```

## llama.cpp Inference Service

- **Systemd units (system scope is canonical):**
  - Main router: `/etc/systemd/system/llamacpp-adaptive.service`
  - Nano model: `/etc/systemd/system/llama-nano-adaptive.service`
  - A user-scope copy of `llamacpp-adaptive` raced the system unit for
    :8080 and crash-looped it on 2026-09-17 — retired to
    `docs/retired-services/`. Do not recreate a user-scope unit for this
    server; whichever scope binds :8080 first wins, the loser crash-loops.
- **Unit state:** `enabled`, `active (running)`, `Restart=always`, `RestartSec=3`
- **Current model family:** Qwen3.8-27B (Unsloth Dynamic v3.0 quants) with
  vision, plus Devstral-Small-2-24B and `nano` (Llama 3.2 3B on :8090).
  The live alias table below is generated — refresh with
  `scripts/gen_router_doc.py`, verify with `--check`.

<!-- BEGIN GENERATED: router-aliases -->
_Generated 2026-09-17 from `~/llama-builds/mainline-adaptive-mtp-10/router.ini` by `scripts/gen_router_doc.py` — do not hand-edit._

| Alias | Weights | ctx | KV | Reasoning | Speculative decode |
|---|---|---|---|---|---|
| `qwen-fast` | Qwen3.8-27B-UD-Q4_K_M.gguf | 131072 | q4_0/q4_0 | off | draft-mtp-adaptive n≤16 |
| `qwen-quality` | Qwen3.8-27B-Q8_0.gguf | 16384 | q4_0/q4_0 | off | none |
| `qwen-balanced` | Qwen3.8-27B-UD-Q6_K.gguf | 131072 | q4_0/q4_0 | off | none |
| `qwen-think` | Qwen3.8-27B-UD-Q4_K_M.gguf | 131072 | q4_0/q4_0 | on | none |
| `qwen-128k` | Qwen3.8-27B-UD-Q4_K_M.gguf | 196608 | q4_0/q4_0 | off | draft-mtp-adaptive n≤16 |
| `qwen-dsh-fast` | Qwen3.8-27B-UD-Q4_K_M.gguf | 131072 | q4_0/q4_0 | off | draft-mtp-adaptive n≤16 |
| `qwen-dsh-low` | Qwen3.8-27B-UD-Q4_K_M.gguf | 131072 | q4_0/q4_0 | on {"reasoning_effort": "low"} | draft-mtp-adaptive n≤16 |
| `qwen-dsh-medium` | Qwen3.8-27B-UD-Q4_K_M.gguf | 131072 | q4_0/q4_0 | on {"reasoning_effort": "medium"} | draft-mtp-adaptive n≤16 |
| `qwen-deepdive` | Qwen3.8-27B-UD-Q4_K_M.gguf | 131072 | q4_0/q4_0 | on | draft-mtp-adaptive n≤16 |
| `qwen-free` | Huihui-Qwen3.8-27B-abliterated-UD-Q4_K_XL.gguf | 131072 | q4_0/q4_0 | on | draft-mtp-adaptive n≤16 |
| `qwen-256k-batch` | Qwen3.8-27B-UD-Q4_K_M.gguf | 262144 | q4_0/q4_0 | off | none |
| `devstral-coding` | mistralai_Devstral-Small-2-24B-Instruct-2512-IQ4_NL.gguf | 65536 | f16/f16 | off | ngram-mod |
<!-- END GENERATED: router-aliases -->

- **Memory:** ~16-31 GB VRAM depending on alias (Q8_0 ~26.5 GB at 16k ctx,
  UD-Q4_K_M ~16 GB weights + q4_0 KV at the alias's ctx).
- **`qwen-256k-batch` is API-only** (removed from the OWUI selector
  2026-09-17): a full 256k prefill is ~12 min and evicts the resident
  model under `--models-max 1`, so it must not be a clickable preset.
  Still callable via `/v1/chat/completions` with `model: qwen-256k-batch`.
- **Binary:** mainline llama.cpp `adaptive-mtp` branch
  - Path: `~/llama-builds/mainline-adaptive-mtp-10/bin/llama-server`
  - Source: `~/llama.cpp-adaptive-mtp`, commit `d236d41a`
  - Built with ROCm/HIP 10.0 for `gfx1201` (Radeon AI PRO R9700)
  - Build manifest: `~/llama-builds/mainline-adaptive-mtp-10/BUILD-MANIFEST.txt`
- **Model store:** `/opt/ai-models/`
  - `Qwen3.8-27B-UD-Q4_K_M.gguf`
  - `Qwen3.8-27B-UD-Q6_K.gguf`
  - `Qwen3.8-27B-Q8_0.gguf`
  - `Qwen3.8-27B-mmproj-F16.gguf` (vision)
  - `mistralai_Devstral-Small-2-24B-Instruct-2512-IQ4_NL.gguf`
  - `Huihui-Qwen3.8-27B-abliterated-UD-Q4_K_XL.gguf`
  - `Llama-3.2-3B-Instruct-Q4_K_M.gguf` (nano)
- **Runtime:** `~/llama-builds/mainline-adaptive-mtp-10/run-llama.sh`
  - `--models-preset ~/llama-builds/mainline-adaptive-mtp-10/router.ini`
  - `--models-max 1 --host 127.0.0.1 --port 8080`
- **Environment:** `LD_LIBRARY_PATH=<rocm-10-prefix>/lib:<rocm-10-prefix>/lib/rocm_sysdeps/lib:<rocm-10-prefix>/lib/llvm/lib:<build-dir>/lib`
  + `GPU_MAX_HW_QUEUES=1` (prevents ROCm idle-100%-GPU bug)

### ROCm 10.0

- Install path: `~/rocm-10.0/rocm/core-10.0`
- Package version: `10.0.0` (from `.info/version`)
- HIP version: `7.15.26333` (from `bin/hipcc --version`)
- `LD_LIBRARY_PATH` must include all three lib directories when running llama-server
- The old `/opt/rocm/` symlink tree still points to `core-7.14` for compatibility,
  but the live inference stack uses the private `core-10.0` prefix above.

_Qwen3.6-era inference notes (turbo4 KV, VBR mode, DFlash, manual-start
commands) moved to **Appendix A — Historical** at the end of this file._

### Rebuilding the Binary

If the binary needs to be rebuilt (e.g. after a system update). The live
build is made by `~/llama-builds/mainline-adaptive-mtp-10/`'s build script —
see `BUILD-MANIFEST.txt` there for the exact flags used last time. (The
Spiritbuun-fork build recipe is in Appendix A.)

**Mainline llama.cpp:**
```bash
cd <your-llama-cpp-source>
rm -rf build
ROCM_PATH=<your-rocm-path> \
cmake -B build -DGGML_HIP=ON -DAMDGPU_TARGETS=gfx1201 \
  -DCMAKE_BUILD_TYPE=Release -DGGML_HIP_NO_VMM=ON \
  -DROCM_PATH=<your-rocm-path> \
  -DCMAKE_HIP_FLAGS="-include __clang_hip_runtime_wrapper.h" \
  -DCMAKE_EXE_LINKER_FLAGS="-L<your-rocm-path>/lib/rocm_sysdeps/lib -Wl,-rpath,<your-rocm-path>/lib/rocm_sysdeps/lib" \
  -DCMAKE_SHARED_LINKER_FLAGS="-L<your-rocm-path>/lib/rocm_sysdeps/lib -Wl,-rpath,<your-rocm-path>/lib/rocm_sysdeps/lib"
ROCM_PATH=<your-rocm-path> cmake --build build --target llama-server -j$(nproc)
```

Note: The Spiritbuun fork's `ggml/src/ggml-hip/CMakeLists.txt` has a patch to add
`-include __clang_hip_runtime_wrapper.h` to `CMAKE_HIP_FLAGS` (needed for clang 23 / ROCm 7.14).
The mainline build requires this flag via `-DCMAKE_HIP_FLAGS`.

## Elastic Context Window

Instead of a fixed 6-turn history window, aios-core uses semantic
retrieval of past turns from Qdrant, ranked by relevance to the current
prompt. This is the mechanism that makes the "10K guarantee" real: your
conversation stays smart at turn 10,000.

**How it works:**
1. Always include the last 2-3 turns (immediate continuity)
2. Semantically retrieve top-K past turns from the same conversation,
   ranked by similarity to the current prompt, up to a token budget
3. Inject distilled memory context from project/longterm tiers

**Test results (500-turn stress test):** Deflection collapse (LLM saying
"I don't know what 'that' is" repeatedly) was significantly mitigated by
PoC 13.7. Remaining issues are model-level (1-5 token degeneration at
certain context configurations), not architecture-level.

**Traces to:** Manifesto Pillar III (Persistent Context & Memory),
PoC 13.1 (10,000-Turn Stress Test), PoC 13.7 (Deflection Mitigation).

## Tailscale / Endpoints

- **Tailscale IP:** `<your-tailscale-ip>`
- **llama-server (OpenAI-compatible):** `http://<your-tailscale-ip>:8080/v1`
  - `GET /v1/models` for model listing
  - `POST /v1/chat/completions` for generation
- **aios-core (OpenAI-compatible + MCP):** `http://<your-tailscale-ip>:5680/v1`
  - Chat and models are also at `https://<your-tailnet>.ts.net/v1/chat/completions`
  - MCP-over-SSE is at `https://<your-tailnet>.ts.net/mcp/sse`
- **Nano model (OpenAI-compatible):** `https://<your-tailnet>.ts.net:8444/v1`
  - `GET /v1/models` returns `nano` (Llama 3.2 3B)
  - `POST /v1/chat/completions` for fast, low-VRAM casual chat
- **Open WebUI:** `http://<your-tailscale-ip>:3000` (primary container `open_webui_aios`, host port `3000`)

## Ports

- `8080/tcp` -> `llama-server` (must remain free)
- `8090/tcp` -> `llama-nano` (Llama 3.2 3B dedicated server)
- `8444/tcp` -> Tailscale Serve for `nano` (`http://localhost:8090`)
- `5680/tcp` -> `aios_core`
- `8766/tcp` -> local-tools server (Open-WebUI and MCP `read_file`/`run_safe_command`)
- `8769/tcp` -> project-tools server
- `8771/tcp` -> workspace-tools server (confirmation-gated directories + `.md` writes)
- `3000/tcp` -> `open_webui_aios` Docker container
- `6333/tcp`, `6334/tcp` -> `qdrant_aios`
- `5678/tcp` -> `n8n_aios`
- `9090/tcp` -> `prometheus_aios`
- `9100/tcp` -> `node_exporter_aios`
- `3001/tcp` -> `grafana_aios` (mapped to container port 3000)

## Cline (VS Code) Configuration

Cline can use the local `llama-server` as an OpenAI-compatible provider.

### Prerequisites

- Cline extension installed in VS Code.
- The machine running VS Code can reach the inference endpoint:
  - If VS Code is on this host: `http://localhost:8080/v1`
  - From another machine: `http://<your-tailscale-ip>:8080/v1` (must be on the same Tailscale mesh).
- `llama-server` is running and responds to `GET /v1/models`.

### Cline Settings

1. Open the Cline panel in VS Code.
2. Click the settings/gear icon (or open the API Provider dropdown below the chat input).
3. Set **API Provider** to **OpenAI Compatible**.
4. Fill in the connection fields:
   - **Base URL**: `http://<your-tailscale-ip>:8080/v1`
     - Use `http://localhost:8080/v1` if VS Code is running directly on this host.
   - **API Key**: any non-empty value, e.g. `not-needed`
     - Cline requires a key to be present, but the local server does not enforce one.
   - **Model ID**: paste one of the exact IDs returned by `/v1/models` — the
     generated alias table above is the live list. Good defaults:
     - `qwen-fast` (default 128k chat)
     - `qwen-balanced` (128k coding/research, Q6_K)
     - `devstral-coding` (Devstral 24B coding, 64k)
     - `qwen-quality` (16k high-stakes reasoning, Q8_0)
     - Agentic/research: `qwen-dsh-fast`, `qwen-dsh-low`, `qwen-dsh-medium`,
       `qwen-deepdive`, `qwen-free`, `qwen-think`, `qwen-128k`
5. Cline has separate **Plan** and **Act** modes; configure the model for each mode if you use both.
6. Click **Save**, then send a test message.

### Notes

- The first request to a model may be slow while `llama-server` loads it into VRAM.
- Model IDs must match the server exactly (case-sensitive). Re-check the list with:
  ```bash
  curl -s http://<your-tailscale-ip>:8080/v1/models | python3 -m json.tool
  ```
- If Cline reports an authentication error, verify the **API Key** field is not blank.
- This endpoint has CORS `*` and no API-key enforcement; only use it on trusted networks (Tailscale).

## Common Verification

```bash
systemctl status llamacpp-adaptive
systemctl is-active llamacpp-adaptive
ss -tlnp | grep -E "8080|8081|5680"
curl -s http://<your-tailscale-ip>:8080/v1/models | python3 -m json.tool
curl -s http://localhost:5680/health
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"
```

## HORI Core Service (:5680)

The shared intelligence layer. All surfaces (Open WebUI, future voice loop, future proactive agent) call this service.

- **Service:** `services/aios_core/main.py` (FastAPI on port 5680)
- **Start:** `PYTHONPATH=. ./venv/bin/python3 -m services.aios_core.main`
- **Endpoints:**
  - `GET /health` - liveness check
  - `POST /chat` - internal pipeline: parse intent -> retrieve memory -> red-team gate -> LLM -> persist
  - `GET /v1/models` - OpenAI-compatible model list (returns `aios-core`)
  - `POST /v1/chat/completions` - OpenAI-compatible chat (used by Open WebUI)
  - `GET /system/state` - system state snapshot (services, VRAM, disk, ROCm version, model, uptime)
  - `POST /system/incident` - report an incident (service down, VRAM overflow, etc.)
  - `GET /system/incidents` - retrieve recent incidents (query: `?limit=N&unresolved_only=true`)
  - `POST /system/incidents/{id}/resolve` - mark an incident as resolved
- **Pipeline (OpenAI-compatible endpoint):**
  1. Skip intent parsing (background concern, avoids doubling latency)
  2. Memory retrieval from Qdrant (single batched embedding, 3 tiers queried in parallel)
  3. State context injection (`core/state/user_model.json` + `project_state.json`)
  4. System status injection (if user asks about system health, inject current state + incidents)
  5. Red-team gate (if destructive patterns detected, runs `RedTeamingEngine.evaluate_action`)
  6. Context trimming: keep last 6 turns + note about older context being in memory
  7. LLM call with assembled context (streaming via async httpx)
  8. Memory persistence (async, in background, does not block response)
- **Context management:** Conversation history is trimmed to last 6 messages + memory retrieval.
  This makes "turn 10000 = turn 1" - the LLM always gets curated context, not raw history.
  The memory system (Qdrant tiers + state files) carries the distilled knowledge.
- **Trivial message fast path:** Messages < 15 chars with no `?@/\` skip memory retrieval entirely.
- **System status detection:** If user asks "how'd that upgrade go?" or similar, current system
  state and recent incidents are injected into the context so the LLM can answer naturally.
- **Multi-source web search:** If the query needs current info (latest releases, model
  versions, news, dates), aios-core fans out to 6 free sources in parallel: DuckDuckGo
  (general web), arXiv (academic preprints), GitHub (code repos), Reddit (community
  discussions), Hacker News (tech industry), and Semantic Scholar (cited papers).
  Results are merged, deduplicated by URL, scored by keyword relevance + source authority
  weight, and summarized with the LLM. All sources are free with no API key required.
  If a source is slow or down, the others still return (5s per-source timeout).
  See `services/aios_core/multi_search.py` for the implementation.
  Triggers: "latest", "recent", model version numbers (GPT-5, Claude 3.5, etc.),
  "is X worth it", explicit "search the web" requests, no memory hits + question.
- **Current date/time:** Injected into every system prompt so HORI knows what today is.
- **Config:** Environment variables (`LLM_API_URL`, `LLM_MODEL`, `EMBED_URL`, `EMBED_MODEL`, `QDRANT_URL`)
- **Current model:** Qwen3.8-27B router aliases via llama-server (see `GET /v1/models` for the active list)
- **MCP-over-SSE remote surface:**
  - `GET /mcp/sse` — Server-Sent Events endpoint. Requires the admin
    bearer token (`HORI_ADMIN_TOKEN`) in the `Authorization` header.
    Returns an initial `event: endpoint` with the message POST URL
    including a fresh `session_id`, followed by `event: message` for each
    JSON-RPC response.
  - `POST /mcp/messages?session_id=<id>` — JSON-RPC message endpoint.
    Accepts `initialize`, `ping`, `tools/list`, and `tools/call`. Returns
    HTTP 202 immediately and queues the JSON-RPC response on the SSE
    stream. Requires the same admin bearer token.
  - **Exposed MCP tools:** `get_status`, `read_file`, `run_safe_command`,
    `search_web`, `fetch_url`, `memory_search`, `memory_store`,
    `thread_list`, `thread_read`, `episode_append`, `episode_search`.
    `read_file` and `run_safe_command` delegate to the local-tools server
    (:8766) and inherit its allowlists and audit logging. The four
    `thread_*`/`episode_*` tools expose the workstream store
    (`HORI_THREADS_ROOT`, default `~/.local/share/hori/threads/`):
    thread names are slug-validated, episode writes are append-only
    (create-only, capped at 64 KiB / 500 per thread), and all paths are
    verified inside the threads root before any write.
  - **Tailnet-only reachability:** `/mcp` is proxied through Tailscale
    Serve (port 443) and is NOT exposed via Funnel. Verify with
    `scripts/hardening/audit_funnel.sh`.
  - **Configured clients (2026-09-15):** Devin CLI
    (`~/.config/devin/mcp_config.json`, user scope, loopback
    `http://127.0.0.1:5680/mcp/sse`) and Cline
    (`cline_mcp_settings.json`, `type: sse`, same URL; read tools
    auto-approved, `episode_append` requires confirmation). Both use the
    admin bearer token stored in the client config files.
  - **Devin session hooks:** `SessionStart` runs
    `scripts/hori_session_start.py` (injects the thread index plus the
    cwd-matched thread's state + latest episode, ~4 KB cap, fail-open);
    `SessionEnd` runs `scripts/hori_session_end.py` (appends a line to
    `~/.local/share/hori/sessions.jsonl` for consolidation signals).
    Configured in `~/.config/devin/config.json` under `hooks`.

## Proactive Opportunity Agent

A scheduled agent that surveys the landscape and proposes work orders.

- **Landscape Survey:** `services/proactive_agent/landscape_survey.py`
  - Surveys GitHub trending, Hacker News top stories, and AI/ML RSS feeds
  - Scores relevance against user interests (from `user_model.json`) and active projects
  - Output: `core/state/opportunities.json` (top 50 scored opportunities)
  - Run: `PYTHONPATH=. ./venv/bin/python3 -m services.proactive_agent.landscape_survey`
- **Opportunity Proposer:** `services/proactive_agent/opportunity_proposer.py`
  - Takes top opportunities and uses LLM to propose 1-3 concrete work orders
  - Grounded in user_model + project_state
  - Output: `core/state/proposed_work_orders.json`
  - Run: `PYTHONPATH=. ./venv/bin/python3 -m services.proactive_agent.opportunity_proposer`

## Incident Memory

- **Storage:** `core/state/incidents.json` (last 100 incidents) + Qdrant `aios_working` tier
- **Flow:** Watchdog detects service down -> reports to aios-core `/system/incident` -> stored -> user can ask "any issues?" -> aios-core responds with natural language summary
- **Resolve:** `POST /system/incidents/{id}/resolve` marks an incident as resolved

## Codebase Awareness

- **Script:** `scripts/ingest_codebase.py`
- Ingests HORI's own Python source files (by function/class) and markdown docs (by section) into `aios_longterm`
- 114 chunks covering aios_core, red_teaming, recovery, telemetry, scripts, core, docs
- Enables HORI to answer questions about its own architecture and reason about changes
- Run: `PYTHONPATH=. ./venv/bin/python3 scripts/ingest_codebase.py` (requires embedding server on :8082)

## Stress Tests

HORI has two stress tests with fundamentally different purposes:

### Entropy & Context Drift Test

- **Script:** `tests/stress/test_ten_thousand_turns.py`
- **Purpose:** Prove the 10,000th turn is just as smart as the first. Tests
  memory recall, context drift, and entropy collapse over long chained
  conversations (planning/strategy sessions).
- **How:** Turns are CHAINED — each turn sends conversation history from
  prior turns (up to 6). Planning and context_reference prompts build on
  each other, requiring multi-turn context. Meta prompts check if the LLM
  remembers what was discussed earlier.
- **Prompt phases:** opening (planning kickoff), factual, context_reference
  (drift detector), planning (strategy), coding, creative, meta (memory checks)
- **Metrics:** repetition ratio, unique responses, topic drift, early vs late
  recall quality (context drift detection), VRAM tracking
- **Run:** `PYTHONPATH=. ./venv/bin/python3 tests/stress/test_ten_thousand_turns.py --turns 100`
- **Pytest:** `PYTHONPATH=. ./venv/bin/python3 -m pytest tests/stress/test_ten_thousand_turns.py::test_stress_10_turns -v`

### Safety Stress Test

- **Script:** `tests/stress/test_safety_stress.py`
- **Purpose:** Exercise the hallucination interception rate (PoC 15.14/16.2)
  and Sherpa behavioral guardian (PoC 15.50) under load.
- **How:** Turns are STATELESS — each request is independent, isolating
  each prompt's effect on safety metrics. Hits both `/v1/chat/completions`
  (no tools advertised) and `/v1/voice/chat` (tools advertised, Sherpa active).
- **Prompt categories:** hallucination_bait (tempts action claims without
  tools), injection_attempt (path traversal, prompt injection), rapid_tool_calls
  (multi-target requests to trigger Sherpa), normal (control group)
- **Metrics:** gate API deltas (hallucination interceptions, Sherpa triggers,
  tool call stats), per-turn status (OK/INTERCEPTED/TOOL_CALLED/ERROR),
  VRAM tracking
- **Run:** `PYTHONPATH=. ./venv/bin/python3 tests/stress/test_safety_stress.py --turns 100 --endpoint both`
- **Pytest:** `PYTHONPATH=. ./venv/bin/python3 -m pytest tests/stress/test_safety_stress.py::test_safety_stress_smoke -v`

### Make Targets

- `make test-stress` — runs both stress smoke tests (10 turns each, ~120s total)
- `make test-stress-entropy` — entropy/drift smoke test only
- `make test-stress-safety` — safety smoke test only

### Admin Panel

The admin panel (`/admin`) has:
- **Test Suite buttons:** Stress (smoke), Entropy, Safety — run the 10-turn
  smoke tests inline (120s timeout)
- **Stress Runner section:** buttons to start background runs of 100/1K/10K
  turns. These run as detached processes — the panel polls
  `/admin/api/stress/status` for live progress (log tail, checkpoints).
  Use the Stop button to kill a running test.

### Running the Full 10K-Turn Entropy Test

The 10K-turn run takes ~11 hours at 4s/turn (1s delay + ~3s response).
It should be run from the CLI (not the admin panel) as a background job:

```bash
cd ~/Projects/aios
PYTHONPATH=. ./venv/bin/python3 -u tests/stress/test_ten_thousand_turns.py \
  --turns 10000 --delay 1.0 --max-tokens 100 \
  2>&1 | tee /tmp/entropy_10k.log
```

- Checkpoints saved every 100 turns to `tests/stress/results/checkpoint_turn_*.json`
- Ctrl+C safe — partial results are saved
- VRAM auto-pauses at 90% usage
- The admin panel's Stress Runner can also start it, but the browser
  tab must stay open to poll for status

## Embedding Server (:8082)

Dedicated embedding server for RAG/memory, separate from the chat model to avoid VRAM contention.

- **Container:** `aios_embedding` from `services/open-webui/docker-compose.override.yml`
- **Image:** `ghcr.io/open-webui/open-webui:main` with a minimal FastAPI server mounted at `/app/embedding_server.py`
- **Model:** `BAAI/bge-base-en-v1.5` (768-dim vectors)
- **Start:** `cd services/open-webui && docker compose up -d embedding`
- **Endpoint:** `POST http://127.0.0.1:8082/v1/embeddings` (OpenAI-compatible shape)
- **Note:** Runs on CPU to keep VRAM free for chat models

## Qdrant Memory Tiers

Three collections for hierarchical memory (the anti-groundhog mechanism):

- `aios_working` - raw conversation turns (hot, high churn)
- `aios_project` - promoted, distilled project insights (warm)
- `aios_longterm` - foundational docs + cross-project knowledge (cold)
- Also: `riff_library_qdrant` (legacy MIDI RAG from PoC 3.2)

## State Files (Living Context)

- `core/state/user_model.json` - preferences, tone, skills, decision patterns
- `core/state/project_state.json` - active projects, recent decisions, open questions
- These are injected into every prompt by `aios-core` so session N+1 starts where session N ended
- Updated automatically by the consolidation cycle (`scripts/memory_consolidation.py`)

## Memory Consolidation (Sleep & Dream)

- **Script:** `scripts/memory_consolidation.py`
- **Cycle:** cluster working memory by conversation -> LLM-distill durable insights -> promote to `aios_project` -> update state files -> archive raw
- **Run:** `PYTHONPATH=. ./venv/bin/python3 scripts/memory_consolidation.py`
- **Dry run:** `CONSOLIDATION_DRY_RUN=true PYTHONPATH=. ./venv/bin/python3 scripts/memory_consolidation.py`
- Should be scheduled via systemd timer (nightly + on-session-start)

## Foundational Docs Ingestion

- **Script:** `scripts/ingest_general.py`
- Ingests manifesto, charter, governance, architecture, roadmap, state files into `aios_longterm`
- **Run:** `PYTHONPATH=. ./venv/bin/python3 scripts/ingest_general.py`

## Red-Team Engine (Yes, AND)

- **Service:** `services/red_teaming/engine.py`
- Three personas: Strategist, Architect, Guardian
- Verdicts: `[APPROVED]`, `[REJECTED]`, or `[YES_AND]` with an alternative
- `YES_AND` returns `alternatives[]` in the report so rejections come with a path forward
- Config: `LLM_API_URL` and `LLM_MODEL` env vars (defaults to llama-server)

## Ollama Status (Decommissioned)

- **Service state:** `disabled`, `inactive (dead)`
- **Blob files:** Deleted (previously 103GB, now freed)
- **Reason for decommission:** Moved to llama.cpp/ROCm for direct GPU control, I-Quant support, and uncompressed KV cache
- **Cleanup:** All references to Ollama (port 11434) have been removed from:
  - `services/recovery/config.json` (now monitors `llamacpp`)
  - `services/red_teaming/engine.py` (now uses `/v1/chat/completions`)
  - `scripts/qdrant_ingest.py` (now uses `/v1/embeddings` on :8082)
  - `scripts/test_rag.py` (same)

## Retired Services

- **Interaction Surface (:5679):** Mock server removed. Open WebUI (:3000) and aios-core (:5680) are the real front doors.
- **Ollama:** Fully decommissioned. Dead Ollama URL removed from Open WebUI config to eliminate 10s model-list timeout.
- **`llamacpp.service`:** Original inference unit, superseded by
  `llamacpp-adaptive.service` (Sep 2026). Unit file in
  `docs/retired-services/`; stale `llamacpp` references in the broker
  allowlist, recovery config, admin API allowlists, post-reboot health
  check, and consolidation units were all swept to `llamacpp-adaptive`
  on 2026-09-17.
- **User-scope `llamacpp-adaptive.service`:** Duplicate unit
  (`~/.config/systemd/user/`) raced the canonical system unit for :8080
  during the 2026-09-17 rebase restart — system unit crash-looped on
  port-bind while the user copy served. Retired to
  `docs/retired-services/llamacpp-adaptive.user.service`. System scope
  is the only owner of :8080 now.

## Open WebUI Configuration

- **Container:** `open_webui_aios` (Docker, `network_mode: host`)
- **Port:** 3000
- **OpenAI connections:**
  - `http://127.0.0.1:5680/v1` (aios-core, primary)
  - `http://127.0.0.1:8080/v1` (direct llama-server, for raw model access)
  - `http://127.0.0.1:8090/v1` (`nano`, Llama 3.2 3B, fast/low-VRAM chat)
- **Ollama:** Disabled (`ollama.enable: false`, `ollama.base_urls: []`)
- **Background tasks disabled** (each was a separate LLM call competing for the single model slot):
  - `task.title.enable: false` (title generation)
  - `task.follow_up.enable: false` (follow-up suggestions)
  - `task.tags.enable: false` (auto-tagging)
  - `task.query.search.enable: false` (search queries)
  - `task.query.retrieval.enable: false` (retrieval queries)
  - `task.voice.prompt.enable: false` (voice prompt generation)
- **DB config location:** `/app/backend/data/webui.db` (SQLite, inside container)
- **Local tool servers:**
  - `local-tools-adaptive` (:8766): read-only host inspection (`read_file`, `run_safe_command`, `get_status`)
  - `project-tools-adaptive` (:8769): confirmed project-skeleton creation
  - `workspace-tools` (:8771): confirmed directory + Markdown manifest writes under `WORKSPACE_TOOLS_ROOT` (`~/Projects` in the unit). Create-only; `.md` files only; attached only to `qwen-deepdive` by `services/open-webui/fix_tooling.py`.
  - Install/verify workspace tools: `make install-workspace-tools`, then `make check-workspace-tools`.

## Destructive Pattern Gate

- **Config:** `services/aios_core/config.py` -> `DESTRUCTIVE_PATTERNS`
- Patterns are intentionally specific to avoid false positives (e.g. `\bformat\b` was removed because "JSON format" in title-generation prompts triggered the red team)
- Current patterns target actual destructive commands: `rm -rf`, `mkfs`, `sudo rm`, `chmod 777`, `delete (all|everything|the|old|files|...)`, `wipe (the|disk)`, `drop (table|schema|database)`, `format (c:|disk|drive)`, `git push --force`, `git reset --hard`, etc.

## Notes

- `llama-server` warns that CORS is `*` and no API key is set. This is acceptable on the Tailscale mesh but should not be exposed to untrusted networks.
- `systemctl` changes require `sudo` and cannot be performed by unprivileged sessions in this environment.
- The embedding server (:8082) is a Docker container (`aios_embedding`), not a systemd unit — `cd services/open-webui && docker compose up -d embedding`.
- aios-core uses `httpx` (async) for LLM calls, not `requests` (blocking). This is critical - blocking calls caused the server to hang during concurrent requests (model polling + chat).
- aios-core runs as the `aios_core` systemd service: `sudo systemctl restart aios_core`.
- The systemd service for llama-server uses router mode (`--models-preset router.ini --models-max 1`), so it can host multiple aliases while keeping one model in VRAM at a time.

## Benchmark (Aug 2026)

| Metric | Original | q8_0 + ngram | Turbo4 + ngram (Qwen2.5) | Turbo4 (Qwen3.6, Aug 2026) | VBR + ngram (optional) |
|--------|----------|-------------|--------------------------|---------------------------|----------------------|
| Binary | Mainline b10204 | Mainline b10204 | Spiritbuun fork | Spiritbuun fork (ROCm 7.14) | Spiritbuun fork |
| Context window | 65536 | 16384 | 16384 | 16384 | 65536 |
| KV cache type | f16 | q8_0 | turbo4 | turbo4 | vbr (dynamic) |
| KV cache VRAM | 16GB | 2GB | ~1GB | ~1GB | ~1.5GB (grows on use) |
| Total VRAM used | 33GB (97%) | 21.9GB (64%) | 20.7GB (61%) | 16.1GB (47%) | 19.9GB (58%) |
| Free VRAM | 0.2GB | 12.3GB | 13.5GB | 18GB | 14.2GB |
| Speculative decoding | none | ngram-mod | ngram-mod | none (incompatible) | ngram-mod |
| "hi" response time | 95s | 0.4s | 0.9s | 0.7s | 1.2s |
| "What is HORI about?" | 95s+ | 1.5s | 1.3s | 2.6s | 1.5s |
| Direct 100-token gen | N/A | 1.3s | 1.5s | 3.4s | 4.7s |
| Model | Qwen3.5-27B | Qwen2.5-Coder-32B | Qwen2.5-Coder-32B | Qwen3.6-27B | Qwen2.5-Coder-32B |
| ROCm | 7.1 | 7.1 | 7.1 | 7.14 | 7.1 |
| Context management | Full history | Last 6 turns + memory | Last 6 turns + memory | Last 6 turns + memory | Last 6 turns + memory |
| Background LLM tasks | 5+ per chat | 0 | 0 | 0 | 0 |

**Aug 2026 config** (turbo4 + Qwen3.6 on ROCm 7.14) used the least VRAM (16GB, 47%) at the cost of speculative decoding.

**Current config** (Sep 2026): Qwen3.8-27B (Unsloth Dynamic v3.0 UD-Q4_K_M / UD-Q6_K / Q8_0) on `mainline-adaptive-mtp-10` with `draft-mtp-adaptive` speculative decode and ROCm 10.0. Context sizes range from 16k (`qwen-quality`) to 256k (`qwen-256k-batch`). Typical VRAM use is ~16-31 GB depending on alias and context. See `router.ini` and `BUILD-MANIFEST.txt` in `~/llama-builds/mainline-adaptive-mtp-10/` for exact flags.

## Post-Reboot Health Check

After a reboot, `systemctl is-active` returning "active" is necessary but
not sufficient. The Aug 8 2026 incident showed that the tool daemon could
be crash-looping (missing `/tmp/aios-workspace`) and the Sherpa could be
blind (audit log parse errors) while appearing healthy to systemd.

**Run after every reboot:**

```bash
sudo scripts/hardening/post_reboot_health.sh
```

This checks:
1. All 4 critical services are active (aios_core, aios-sherpa, aios-tool-daemon, llamacpp)
2. The tool daemon Unix socket exists and is reachable
3. The Sherpa capability file exists, is fresh (<10s old), and shows Level 0
4. The Sherpa is NOT skipping audit log entries (not blind)
5. The tool daemon can execute a real `count_files` call end-to-end
6. The LLM inference server responds to `/health`
7. `/tmp/aios-workspace` exists (tool daemon workspace)

## Safety Spine Incident (Aug 8 2026)

**Root cause:** Two bugs surfaced after a reboot, both undetected by the
existing test suite:

1. **Tool daemon crash-loop (529 restarts):** `/tmp/aios-workspace` is
   volatile (wiped on reboot). systemd's `ReadWritePaths` requires the
   directory to exist for mount namespacing. The install script created
   it manually but nothing recreated it at boot.
   **Initial fix (broken):** Added `ExecStartPre=/bin/mkdir -p /tmp/aios-workspace`
   to the service file. This was **fundamentally broken**: systemd processes
   `ReadWritePaths` (mount namespace setup) BEFORE running `ExecStartPre`.
   So when the directory doesn't exist, namespace setup fails with status
   226/NAMESPACE and `ExecStartPre=/bin/mkdir` never runs. It's a
   chicken-and-egg problem — the directory must exist before `ReadWritePaths`
   can bind-mount it, but `ExecStartPre` (which creates the directory) runs
   after namespace setup.
   **Correct fix:** Use `systemd-tmpfiles`. A tmpfiles.d config at
   `/etc/tmpfiles.d/aios-workspace.conf` (source: `scripts/hardening/aios-workspace.conf`)
   runs early in boot (via `sysinit.target`) and creates the directory before
   any services start. The install script (`install_tool_daemon.sh`) installs
   this config and applies it immediately. The broken `ExecStartPre` lines
   were removed from the service file.

2. **Sherpa blind to audit log:** The Go `AuditEntry.Timestamp` field was
   typed `string` while the Python `AuditLogger` writes `time.time()` as
   a `float`. The Sherpa silently skipped every audit line as malformed.
   It was alive, writing Level 0 every 3 seconds, but couldn't see any
   tool calls. The fail-closed design only covers "Sherpa dies → Level 4";
   "Sherpa alive but blind" was not a recognized failure mode.
   **Fix:** Changed Go struct field to `float64`. Added a health metric
   that tracks the skip ratio and escalates to Level 2 (reduced) if >50%
   of audit lines are skipped after 5+ lines — a blind guardian restricts
   capabilities rather than pretending everything is fine.

**Why the tests didn't catch it:** The 6 adversarial Sherpa tests
(`test_sherpa_trigger.py`) test the behavioral logic via the Python
capability-file interface. They never feed the Go binary real audit log
entries. The wire contract between `audit.py` (Python, float) and
`main.go` (Go, string) was never tested end-to-end.

**New tests added:**
- `services/integration_tests/test_sherpa_wire_contract.py` — 6 tests
  that write real audit entries via the Python `AuditLogger`, run the
  actual Go Sherpa binary, and verify it parses entries, detects rate
  anomalies, and escalates when blind. Includes a static contract check
  that Python JSON keys match Go struct tags.
- `services/integration_tests/test_reboot_survival.py` — automated test
  for the tool daemon workspace-recreation fix, in three layers:
  - **Repo static tests** (4 tests, no privileges): verify the repo
    tmpfiles.d config creates `/tmp/aios-workspace` with correct
    owner/mode, the service file has `ReadWritePaths`, and the service
    file does NOT have the broken `ExecStartPre` lines (regression guard).
  - **Installed static tests** (5 tests, no privileges): verify the
    deployed tmpfiles.d config and service file match the repo and have
    the fix. This catches the "fix committed but not deployed" case.
  - **Simulated reboot** (2 tests, require root): stop the service,
    wipe `/tmp/aios-workspace` and the stale socket (simulating /tmp
    and /run volatility on reboot), run `daemon-reload` +
    `systemd-tmpfiles --create` (simulating boot), restart, and verify
    the workspace is recreated with correct ownership/permissions, the
    service reaches `active` state, and an end-to-end tool call succeeds.
  The privileged tests skip gracefully when not root so
  `make test-integration` still works for non-privileged users. To run
  the full suite including the simulated reboot:
  ```bash
  sudo env PYTHONPATH=. ./venv/bin/python3 -m pytest \
      services/integration_tests/test_reboot_survival.py -v
  ```
- The Makefile `test-integration` target now runs ALL integration tests
  (was only running `test_workflow_integration.py`).

**Soak clock impact:** The Sherpa was blind for the entire Aug 5-8
period. The 2-week gate restarts from Aug 8 with both fixes applied.

## Appendix A — Historical (Qwen3.6 / turbo4 / ROCm 7.14 era)

Everything below describes the **Aug 2026 stack**: Qwen3.6-27B IQ4_NL on
the Spiritbuun fork with TurboQuant `turbo4` KV cache. None of it reflects
the live config — the current stack is Qwen3.8-27B UD quants on
`mainline-adaptive-mtp-10` with q4_0/f16 KV and `draft-mtp-adaptive`
speculative decode (see the generated alias table under "llama.cpp
Inference Service"). Kept for archaeology: the failure notes, quant
comparisons, and Vulkan review are still useful history.

### Inference Optimizations (Aug 2026)

- **Context window: 16384** (down from 65536)
  - KV cache at f16/65K = 16GB VRAM (97% of 32GB, zero headroom)
  - KV cache at turbo4/16K = ~1GB VRAM (3% of 32GB, 13GB free)
  - aios-core trims conversation to last 6 turns + memory retrieval, so 16K is plenty
  - The "10000th turn = turn 1" promise is carried by the memory system, not the context window
- **KV cache quantization: TurboQuant turbo4** (`-ctk turbo4 -ctv turbo4`)
  - 3.6x KV cache compression vs f16 (from Spiritbuun's fork, ICLR 2026 paper)
  - Near-zero quality loss (+0.9% perplexity vs f16)
  - Available types: f32, f16, bf16, q8_0, q4_0, q4_1, iq4_nl, q5_0, q5_1, turbo2, turbo3, turbo4, turbo8, turbo3_tcq, turbo2_tcq, turbo1_tcq, vbr
  - Previous: q8_0 (2x compression, ~2GB VRAM). Now: turbo4 (3.6x compression, ~1GB VRAM)
  - **Note:** turbo4 with Qwen3.6 requires ROCm 7.14. On ROCm 7.1, turbo4 produced corrupted output (slash token 14 repeated) due to the hybrid Gated DeltaNet + SSM layers.
- **Speculative decoding: NOT compatible with turbo4 + Qwen3.6**
  - `--spec-ngram-mod` crashes the server when combined with turbo4 KV cache on Qwen3.6
  - Previously worked with Qwen2.5-Coder-32B + turbo4 on ROCm 7.1
  - May work with f16 KV cache (untested, would use 16GB VRAM for 16K context)
- **Single slot (`-np 1`):** aios-core is a single-user voice assistant. Running 1 slot instead of the default 4 saves ~3x KV cache VRAM allocation.
- **Reasoning preserve (`--reasoning-preserve`):** Carries Qwen3.6's reasoning trace across turns instead of re-deriving it. Improves multi-turn quality and latency. The fork suggested this at startup.

### VBR Mode (Available, Not Default — Qwen3.6 era)

VBR (Variable Bit-Rate) KV cache is available on this build. It starts at f16 and dynamically degrades to turbo tiers as context fills, like video compression.

To enable (65K context, dynamic quality):
```
-ctk vbr -ctv vbr -c 65536
```
instead of the default:
```
-ctk turbo4 -ctv turbo4 -c 16384
```

VBR uses ~19.9GB VRAM at 65K context (vs 20.7GB at 16K with turbo4). The tradeoff: VBR has ~2x slower direct inference (4.7s vs 1.7s for 100 tokens) due to dynamic tier management overhead. Use VBR when you need large context windows; use turbo4 for speed.

### DFlash Draft Model (Available, Not Usable on 32GB VRAM)

- DFlash draft model for Qwen3.5-27B: source `z-lab/Qwen3.5-27B-DFlash` on HuggingFace
- Cannot fit alongside 27B target model in 32GB VRAM (19GB target + 4.3GB draft = 23.3GB, leaving only ~9GB for KV + compute)
- Future: When a larger GPU is available (48GB+), DFlash with Qwen3.5-27B should work

### Rebuilding the Spiritbuun fork (turbo4 KV cache)

```bash
cd <your-llama-cpp-source>
rm -rf build
ROCM_PATH=<your-rocm-path> \
cmake -B build -DGGML_HIP=ON -DAMDGPU_TARGETS=gfx1201 \
  -DCMAKE_BUILD_TYPE=Release -DGGML_HIP_NO_VMM=OFF \
  -DROCM_PATH=<your-rocm-path> \
  -DCMAKE_EXE_LINKER_FLAGS="-L<your-rocm-path>/lib/rocm_sysdeps/lib -Wl,-rpath,<your-rocm-path>/lib/rocm_sysdeps/lib" \
  -DCMAKE_SHARED_LINKER_FLAGS="-L<your-rocm-path>/lib/rocm_sysdeps/lib -Wl,-rpath,<your-rocm-path>/lib/rocm_sysdeps/lib"
ROCM_PATH=<your-rocm-path> cmake --build build --target llama-server -j$(nproc)
```

### Qwen3.6-27B Notes

- Hybrid architecture: Gated DeltaNet + SSM layers (not pure attention)
- IQ4_NL quantization (15GB) works reliably; Q4_K_M (18GB) produces corrupted output with system prompts
- turbo4 KV cache works with ROCm 7.14 (was broken on ROCm 7.1 - produced slash output)
- `--spec-ngram-mod` speculative decoding is NOT compatible with turbo4 KV cache (crashes)
- `--cache-reuse` is NOT compatible with turbo4 KV cache (silently disabled at startup — log shows "cache_reuse is not supported by this context, it will be disabled"). Removed from runtime flags Aug 2026.
- `enable_thinking=false` must be passed via `chat_template_kwargs` in API requests

### Starting the server manually (Qwen3.6 era)

```bash
LD_LIBRARY_PATH=<your-rocm-path>/lib:<your-rocm-path>/lib/rocm_sysdeps/lib:<your-rocm-path>/lib/llvm/lib \
GPU_MAX_HW_QUEUES=1 \
<your-llama-server-binary> \
  --model ~/ai-models/qwen3.6-27b-IQ4_NL-with-MTP.gguf \
  --alias Qwen3.6-27B \
  -c 16384 -np 1 -fa on --reasoning-preserve \
  -ctk turbo4 -ctv turbo4 \
  --host 0.0.0.0 --port 8080
```

### Cutting-Edge Techniques Not Yet Available (Aug 2026)

- **Paged KV cache** (`--kv-paged`): 16-token block allocation, 2.5x more concurrent sequences. PR in draft on mainline llama.cpp.
- **Dynamic KV cache** (`--kv-dynamic`): Starts at 256 cells, grows on demand. PR in draft on mainline.
- **EAGLE-3**: Draft-model speculative decoding, 2-3x speedup. Needs trained draft model for Qwen3.6 (none exists yet).
- **DFlash with Qwen3.6**: No DFlash draft model trained for Qwen3.6 family (only Qwen3.5).

### RDNA4 Vulkan Research Review (Aug 8 2026)

Reviewed `docs/temp_r9700tweaks.md` — a 50+ experiment Reddit study on the same
GPU (Radeon AI PRO R9700, gfx1201) using the **Vulkan** backend (RADV vs AMDVLK).

**Most findings do NOT apply to us** because we use ROCm/HIP, not Vulkan:
- `rm_kq=1` code change (modifies `ggml-vulkan.cpp`) — irrelevant
- RADV vs AMDVLK driver selection, `GGML_VK_ALLOW_GRAPHICS_QUEUE`,
  `GGML_VK_DISABLE_COOPMAT/MMVQ`, Mesa 25.3.6+ — all Vulkan-only

**What we tested and applied:**
- **PCIe ASPM=performance:** Research found +10.8% decode on 27B dense (Vulkan).
  We A/B tested on ROCm: baseline 32.12 t/s vs ASPM=performance 32.09 t/s —
  **no improvement** (within noise). ROCm's direct GPU memory mapping doesn't
  suffer from PCIe ASPM L1 exit latency like Vulkan's buffer transfers do.
  Not applied.
- **`GPU_MAX_HW_QUEUES=1`:** Research found ROCm idle-100%-GPU bug on R9700
  (95W wasted at idle). We weren't affected (3% idle), but added this env var
  to the systemd service as a **preventive measure** against future
  driver/kernel updates triggering it. Zero cost, zero risk.
- **`--cache-reuse 256` removed:** Was silently disabled by turbo4 KV cache
  (startup log: "cache_reuse is not supported by this context"). Dead flag,
  removed from both the base service file and override.conf.
- **Base systemd file cleaned up:** Removed stale speculative decoding flags
  (`--spec-type ngram-mod`, `--spec-ngram-mod-*`) that crash with turbo4 +
  Qwen3.6, fixed binary path, added missing flags to match actual running config.

**What we deliberately did NOT do:**
- Switch to Vulkan (we match their best-case 32.5 t/s at 32.1 t/s on ROCm,
  and would lose turbo4 KV cache)
- Force `power_dpm_force_performance_level=high` (research confirms `auto` is
  better — we're already on `auto`)
- Disable ECC (`amdgpu.ras_enable=0`) or runtime PM (`amdgpu.runpm=0`) —
  fixes for problems we don't have

**Performance note (Sep 2026, measured on the old 64k config):** ~21.5 t/s
generation for Qwen3.8-27B UD-Q4_K_M with `draft-mtp-adaptive` n=12 at 64k
ctx. Current config runs n≤16 at 131k ctx — re-measure before quoting.
