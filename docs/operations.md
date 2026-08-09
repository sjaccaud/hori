# HORI Operational Notes

This file is the canonical source of truth for hardware, runtime
details, build/test commands, and inference configuration. Paths shown
are examples — adjust to your own build locations.

## Reference Hardware

HORI is designed to run on a local GPU server with remote access via
Tailscale. The reference setup:

- **GPU server:** Headless Linux box with an AMD Radeon AI PRO R9700
  (32GB VRAM), Ryzen 7800X3D, ROCm 7.14. This is where llama.cpp and
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

- **Systemd unit:** `/etc/systemd/system/llamacpp.service`
- **Unit state:** `enabled`, `active (running)`, `Restart=always`, `RestartSec=3`
- **Current model loaded:** `Qwen3.6-27B` (IQ4_NL, 15GB, hybrid Gated DeltaNet + SSM architecture)
  - Previously used `Qwen2.5-Coder-32B-Instruct-IQ4_NL` (pure attention, worked with turbo4 on ROCm 7.1)
  - Previously used `Qwen_Qwen3.5-27B-IQ4_NL` (reasoning model, 30-60s thinking phase before content)
  - Switched to Qwen3.6 for newer architecture; requires ROCm 7.14 (ROCm 7.1 crashed with HipVMM errors and corrupted turbo4 KV output)
- **Performance:** ~0.7s for simple queries, ~2.5s with system prompt + memory retrieval
- **Memory:** ~16GB VRAM (15GB model + ~1GB turbo4 KV cache at 16K context)
- **Binary:** Spiritbuun's `buun-llama-cpp` fork (TurboQuant + VBR + DFlash)
  - Source: `github.com/spiritbuun/buun-llama-cpp`
  - Compiled with ROCm/HIP 7.14 for gfx1201 (Radeon AI PRO R9700)
  - Build config: `-DGGML_HIP=ON -DAMDGPU_TARGETS=gfx1201 -DGGML_HIP_NO_VMM=OFF -DCMAKE_BUILD_TYPE=Release`
- **Model store:** `~/ai-models` (adjust to your preferred location)
  - Contains `.gguf` files (e.g. `qwen3.6-27b-IQ4_NL-with-MTP`, `Qwen2.5-Coder-32B-Instruct-IQ4_NL`, etc.)
  - DFlash draft model for Qwen3.5-27B: tested but OOM with 27B target on 32GB VRAM
- **Runtime flags:** `--model ~/ai-models/qwen3.6-27b-IQ4_NL-with-MTP.gguf --alias Qwen3.6-27B -c 16384 -np 1 -fa on --reasoning-preserve -b 2048 -ub 2048 -ctk turbo4 -ctv turbo4 --host 0.0.0.0 --port 8080`
- **Environment:** `LD_LIBRARY_PATH=<your-rocm-path>/lib:<your-rocm-path>/lib/rocm_sysdeps/lib:<your-rocm-path>/lib/llvm/lib` + `GPU_MAX_HW_QUEUES=1` (prevents ROCm idle-100%-GPU bug)

### ROCm 7.14

- Installed via runfile installer (adjust path to your installer):
  `bash rocm-installer-7.14.0-6.run deps=install gfx=gfx1201 rocm`
- Runtime at `/opt/rocm/` (or wherever you installed it)
- SDK headers: extract dev packages from the runfile (the default `core`
  install only includes runtime, not dev headers)
- `LD_LIBRARY_PATH` must include all three lib directories when running llama-server
- Fixes vs ROCm 7.1: HipVMM crashes eliminated, turbo4 KV cache works with Qwen3.6's hybrid architecture

### Inference Optimizations (Aug 2026)

- **Context window: 16384** (down from 65536)
  - KV cache at f16/65K = 16GB VRAM (97% of 34GB, zero headroom)
  - KV cache at turbo4/16K = ~1GB VRAM (3% of 34GB, 13GB free)
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

### VBR Mode (Available, Not Default)

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

### DFlash Draft Model (Available, Not Usable on 34GB VRAM)

- DFlash draft model for Qwen3.5-27B: source `z-lab/Qwen3.5-27B-DFlash` on HuggingFace
- Cannot fit alongside 27B target model in 32GB VRAM (19GB target + 4.3GB draft = 23.3GB, leaving only ~9GB for KV + compute)
- Future: When a larger GPU is available (48GB+), DFlash with Qwen3.5-27B should work

### Rebuilding the Binary

If the binary needs to be rebuilt (e.g. after a system update):

**Spiritbuun fork** (for turbo4 KV cache):
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

**Mainline llama.cpp** (no turbo4, but stable):
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

### Qwen3.6-27B Notes

- Hybrid architecture: Gated DeltaNet + SSM layers (not pure attention)
- IQ4_NL quantization (15GB) works reliably; Q4_K_M (18GB) produces corrupted output with system prompts
- turbo4 KV cache works with ROCm 7.14 (was broken on ROCm 7.1 - produced slash output)
- `--spec-ngram-mod` speculative decoding is NOT compatible with turbo4 KV cache (crashes)
- `--cache-reuse` is NOT compatible with turbo4 KV cache (silently disabled at startup — log shows "cache_reuse is not supported by this context, it will be disabled"). Removed from runtime flags Aug 2026.
- `enable_thinking=false` must be passed via `chat_template_kwargs` in API requests

### Starting the server manually

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

### Cutting-Edge Techniques Not Yet Available

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

**Current performance (Aug 8 2026):** 32.1 t/s decode, 79% of 640 GB/s
bandwidth ceiling. This is near the hardware limit for a 15GB dense model.

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
- **Open WebUI:** `http://<your-tailscale-ip>:3000` (primary container `open_webui_aios`, host port `3000`)

## Ports

- `8080/tcp` -> `llama-server` (must remain free)
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
   - **Model ID**: paste one of the exact IDs returned by `/v1/models`. Good defaults:
     - `Qwen3.6-27B` (current default)
     - `Qwen2.5-Coder-32B-Instruct-IQ4_NL` (coding)
     - Other available models:
       - `gemma-4-26B-A4B-it-qat-UD-Q4_K_XL`
       - `gemma-4-e4b-it-Q4_K_M`
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
systemctl status llamacpp
systemctl is-active llamacpp
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
- **Current model:** `Qwen3.6-27B` (via llama-server)

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
- Run: `PYTHONPATH=. ./venv/bin/python3 scripts/ingest_codebase.py` (requires embedding server on :8081)

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

## Embedding Server (:8081)

Dedicated embedding server for RAG/memory, separate from the chat model to avoid VRAM contention.

- **Binary:** `~/llama.cpp/build/bin/llama-server`
- **Model:** `~/ai-models/nomic-embed-text-v1.5.Q8_0.gguf` (140 MiB, 768-dim vectors)
- **Start:** `llama-server --model ~/ai-models/nomic-embed-text-v1.5.Q8_0.gguf --host 127.0.0.1 --port 8081 --ctx-size 2048 --embedding --n-gpu-layers 0`
- **Endpoint:** `POST http://localhost:8081/v1/embeddings` (OpenAI-compatible shape)
- **Note:** Runs on CPU only (`--n-gpu-layers 0`) to keep VRAM free for chat models

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
  - `scripts/qdrant_ingest.py` (now uses `/v1/embeddings` on :8081)
  - `scripts/test_rag.py` (same)

## Retired Services

- **Interaction Surface (:5679):** Mock server removed. Open WebUI (:3000) and aios-core (:5680) are the real front doors.
- **Ollama:** Fully decommissioned. Dead Ollama URL removed from Open WebUI config to eliminate 10s model-list timeout.

## Open WebUI Configuration

- **Container:** `open_webui_aios` (Docker, `network_mode: host`)
- **Port:** 3000
- **OpenAI connections:**
  - `http://127.0.0.1:5680/v1` (aios-core, primary)
  - `http://127.0.0.1:8080/v1` (direct llama-server, for raw model access)
- **Ollama:** Disabled (`ollama.enable: false`, `ollama.base_urls: []`)
- **Background tasks disabled** (each was a separate LLM call competing for the single model slot):
  - `task.title.enable: false` (title generation)
  - `task.follow_up.enable: false` (follow-up suggestions)
  - `task.tags.enable: false` (auto-tagging)
  - `task.query.search.enable: false` (search queries)
  - `task.query.retrieval.enable: false` (retrieval queries)
  - `task.voice.prompt.enable: false` (voice prompt generation)
- **DB config location:** `/app/backend/data/webui.db` (SQLite, inside container)

## Destructive Pattern Gate

- **Config:** `services/aios_core/config.py` -> `DESTRUCTIVE_PATTERNS`
- Patterns are intentionally specific to avoid false positives (e.g. `\bformat\b` was removed because "JSON format" in title-generation prompts triggered the red team)
- Current patterns target actual destructive commands: `rm -rf`, `mkfs`, `sudo rm`, `chmod 777`, `delete (all|everything|the|old|files|...)`, `wipe (the|disk)`, `drop (table|schema|database)`, `format (c:|disk|drive)`, `git push --force`, `git reset --hard`, etc.

## Notes

- `llama-server` warns that CORS is `*` and no API key is set. This is acceptable on the Tailscale mesh but should not be exposed to untrusted networks.
- `systemctl` changes require `sudo` and cannot be performed by unprivileged sessions in this environment.
- The embedding server on :8081 is not in the systemd unit yet; it must be started manually or added as a separate service.
- aios-core uses `httpx` (async) for LLM calls, not `requests` (blocking). This is critical - blocking calls caused the server to hang during concurrent requests (model polling + chat).
- aios-core must be started manually: `PYTHONPATH=. ./venv/bin/python3 -m services.aios_core.main &`
- The systemd service for llama-server now uses a single model (`--model`), not router mode. This is because Qwen3.6 requires specific turbo4 KV flags that don't work with all models.

## Benchmark (Aug 2026)

| Metric | Original | q8_0 + ngram | Turbo4 + ngram (Qwen2.5) | Turbo4 (Qwen3.6, current) | VBR + ngram (optional) |
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

**Current config** (turbo4 + Qwen3.6 on ROCm 7.14) uses the least VRAM (16GB, 47%) at the cost of speculative decoding. The 18GB free VRAM leaves room for a future draft model or larger context. Switch to VBR when you need 65K context windows (e.g. long document analysis).

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
