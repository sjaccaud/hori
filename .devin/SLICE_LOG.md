# HORI Slice Log

> The single source of truth for "where are we right now."
> Any new session reads this first (see .devin/AGENTS.md → Crash Recovery Protocol).

## Current Slice

### SLICE-13-REMOTE-MCP-TOOLS: Expose remote tools over Tailnet via MCP, harden Tailscale Serve, and add nano endpoint. — HANDOFF

**Branch:** `rebrand/hori` (to be merged after demo)

**What it is:** A Tailnet-only MCP-over-SSE server on aios_core that lets
remote clients call `get_status`, `read_file`, `run_safe_command`,
`search_web`, `fetch_url`, `memory_search`, and `memory_store` with the same
admin token used for `/admin/api`. Tailscale Serve is hardened to a
port-aware allowlist, `nano` is exposed on `:8444`, and the audit/harden
scripts are updated to reflect the new remote surface.

**Demo criterion:**
- `tailscale serve status` shows only intended ports/paths; `audit_funnel.sh`
  passes.
- From a remote client (or local `curl` over Tailnet), a valid admin token opens an
  SSE session at `https://<tailnet>/mcp/sse`, lists the 7 tools,
  and successfully calls `get_status` or `read_file`.
- `https://<tailnet>:8444/v1/models` returns `nano`.
- Full relevant test suite passes.

**Status:**
- PRD, work order, unit, integration, regression, and adversarial tests passing.
- Code committed to `rebrand/hori`.
- `services/integration_tests/test_remote_mcp.py` added and passes when `AIOS_ADMIN_TOKEN` is set.
- `docs/operations.md` and `.devin/AGENTS.md` updated with MCP and nano endpoints.
- `.devin/ubiquitous_language.md` updated with `MCP-over-SSE` and `Nano Chat` terms.
- `audit_funnel.sh` passes; Tailscale Serve exposes 443, 3443, 8443, 8444 with intended paths.
- Live `/mcp/sse` verified over Tailnet (returns `event: endpoint` with session_id; 401 without token).
- `https://<tailnet>:8444/v1/models` verified from Tailnet and returns `nano`.
- aios_core restarted via `sudo systemctl restart aios_core` by the user.

**Remaining before merge:**
- None. aios_core was restarted via `sudo systemctl restart aios_core` by the user. The `AIOS_ADMIN_TOKEN` rotation decision was left as-is per the user.

**PRD:** `.devin/prd/remote-mcp-tools.md`
**Work order:** `core/state/proposed_work_orders/60b7e3e7-f9b6-404b-9848-8c5c87750700.json`

**Open product/safety decisions made:**
- Harden first, then add explicit paths.
- Core 4 tools + memory search/store for the first slice.
- No public Funnel; Tailnet mesh only.
- Restart was done manually because `sudo systemctl` requires a TTY; the canonical systemd path is `sudo systemctl restart aios_core`.

## Completed Slices

### [2026-09-07] Local web search: SearXNG verified + model-decided `search_web` pilot on `qwen-dsh-low`

**Problem:** User wanted local/free web search wired into Open-WebUI with
automatic "search when needed" behavior, plus a fix for flaky tool-call
loops.

**What was found:**
- SearXNG (`searchrouter_aios`, `searxng/searxng:latest` on 127.0.0.1:8081)
  was already running and OWUI's `web.search.*` DB config was already
  correct — the stale `searchrouter` build service in
  `services/open-webui/docker-compose.yml` (no Dockerfile in
  `../searchrouter`, container-name collision) was the only real defect;
  removed.
- OWUI 0.11.3 exposes `search_web`/`fetch_url` as *model-callable* builtin
  tools, gated by 4 conditions: `builtinTools.web_search` AND
  `web.search.enable` AND model capability AND request
  `features.web_search` (see `open_webui/utils/tools.py`). With native
  function calling, OWUI deliberately skips forced RAG injection and lets
  the model decide.
- `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS` defaulted to 256 — set to 4 in
  compose (suspected cause of "tool call loop" stalls).
- `llamacpp.service` was `inactive` at session start (port 8080 down →
  OWUI `/api/models` empty, every chat "Model not found"). Its
  `llamacpp.service.d/override.conf` already points ExecStart at
  `run-llama.sh` (router mode) — the unit is current, just stopped.

**What was built:**
- `services/integration_tests/test_web_search_pipeline.py` — 12 tests in
  4 layers: static config (compose cap, fix_tooling helpers, pilot prompt
  rules), live services (SearXNG JSON, OWUI health/config, retrieval
  endpoint e2e via in-container JWT mint), model metadata gate, and
  llama-dependent checks (skipped when :8080 is down).
- `fix_tooling.py`: `WEB_SEARCH_MODELS` set + `builtin_tools_for()` /
  `default_features_for()` helpers + `WEB_SEARCH_BEHAVIOR` prompt rules
  (when to search, cite-or-say-unknown, `run_safe_command` is host-only —
  the model tried `run_safe_command`+`curl` for web access on first test).
- `qwen-dsh-low` model row patched: `builtinTools.web_search=true`,
  `web_search` in `default_feature_ids`, search-rules prompt appended.

**Surprises:**
- Builtin tools are UI-only: `use_builtin_tools` in middleware.py requires
  a top-level `session_id` in the request ("API callers don't expect
  hidden tools"). Without it, `search_web`/`fetch_url` are never
  registered — the earlier "model chose curl" runs were testing a path
  where the tool didn't exist. With `session_id` + `features.web_search`,
  the model calls `search_web` both on request AND unprompted for
  fresh-info questions (verified live).
- `/api/chat/completions` passes tool calls through to the client
  (`finish_reason: tool_calls`) rather than executing server-side — the
  OWUI UI executes them in its own loop.
- `default_feature_ids` is consumed by the *frontend* only
  (`utils/automations.py`); raw API callers must pass `features`
  explicitly.
- `docker cp` of webui.db into a *running* container does not take effect
  (old inode held open) — apply-model-config.sh's stop/copy/patch/start
  ordering is load-bearing.
- A `/tmp/model-archive-eval-*` llama-server benchmark held 18.9 GB VRAM
  and cut generation to ~0.3 tok/s; killed at user request (they'll
  reschedule the bench). llamacpp.service had been cleanly stopped at
  04:00 before the eval — service is `enabled`, unit override already
  points at run-llama.sh.

**Verified live:** `search_web` fires for `qwen-dsh-low` (explicit request
and unprompted "latest llama.cpp release" question → clean query). Tool
execution + citation display happen in the OWUI UI loop.

**Follow-up (same day):** after the pilot felt usable, all interactive
models were armed (`WEB_SEARCH_MODELS = ALL_QWEN_DEVSTRAL - {qwen-256k-batch}`)
and display names were shortened to ≤3 words for the mobile selector
(Fast Chat, Long Docs, Deep Plan, Batch Only, Think Mode, Balanced,
Max Quality, Devstral Code, Agentic Fast, Agentic Best, Agentic Mid).
Added `system_prompt_for()` so WEB_SEARCH_BEHAVIOR applies to every armed
model. Re-applied `patch_openai_model_names.py` inside the container —
the earlier `docker compose up` recreate had wiped the openai.py patch.
Verified: `qwen-fast` calls `search_web` for external questions and
`uname -r` for local-state questions (local-first prompt routing works).
14/14 integration tests pass.

**OaK declutter (same day):** OaK was a research stepping-stone, not a
persistent chat surface. Set `is_active=0` on `consensus-oak`,
`covenant-oak`, `cross-project-oak` models + the `oak_tool_selector`
filter (reversible — rows kept for revival). Tool-server connections
were already `enable:false`; servers 8767/8768 are not running. Recall
in normal chat: `read_file`/`run_safe_command` already reach
`~/Projects/*/.oak/kernel.json` (`~` is a local-tools read root), and an
OWUI memory now points future chats at the kernel paths — no bespoke UI
needed. Verified: `/api/models` shows only the 11 short-named presets.

**Selector polish (same day):** canonical dropdown order
(`MODEL_ORDER` in fix_tooling → `ui.model_order_list`): Fast Chat →
Think Mode → Long Docs → Deep Plan → Balanced → Max Quality →
Devstral Code → Agentic Fast → Agentic Mid → Agentic Best → Batch Only.
Short use-case `MODEL_DESCRIPTIONS` written to `meta.description`
(selector subtitle / admin list). New contracts: order == MODEL_ORDER,
descriptions canonical, both asserted live against webui.db.
Gotcha: `docker exec` heredocs need `-i` or stdin is dropped silently.

**Speed/QoL pass (same day, all eight suggestions):**
- `nano` model: Llama-3.2-3B on dedicated user service `llama-nano.service`
  (:8090, ~2.5GB VRAM, coexists with the 27B — no router eviction). OWUI
  second `openai.api_base_urls` entry + minimal workspace row (no tools —
  3B can't carry them; not in the tool-server filter allowlist). 95ms
  replies. Unit committed at `services/open-webui/llama-nano.service` +
  `make install-llama-nano`/`check-llama-nano`, wired into `aios-up/down`.
- `task.model.default` → `qwen-fast` (was `devstral-coding` — titles/tags
  were generated by the 24B).
- SearXNG: disabled duckduckgo + startpage engines (persistent CAPTCHAs).
  Note: `autocomplete` must be a *suggestion backend* name — "mojeek" is
  not valid and 500s every request; reverted to "duckduckgo" (suggestion
  endpoint is separate from the disabled search engine).
- `ui.default_pinned_models` normalized to JSON array (was CSV string).
- `canonical_config` block in fix_tooling.py writes task model, pinned/
  order lists, web.search.* (5/3), searxng URL, openai.api_base_urls —
  DB is source of truth, so apply restores instead of drifting. Compose
  env updated to match (RESULT_COUNT=5, CONCURRENT=3).
- `/system/state` now HTTP-probes the full 9-service stack (was 5 TCP
  checks); admin panel shows them automatically.
- `llamacpp.service` restarted via `sudo -n systemctl` (a targeted
  NOPASSWD rule covers systemctl service ops; `sudo -n true` still fails).
  Manually-started run-llama.sh killed so the service owns :8080.
- 68/68 tests pass (17 pipeline + 51 aios_core).

### SLICE-12-PROJECT-OFFLOAD: Back up non-daily `~/Projects` to private GitHub repos and optionally free local 2TB space. — COMPLETE

**Branch:** `rebrand/hori` (merged)

**What it is:** A tool in `~/Projects/project-offload` that walks `~/Projects`,
compares each to a `keep` list, creates private GitHub repos for candidates,
pushes them, and optionally deletes local copies. It protects required projects
(`aios`, `model-archive`, etc.) from accidental deletion.

**Built:**
- `~/Projects/project-offload` Python project with `scan`, `plan`, `push`, `clean`.
- `src/project_offload/scanner.py` with keep/candidate/protected tags and sizes.
- `src/project_offload/pusher.py` using `gh repo create`, HTTPS remotes, and `gh auth`.
- `src/project_offload/cleaner.py` requiring `--project` and `--confirmed`.
- `src/project_offload/cli.py` with dry-run support and `--extra-keep`/`--drop-keep`.
- 7 tests passed.

**Initial keep list:**
- `aios` (HORI core)
- `model-archive` (offline model manifest)
- `krill_harvester` (autonomous trading daemon)
- `consensus_protocol` (book/OaK IP)
- `book_forge` (local LLM book editor)
- `covenant` (sociocratic M2M protocol)
- `oak-bench` (OaK knowledge harness)
- `project-offload` (this tool)

**Demo:**
- `scan` showed 18 projects, 6.33 GB total, 1.10 GB candidates.
- `push --project passive_stack` created a private `passive_stack` GitHub repo and pushed.
- `clean --project passive_stack --confirmed` removed the local copy.

**Safety:**
- No protected or keep-listed projects were deleted.
- No token was exposed in code or conversation.
- `passive_stack` was private on GitHub after push.

**Numbers:**
- `~/Projects`: 18 remaining (5.22 GB keep/protected, 1.10 GB candidates left to offload).

### SLICE-11-MODEL-PREPPER: Build an offline open-weight model dream-team manifest and downloader. — COMPLETE

**Branch:** `rebrand/hori` (merged)

**What it is:** A new project at `~/Projects/model-archive` that curates a
dream-team list of open-source, open-weight models for offline storage on
`/mnt/4TB` and `/mnt/8TB`, with a downloader that respects the 64GB VRAM target
and never writes to the 2TB system drive.

**Built:**
- `~/Projects/model-archive` Python project (stdlib + huggingface_hub + pyyaml).
- 48 seed models across general, coding, reasoning, long-context, medical,
  legal, uncensored, multilingual, MoE, SLM, VLM, TTS, STT, embedding.
- `manifest.yml` auto-generated with VRAM, size, priority, source tier, license,
  topology path, and rationale.
- `TOPOLOGY.md` with directory layout, 32GB/64GB VRAM strategy, and trust tiers.
- `model-archive` CLI: `generate-manifest`, `list`, `plan`, `download`, `verify`.
- `download` supports dry-run, priority/VRAM/tag/`--id` filters, disk-space
  guard, resume, and `HF_TOKEN`.
- 14 tests passed; one real download verified (`BAAI/bge-base-en-v1.5` to
  `/mnt/4TB/models/prepper/embedding/bge-base-en-v1-5`).

**Numbers:**
- 48 models, ~447 GB estimated total, ~125 GB priority 1.
- `/mnt/4TB` has 3.2 TB free; `/mnt/8TB` has 5.5 TB free.

**Demo:**
- `model-archive list --priority 1` shows the must-have list.
- `model-archive plan` shows the full download plan.
- `model-archive download --id bge-base-en-v1-5` fetched the model into the
  correct topology directory.
- `model-archive verify` shows `bge-base-en-v1-5` present and the rest missing.

### SLICE-10-PROJECT-CREATION-POLISH: Streaming, better templates, and Open-WebUI integration for safe project creation. — COMPLETE

**Branch:** `rebrand/hori` (merged)

**What was built:**
- Better templates in `services/aios_core/project_creation.py`: `python-cli`, `python-package`, `fastapi-app`, `data-science`, `static-site`.
- Smarter intent detection: creation verbs + project/app/service/site/api, plus quoted/called/named name extraction.
- Streaming short-circuit in `services/aios_core/main.py`:
  - `/chat` and `/v1/chat/completions` (non-stream and stream).
  - `/v1/voice/chat/stream` and `/v1/voice/chat`.
- TTS summary in streaming voice so the manifest is shown as text while a short summary is spoken.
- `services/open-webui/project_tools_server.py` on `127.0.0.1:8769` with OpenAPI tools:
  - `propose_create_project`
  - `confirm_create_project`
  - `rollback_project`
- `project-tools.service` and Makefile targets (`install-project-tools`, `check-project-tools`).
- `services/open-webui/fix_tooling.py` adds `project-tools` to `tool_server.connections` and the tool-server filter for all Qwen/Devstral models.
- `scripts/health.py` checks `project-tools`.
- Tests: `services/aios_core/test_project_creation.py`, `services/aios_core/test_main.py`, `services/open-webui/test_project_tools_server.py`.

**Verification:**
- `make test-unit` — 443 passed, 2 skipped.
- `make health` — all green, `project-tools` OK.
- Live `POST /chat`: "Create a FastAPI web service for a todo API" → dry-run manifest + token.
- Live confirmation "yes" → created `~/Projects/todo-api` (FastAPI template).
- Live `POST /v1/voice/chat`? Not tested; streaming path has the same `_maybe_create_project` short-circuit.
- Live tool server: `propose_create_project` returned manifest and token on `127.0.0.1:8769`.
- `make open-webui-apply` completed and restarted Open-WebUI.

## Completed Slices

### SLICE-09-SAFE-PROJECT-CREATION: Let the assistant safely create a new project under `~/Projects` with dry-run, explicit confirmation, confinement, audit, and rollback. — COMPLETE

**Branch:** `rebrand/hori` (merged)

**What was built:**
- `services/aios_core/project_creation.py` with `propose_project`, `create_project`, and `rollback_project`.
- Confinement to `~/Projects` (or `AIOS_PROJECTS_DIR`), dry-run manifest, explicit confirmation token, audit log at `~/.local/share/aios/project_creation_audit.jsonl`, and 5-minute rollback window.
- Templates: `empty`, `python-cli`, `python-package`.
- Keyword-based intent detection for "create a new project" and confirmation.
- `/chat` short-circuits to the project-creation workflow so the assistant never silently creates files.
- `services/aios_core/test_project_creation.py` and updates to `test_main.py`.

**Verification:**
- `make test-unit` — 439 passed, 2 skipped.
- `make health` — all green.
- Live `POST /chat`: "Create a new project in ~/Projects for a Python CLI todo app" → dry-run manifest + token.
- Live confirmation "yes" → created `~/Projects/python-cli-todo-app`.
- Rollback via audit token → removed the project.
- Pre-commit hook correctly blocked a test path containing PII; fixed to use generic `/home/user/Projects`.

## Completed Slices

### SLICE-08-CHAT-WEB-SEARCH: Patch /chat to inject web search context so it never leaves the user hanging. — COMPLETE

**Branch:** `rebrand/hori` (merged)

**What was built:**
- `services/aios_core/main.py` `/chat` endpoint now awaits `_maybe_web_search` and appends the results to `system_prompt`, mirroring `/v1/chat/completions`.
- Added `test_chat_injects_web_search_context` in `services/aios_core/test_main.py`.

**Verification:**
- `make test-unit` — 437 passed, 2 skipped.
- `make health` — all green.
- Live `POST /chat` with `What is the best local LLM for coding in 2026?` now returns a real, cited answer instead of 'Let me see what I can find.'

### SLICE-07-NO-KEY-SEARCH: Stub out a zero-API-key search backbone (SearXNG + Jina Reader) and leave paid keys as a plug-and-play upgrade. — COMPLETE

**Branch:** `rebrand/hori` (merged)

**What was built:**
- Replaced the black-box `open-webui-searchrouter:latest` container with `searxng/searxng:latest` on `127.0.0.1:8081`.
- Added `services/searchrouter/settings.yml` with JSON output and the limiter disabled for local API use.
- Added `services/searchrouter/.env` (gitignored) for `SEARXNG_SECRET`, generated by `make search-secret`.
- Updated `Makefile` `aios-up` to ensure the SearXNG secret exists and `health` to probe the real `/search` endpoint.
- Added `SearXNG` as a source in `services/aios_core/multi_search.py`, weighted above DDG.
- Added `_enrich_with_jina()` using the no-key `r.jina.ai/URL` Jina Reader.
- `multi_search(..., enrich=True)` enables full-page Markdown extraction; default is `enrich=False` to stay under the 3s web-search timeout.
- Updated `test_multi_search.py` with SearXNG tests, an enrichment test, and a safer `_patch_sources` that defaults unpatched sources to empty.
- Left `~/.config/aios/searchrouter.env` empty but documented for future Brave/Tavily/Exa/Jina keys.

**Verification:**
- `make test-unit` — 436 passed, 2 skipped.
- `make health` — all critical services green, SearXNG returns JSON results.
- `PYTHONPATH=. venv/bin/python3` direct `multi_search('SearXNG 2026')` returned SearXNG + DDG results.

**Surprises / notes:**
- SearXNG starts in a few seconds and already returns useful results on `127.0.0.1:8081/search?...&format=json`.
- Jina Reader `r.jina.ai/URL` works without an API key, but page fetches can take several seconds. We wired it in as an optional enrichment, not the default, so the 3s web-search timeout isn't violated.
- The old `searchrouter.env` is no longer consumed by the SearXNG container; it now serves only as a holding place for future paid keys.

**Deferred:**
- Add paid keys (Brave / Tavily / Exa / Jina) next week and optionally switch `aios_core` or Open-WebUI to a paid provider.
- Decide whether to enable `multi_search(..., enrich=True)` by default once a paid/faster content-extraction backend is available.

### SLICE-06-ON-THE-GO-ASSISTANT-HARDENING: Reconcile Open-WebUI config, make the default assistant useful on mobile, and close surface/privacy gaps. — COMPLETE

**Branch:** `rebrand/hori` (merged)

**What was built:**
- `qwen-fast` is the default assistant with `function_calling: native`, local tools (`read_file`, `run_safe_command`, `get_status`), and web search / code interpreter toggleable-but-off.
- All Qwen/Devstral/DSH models have consistent prompts, tool access, and context/max_tokens settings.
- `get_status` tool added to the local-tools server so the assistant can answer "what's my system status?" in one call.
- `make health` gives a single green/red status for the AIOS spine; `make aios-up` / `make aios-down` manage the stack.
- Searchrouter recreated to bind `127.0.0.1:8081`; paid search keys moved to `~/.config/aios/searchrouter.env` and the old exposed keys removed from the container.
- Qdrant started with `restart=always`.
- `scripts/hardening/batch_privileged.sh` set `HORI_SERVICE_HOST=127.0.0.1`, restarted `llamacpp` and `aios_core`, and enabled the whole-Ubuntu restic backup timers.

**Verification:**
- `make test-unit` — 433 passed, 2 skipped.
- `make health` — all critical services green.
- `ss -tlnH` confirms all AI ports (`3000`, `8080`, `8081`, `8082`, `5680`, `6333`) are on `127.0.0.1`.
- `systemctl list-timers restic-ubuntu-backup` shows the timer is active.

**Surprises / notes:**
- `docker inspect searchrouter_aios` printed the existing Tavily and Exa keys, so those keys are now exposed in conversation history. The old container was destroyed and the keys removed, but they still need to be rotated in the provider dashboards.
- `yq` was not installed on the system, so `batch_privileged.sh` did not update `~/.config/hori/hori.yaml`. The env token is enough for `aios_core`; install `yq` if you want `hori.yaml` kept in sync.
- The Ubuntu telemetry timers (`ubuntu-insights-collect.timer`, `ubuntu-insights-upload.timer`) were not present on this system, so the script skipped them.

**Deferred:**
- Rotate Tavily and Exa keys in their provider dashboards; add the new key(s) to `~/.config/aios/searchrouter.env` when you pick a paid search provider.
- Paid search API comparison (Brave / Tavily / Exa) is still an open product decision.

### SLICE-DEVIN-CLI-OPENWEBUI-01: Restore Devin CLI diagnostic permissions and Open-WebUI `local-tools` server — COMPLETE

**Branch:** `rebrand/hori` (repair work, no feature branch)

**What was built:**
- Replaced the 190-entry positive Devin CLI allowlist in `~/.config/devin/config.json`
  with a concise negative-security policy: allow broad read-only diagnostics and
  project writes, deny root/shells/package installs/destructive commands, and ask
  for file-moving/git-pushing/system mutations. Backed up the old config to
  `/tmp/devin_config_backup_20260904.json`.
- Created `services/open-webui/local_tools_server.py`, an OpenAPI tool server
  on `127.0.0.1:8766` exposing `read_file` and `run_safe_command` for Open-WebUI.
- Added `services/open-webui/local-tools.service` and Makefile targets
  `install-local-tools` and `check-local-tools`.
- Added `services/open-webui/test_local_tools_server.py` with 15 tests
  covering allowed/denied reads, shell metacharacters, package installers,
  `find`/`xargs` injection, timeout, and output capping.

**Verification:**
- `make test-unit` — 432 passed, 2 skipped.
- `systemctl --user status local-tools` shows active/running.
- `http://127.0.0.1:8766/health` returns `{"status": "ok"}`.
- `/read_file` and `/run_safe_command` (`inxi -F`, `find`, `lscpu`) return
  expected output.

**Surprises / notes:**
- The existing `local-tools` OpenAPI server source and service were missing
  from the repo; Open-WebUI's `fix_tooling.py` was pointing to a non-existent
  process on `127.0.0.1:8766`.
- The regex for shell-metacharacter detection took several iterations to escape
  correctly; the lesson is to use explicit character checks over complex regex.
- Devin CLI's cached permission policy means the new `~/.config/devin/config.json`
  only takes effect in a **new Devin session** or after switching permission mode.

**Deferred:**
- New Devin session / permission-mode switch to activate the rewritten config.
- `make open-webui-apply` to re-apply the Open-WebUI tool filter and DB entries
  if the UI does not pick up the server automatically.
- Adding a `env` parameter to `run_safe_command` for commands that need
  `MESA_VK_DEVICE_SELECT` etc.
- Deciding whether to move the server source into `services/open_webui/` once the
  hyphen-vs-underscore package issue is addressed.

### SLICE-SYSTEM-ROOT-BROKER-05: Read-only GPU / Docker / firewall / fail2ban queries through the root broker — COMPLETE

**Branch:** `slice/system-root-broker-05` (merged to `rebrand/hori`)

**What was built:**
- `.devin/prd/system-root-broker-05.md` — interface, safety, and test contract.
- `services/broker/allowlist.py` — added `gpu_status`, `docker_status`,
  `ufw_status`, `fail2ban_status` with read-only params.
- `services/broker/broker.py` — new operation handlers dispatching to
  `/usr/local/sbin/hori-{gpu,docker,ufw,fail2ban}`.
- `services/broker/client.py` — CLI supports the four new operations and their
  options.
- `services/broker/sandbox.py` — per-operation Landlock allowlists for GPU
  sysfs, docker, ufw, and fail2ban.
- `scripts/hardening/hori_{gpu,docker,ufw,fail2ban}.py` — sandboxed wrappers.
- `scripts/hardening/install_root_broker.sh` — installs the new wrappers and
  updates `/etc/sudoers.d/system-root-broker`.

**Verification:**
- `make test-unit` — 417 passed, 2 skipped.
- `make test-adversarial` — 147 passed.
- `make test-integration` — 25 passed, 2 skipped.
- `make test-regression` — 7 passed.
- Broker client live commands:
  - `gpu_status` → `sandbox_profile: "seccomp+landlock"`, parsed AMD GPU stats.
  - `docker_status --query ps` → `sandbox_profile: "seccomp+landlock"`, container list.
  - `ufw_status` → `sandbox_profile: "seccomp+landlock"`, firewall rules.
  - `fail2ban_status --jail sshd` → `sandbox_profile: "seccomp+landlock"`, jail stats.
- `sudo visudo -c` passes implicitly via installer.

**Surprises / notes:**
- `docker` query parameter is allowlisted, not arbitrary: only `ps`,
  `system_df`, `info` are accepted.
- `ufw` is a Python script, so the `ufw_status` sandbox needs `/usr/bin/python3`
  to be allowed — same as `fail2ban-client`.
- GPU state is read from DRM sysfs (`/sys/class/drm/cardN/device`) and hwmon,
  not `rocm-smi`, which keeps Landlock allowlists minimal and avoids device
  node access.

**Deferred:**
- Removing `<user>` from `sudo` and `docker` (requires more broker coverage or
  a later hardening step).
- Tamper-evident audit chaining.
- Firewall/fail2ban mutation operations.

### SLICE-SYSTEM-ROOT-BROKER-04: sudoers hygiene and `cli-admin` break-glass — COMPLETE

**Branch:** `slice/system-root-broker-04`

**What was built:**
- `scripts/hardening/install_cli_admin.sh` creates the `cli-admin`
  break-glass account (real shell, sudo group, password set by the
  product owner), removes `<user>` from `lxd`/`ollama`/`lpadmin`/`cdrom`/
  `dip`, deletes stale `/etc/sudoers.d/*.bak.*` and old per-user
  fragments, and writes `/etc/sudoers.d/hori-user` with the consolidated
  safe NOPASSWD rules.
- `scripts/hardening/test_cli_admin.sh` verifies the install.
- `scripts/hardening/setup_admin_sudoers.sh` updated so it no longer
  creates new `.bak.*` files when it removes legacy admin sudoers.
- The primary `<user>` keeps `sudo` and `docker` so Devin CLI can still
  optimize the rig. `plugdev` is left in place for openrazer.

**Verification:**
- `cli-admin` exists, has `/bin/bash`, is in the `sudo` group, and can
  run `sudo systemctl status llamacpp`.
- `<user>` is no longer in `lxd`, `ollama`, `lpadmin`, `cdrom`, or `dip`.
- `/etc/sudoers.d/` contains only `README`, `hori-user`, and
  `system-root-broker`; old `.bak.*` files and per-user fragments gone.
- `sudo visudo -c` passes.
- `make test` — unit, integration, regression, and adversarial all green.
- Broker client `service_status --service llamacpp` still returns
  `sandbox_profile: "seccomp+landlock"`.

**Surprises / notes:**
- `make test` cannot run as `cli-admin` because the repo `logs/` and
  `.pytest_cache/` are owned by the primary `<user>`; this is correct
  and confirms `cli-admin` is not a daily-driver account.
- `ollama` service was already `inactive` and `lxc` was not configured,
  so removing the `ollama` and `lxd` groups was safe.
- `plugdev` was left in place because openrazer likely needs USB device
  access.

**Deferred:**
- Removing `<user>` from `sudo` and `docker` (Slice 5 or later, after
  broker/Devin CLI privileged command paths are expanded or a narrow
  allowlist is proven).
- Tamper-evident audit chaining (still a candidate slice).

### SLICE-SYSTEM-ROOT-BROKER-03: confirmation gates and sandboxing — COMPLETE

**Branch:** `slice/system-root-broker-01` (`7d5ba23`)

**What was built:**
- `confirmed_by` confirmation identity gating for `service_restart` and
  `service_stop`; `confirmed_by` must not be the broker token and must match
  a safe pattern/length.
- Sandboxed wrappers `hori-systemctl` and `hori-journalctl` installed to
  `/usr/local/sbin`; each reads a JSON request, validates it, applies
  Landlock/seccomp, and execs only the intended `systemctl`/`journalctl`
  command.
- `services/broker/sandbox.py` wraps the existing tool daemon Landlock and
  seccomp primitives with broker-specific path allowlists.
- `services/broker/broker.py` routes all four broker operations through the
  wrappers, parses their JSON, and carries `sandbox_profile` into audit.
- `scripts/hardening/install_root_broker.sh` installs both wrappers and
  narrows sudoers to only those two fixed paths.
- `services/broker/system-root-broker.service` hardened for sandboxed
  children with writable `/run/systemd`, `/run/dbus`, and `/var/log/journal`.
- `services/tool_daemon/landlock.py` gained an `execute` flag and
  file-vs-directory access rights so Landlock can confine processes that
  `exec` real binaries.
- Adversarial tests for token-as-confirmation, shell meta-characters in
  `confirmed_by`, ptrace blocking, `/etc/shadow`/`/etc/group`/`~/.ssh`
  denial, TCP socket denial, and audit `confirmed_by` + `sandbox_profile`.

**Verification:**
- `services/broker/test_broker.py` — 17 passed.
- `tests/adversarial/test_root_broker.py` — 17 passed.
- `make test-unit` — 411 passed, 2 skipped.
- `make test-adversarial` — 142 passed.
- `make test` — all suites green.
- Live smoke test: unconfirmed `service_stop` on `llamacpp` denied; confirmed
  `service_stop` and `service_restart` succeeded with
  `sandbox_profile: "seccomp+landlock"`; audit records contain both
  `confirmed_by` and `sandbox_profile`.

### SLICE-SYSTEM-ROOT-BROKER-02: service_stop and journalctl_unit — COMPLETE

**Branch:** `slice/system-root-broker-01` (`98bd87e`)

**What was built:**
- `service_stop` and `journalctl_unit` operations in the root broker
  allowlist and executor, with bounded `lines`, optional signed `since`,
  and the same four allowlisted units.
- `services/broker/client.py` gains a CLI so it can be run as
  `python -m services.broker.client <operation> --service ...`.
- Adversarial tests for non-allowlisted unit, shell-injection via unit
  name, arbitrary journalctl arguments, and line-count bounds.
- Admin panel `POST /admin/api/service/stop` and
  `GET /admin/api/logs/unit/{unit}` routed through `RootBrokerClient`,
  with self-stop detached handling.
- `scripts/hardening/install_root_broker.sh` now merges the allowlist,
  installs a fixed-path `hori-journalctl` wrapper, and emits narrow
  `systemctl`/wrapper sudoers rules for `sudo-rs` compatibility.
- PRD: `.devin/prd/system-root-broker-02.md`.

**Verification:**
- `services/broker/test_broker.py` — 15/15 passed.
- `tests/adversarial/test_root_broker.py` — 10/10 passed.
- `services/aios_core/test_main.py` admin tests — 11/11 passed.
- `make test-unit` — 408 passed, 2 skipped.
- `make test-adversarial` — 135 passed.
- `make test-integration` — 23 passed, 2 failed (pre-existing installed
  `tmpfiles.d` drift; unrelated to this slice).

**Demo:** Live end-to-end successful. Verified `service_status`,
`journalctl_unit`, and `service_stop --service llamacpp` via the broker
CLI; service was restarted immediately after. The broker wrote audit
entries to `/var/log/system-root-broker/audit.jsonl`.

### SLICE-SYSTEM-ROOT-BROKER-01: service restart/status and systemd unit — COMPLETE

**Branch:** `slice/system-root-broker-01` (`791551c`)

**What was built:**
- `system-root-broker` systemd service and socket.
- `service_status` and `service_restart` for the allowlist `aios_core`,
  `aios-sherpa`, `aios-tool-daemon`, `llamacpp`.
- Client library (`services/broker/client.py`) and admin-panel integration.
- Audit log at `/var/log/system-root-broker/audit.jsonl` with correct
  `root:root-broker 0620` permissions.
- Adversarial tests for auth, allowlist, and audit-log correctness.

**Demo:** Start the broker, call `service_restart --service aios_core`;
service restarts, audit entry appended, wrong token and non-allowlisted
service denied. Admin panel `/admin/api/service/restart` now uses the
broker.

**Tests:** `services/broker/test_broker.py` (8),
`tests/adversarial/test_root_broker.py` (6), and
`services/aios_core/test_main.py` (42) pass.

**Full context:** `.devin/handoff-system-root-broker-01.md`.

### SLICE-MACOS-07: The Koi, Menu Bar, Sound, Accessibility Audit (completed 2026-08-14)

**Branch:** `slice/macos-07-personality`

**What was built:**
- MenuBarController — NSStatusItem with fish icon, menu with presence
  status, new conversation, connection settings, sound feedback toggle,
  quit. Menu rebuilds in place after each action so state stays current.
- SoundFeedback — subtle system sounds for message sent (Pop), received
  (Glass), presence change (Tink). Opt-in via feedbackSoundsEnabled.
  Wired into both text mode and voice mode send/receive paths.
- KoiIndicator — small fish in conversation header that reacts to
  presence state (idle: float, thinking: wiggle, hasNudge: glow,
  offline: subtle float). Respects Reduce Motion.
- VoiceSettingsView accessibility — labels, hints, values on all
  controls (voice picker, speed slider, done button, error states).
- MenuBarControllerTests — 8 tests (presence display names, sound
  volumes, no-crash-when-disabled).
- LLM call timeouts bumped to 300s on all three paths (_call_llm,
  _call_llm_with_messages, _stream_llm). Was causing "could not reach
  inference server" when generating long HTML.

**Demo criterion:** HORI icon in the menu bar showing presence state.
Click it → menu with quick actions. Send a message → subtle sound
feedback (if enabled). Koi visible during conversation. All views
pass accessibility audit. ✅ Confirmed — hygge color picker generated
and rendered in canvas, sound feedback working, menu bar functional.

**What surprised us:**
1. Menu bar fish stopped responding after first interaction — the
   NSMenuDelegate approach rebuilt items from a separate NSMenu,
   losing target/action references. Fixed by rebuilding in place
   on the same NSMenu object.
2. No sound in voice mode — the voice send path didn't add the user's
  message to the conversation or play sounds. Added onSent callback
  to VoiceViewModel.
3. "Could not reach inference server" was a server-side timeout (60s/
   120s) on non-streaming LLM calls, not a network issue. Bumped all
   paths to 300s.

**What's unsure:**
- Koi is still an SF Symbol, not a Rive/Lottie animation. The roadmap
  mentions Rive/Lottie but that's a separate design effort.
- Menu bar onNewConversation and onShowConnectionSettings callbacks
  are not wired to ContentView yet (the menu items exist but don't
  do anything yet). Wiring requires a bridge from the app delegate
  to the focused window's state.
- Sound feedback uses system sounds (Pop, Glass, Tink). Custom sounds
  would be nicer but system sounds are zero-dependency.

**Tests:** 64 tests in 18 suites, all passing.

## Completed Slices

### SLICE-MACOS-06: The Emerging Canvas — Sims Builder Mode (completed 2026-08-10)

**Branch:** `slice/macos-06-canvas`

**What was built:**
- CanvasView — full-screen HTML preview with dim/focus states. When
  conversation is focused, canvas is dimmed (45% black overlay).
  Click the dim overlay to shift focus to the canvas.
- CanvasConversationOverlay — conversation floats over the canvas as
  a 440pt panel on the left. Semi-transparent background (95% when
  focused, 70% when dimmed), rounded corners (14pt), shadow, 12pt
  inset from edges. When canvas is focused, conversation dims to 40%
  opacity and scales to 96%. Click dimmed conversation to refocus.
- GuidanceFlyout — COULDN'T/SHOULDN'T flyout that appears when HORI
  indicates a limitation. Client-side detection from response text
  (no safety architecture changes). 12 tests.
- HoriTheme.semanticWarning added (amber #FFC107).
- System prompt updated — _mac_app_capabilities() tells HORI she can
  write HTML in ```html blocks and the Mac app handles rendering/saving.
  Voice guidelines updated to allow ```html blocks (TTS skips them).
  mac_app=True passed from /v1/voice/chat and /v1/voice/chat/stream.
- HoriClient timeout bumped 60s→300s (4096 max_tokens takes longer
  than 60s for full HTML pages).

**Demo criterion:** Ask HORI to build something → HTML renders
full-screen behind the conversation → conversation dims → click on
the canvas to interact with what she built → click back to continue
refining in conversation. ✅ Confirmed working — Scruffy's landing
page rendered full-screen behind floating conversation panel.

**What surprised us:**
1. HORI refused to generate HTML — her system prompt said she
   "cannot create files" and "never use backticks" (voice mode).
   Fixed by adding _mac_app_capabilities() that tells her the Mac app
   renders and saves HTML automatically.
2. HoriClient 60s timeout — with max_tokens at 4096, generating a
   full HTML page takes longer than 60s. Caused "Could not reach HORI"
   errors. Bumped to 300s. Long-term: switch text mode to streaming.
3. The overlay initially looked like a split pane, not a floating
   panel. Fixed with semi-transparent background, rounded corners,
   shadow, and edge insets so it visually "floats" over the canvas.

**What's unsure:**
- Text mode still uses non-streaming HoriClient. Should switch to
  VoiceChatStreamClient for consistency and to avoid timeout issues.
- Guidance flyout detection is client-side heuristic. The full vision
  uses a CapabilityChecker on the server side (deferred).
- The canvas focus model works but needs refinement — the dim/bright
  transitions could be smoother, and the conversation panel could
  be draggable/resizable in a future iteration.

**Tests:** 154 tests pass (142 + 12 GuidanceFlyout).

## Completed Slices

### SLICE-MACOS-05: The Workshop — Projects, Files, "Make It Yours" (completed 2026-08-10)

**Branch:** `slice/macos-05-workshop`

**What was built:**
- ProjectStore — manages projects on disk at ~/HORI/projects/.
  Creates project directories with README.md + hori.log. Handles
  unique slug generation (my-app, my-app-2, etc.), file I/O with
  subdirectory creation, recursive file listing, deletion. 11 tests.
- ProjectSidebar — list of projects, "New Project" sheet (name input
  → create → select), empty state, project rows with folder icon +
  name + description, selection highlights.
- ProjectStructureView — file tree of the current project, sorted by
  path. File icons by extension (html→globe, css→paintbrush,
  js→curlybraces, swift→swift, etc.). Refresh button. Empty state.
- ContentView integration — sidebar toggle button (sidebar.left icon),
  sidebar + structure view in a 280pt panel, HTML auto-saves to
  project directory as index.html on stream completion.
- WKWebView entitlements — added HORI.entitlements with JIT, network
  client, unsigned executable memory, library validation disabled.
  Fixed GPU process crash that was half-rendering HTML pages.
- LLM max_tokens increase — bumped from 500 to 4096 in _llm_payload().
  500 tokens was cutting off HTML generation mid-CSS.

**Demo criterion:** Click "New Project" → name it → sidebar shows it →
ask HORI to build something → files appear in the project's file tree →
project persists across app restarts. ✅ Confirmed working — created
a project, asked HORI for a Scruffy photo album, full HTML rendered
in preview pane and saved to project directory.

**What surprised us:**
1. WKWebView GPU process crash — the WebContent process was being
   killed by sandbox restrictions ("GPUProcess became unresponsive,
   terminating it"). Required adding entitlements for JIT, unsigned
   executable memory, and library validation. The macOS console logs
   are noisy with non-fatal sandbox errors (clipboard, LaunchServices,
   audio) that don't affect rendering.
2. LLM max_tokens was 500 — way too short for HTML generation. HORI's
   responses were getting cut off mid-CSS (`background: #` and then
   nothing). Bumped to 4096.
3. HORI refuses to write files ("I can only read files on your
   machine — I can't create or save new ones for you"). This is the
   safety spine working as designed — she has read-only filesystem
   tools. The Mac app handles saving client-side. Workaround: tell
   her "just write the full HTML in your reply, the app will save it."

**What's unsure:**
- The "I can't write files" refusal is a UX friction point. Long-term,
  HORI should know the app handles file saving. This is a system
  prompt / context issue, not a safety change.
- DM Sans font weight warnings in console — cosmetic, not functional.

**Tests:** 142 tests pass (131 + 11 ProjectStore).

## Completed Slices

### SLICE-MACOS-04: Live Preview — First Taste of Emerging Software (completed 2026-08-09)

**Branch:** `slice/macos-04-live-preview`

**What was built:**
- HTMLExtractor — extracts ```html code blocks from message text.
  Handles streaming (unclosed fences), multiple blocks, case-insensitive
  fence tags. 13 tests.
- HTMLPreviewView — WKWebView wrapper (NSViewRepresentable). Renders
  HTML live, blocks external navigation (security), updates as content
  changes.
- SplitConversationView — HSplitView with conversation | preview pane.
  Preview header with close button.
- ContentView integration — auto-detects HTML in HORI's messages,
  auto-shows preview pane, toggle button in top-right, works with both
  text and voice mode.

**Demo criterion:** Ask HORI "make me a simple landing page" → she
replies with HTML → it renders live in a preview pane next to the
conversation. ✅ Confirmed working — "welcome to your project" with
a Get Started button rendered live.

**What's the vision:** This is the "first taste" — a functional split
view. The elegant version is MAC-6 (Emerging Canvas) where the
conversation floats over the thing being built, not beside it.

**Tests:** 131 tests pass (118 + 13 HTMLExtractor).

## Completed Slices

### SLICE-MACOS-03: Voice Conversation (completed 2026-08-09)

**Branch:** `slice/macos-03-voice`

**What was built:**
- VoiceState state machine (idle/listening/processing/speaking) with TDD
- VoiceChatStreamClient — SSE parser for /v1/voice/chat/stream (text,
  audio, searching, correction, done, error events)
- VoicesClient — GET /v1/audio/voices, sorted by name
- AudioPlayer — queue-based WAV playback, chunks sorted by index
- SpeechRecognizer — SFSpeechRecognizer + AVAudioEngine, on-device STT
  with mono downmix for macOS multi-channel audio
- VoiceInputButton — toggle mic button (click to start, click to stop)
- VoiceSettingsView — voice picker + speed slider, Done button
- VoiceViewModel — coordinates the full voice round-trip
- ContentView integration — voice mode toggle, live partial transcript,
  streaming text into conversation bubbles, voice settings sheet

**Demo criterion:** Click mic button → speak → click again → HORI
replies in text bubble AND speaks the reply aloud through speakers.
✅ Confirmed working — two-way voice conversation, audible, fast.

**What surprised us:**
- PTT (press-and-hold) doesn't work on macOS — DragGesture fires
  press+release almost instantly. Switched to toggle (click to start,
  click to stop).
- SFSpeechRecognizer requires explicit permission request — auth=0
  (notDetermined) silently fails with "No speech detected."
- macOS input node gives 4-channel 44100/96000Hz audio. SFSpeechRecognizer
  doesn't handle multi-channel well. Solution: downmix to mono by copying
  channel 0 (no sample rate conversion needed).
- "Recognition request was canceled" warning when stopping — fixed by
  calling endAudio() instead of cancel() in stopListening().

**What's unsure:**
- SFSpeechRecognizer quality is on-device, not as good as cloud STT.
  May need to revisit for production quality.
- Stale memories in SQLite (Qdrant, Qwen 3.8, VRAM) are being retrieved
  alongside current state. Will resolve once HORI can read docs directly.

**What was skipped:**
- Push-to-talk (replaced with toggle — more reliable on macOS)
- Manual AVAudioConverter (replaced with simple channel 0 copy —
  AVAudioConverter failed with -10877 on deinterleaved 4-channel input)

**Tests:** 118 tests pass (13 VoiceChatStreamClient, 6 VoicesClient,
8 AudioPlayer, plus existing 91).

## Slice Queue

1. SLICE-MACOS-03: Voice Conversation
2. SLICE-MACOS-04: Live Preview — First Taste of Emerging Software
3. SLICE-MACOS-05: The Workshop — Projects, Files, "Make It Yours"
4. SLICE-MACOS-06: The Emerging Canvas — Sims Builder Mode
5. SLICE-MACOS-07: The Koi, Menu Bar, Sound, Accessibility Audit
6. SLICE-MACOS-08: Install-Time Hardware Sensing
7. SLICE-MACOS-09: Phone Companion

## Completed Slices

### SLICE-MACOS-02: HORI Feels Alive — Presence — COMPLETE
- Branch: `slice/macos-02-presence` (merged to rebrand/hori)
- What was built: PresenceClient (SSE parser for /v1/presence stream),
  PresenceIndicator (animated dot + label — green/Available, orange/
  Thinking, violet/Has something to say, red/Offline), koi reactivity
  in EmptyStateView (float when idle, wiggle when thinking, glow when
  hasNudge). Presence stream lifecycle managed by HoriAppDelegate
  (NSApplicationDelegateAdaptor) so it survives window close. 76 tests
  total (75 + 1 @Observable tracking test).
- Surprises:
  1. `hasNudge` raw value didn't match server wire format — server
     sends `has_nudge` (snake_case), enum needed `case hasNudge =
     "has_nudge"`.
  2. Presence indicator falsely showed "Offline" when chat worked —
     SSE stream lifecycle was tied to ContentView (destroyed on window
     close). Moved to HoriAppDelegate via NSApplicationDelegateAdaptor.
  3. `URLSession.shared` doesn't support custom delegates —
     PresenceClient needed its own URLSession instance.
  4. MessageInputView pushed off-screen by ZStack layout regression —
     added `.frame(maxWidth: .infinity, maxHeight: .infinity)` to the
     VStack inside the ZStack.
  5. **@Observable not tracking aiosCoreURL changes** (found at demo):
     `aiosCoreURL` and `feedbackSoundsEnabled` were computed properties
     backed by UserDefaults. @Observable only tracks stored properties,
     so views depending on `isConnectionConfigured` never re-rendered
     when the URL was set from ConnectionSetupView. Product owner had
     to restart the app to get the chat input bar to appear after
     entering the server URL. Fix: made both stored properties with
     didSet syncing to UserDefaults. Added
     `isConnectionConfiguredIsObservable` test using
     withObservationTracking.
  6. **Koi disappears when chat starts** (found at demo, by design —
     not a bug): EmptyStateView (with koi) is replaced by
     ConversationView (no koi) via if/else in ContentView. The koi
     reactivity can only be observed before chatting. Product owner
     said the koi isn't the star — going for minimalist canvas. Koi
     integration deferred to SLICE-MACOS-07 (The Koi, Menu Bar, Sound).
  7. `test_installed_service_matches_repo` integration test was a
     false positive — compared templated repo service file against
     de-templated installed file. Fixed by adding
     `_expand_service_templates()` mirroring the install script's sed
     substitution.
- Skipped: Koi persistent during conversation (deferred to
  SLICE-MACOS-07). Connection settings accessible after first-run
  (needs a menu item — deferred).
- Demo: Product owner verified chat input bar appears immediately after
  entering server URL (no restart needed — the @Observable fix). 76
  tests pass on Mac.

### SLICE-MACOS-01: Native Text Conversation — COMPLETE
- Branch: `slice/macos-01-text-conversation` (merged to rebrand/hori pending)
- What was built: HoriClient (HTTP client for POST /v1/voice/chat —
  text only, audio ignored until Phase 3), ConversationView (scrollable
  message bubbles — user right/accent, HORI left/surface, spring
  animations, typing indicator), MessageInputView (multi-line input +
  send button bound to Cmd+Return), ConnectionSetupView (first-run
  sheet for aios-core URL with "Test Connection"), ContentView wired
  with send logic (user message → HORI reply → error banner on
  failure). 54 tests total (8 HoriClient + 12 ConversationView + 34
  existing).
- Surprises:
  1. `TestResult.success` collided with SwiftUI's `SensoryFeedback.success`
     (macOS 15) — fixed by making TestResult Equatable.
  2. `localizedDescription` returns String (non-optional) — optional
     chaining on it was invalid. Removed `?`.
  3. MockURLProtocol: URLProtocol instantiates a new instance per
     request, so instance properties can't be shared with the test.
     Moved all config to static vars. Also URLProtocol strips httpBody
     from POST requests — read it from httpBodyStream in startLoading().
  4. Swift Testing runs tests in parallel by default — shared static
     mock state leaked between tests. Added `.serialized` to the suite.
  5. EmptyStateView had `.frame(maxHeight: .infinity)` which greedily
     consumed all vertical space, pushing the input field off-screen.
     Removed `maxHeight: .infinity`.
  6. aios-core serves HTTP only (Tailscale Serve proxies HTTPS). The
     app uses HTTP over Tailscale — ATS is disabled via
     NSAllowsArbitraryLoads in Info.plist.
- Infrastructure: Set up SSH from GPU server to Mac via Tailscale
  (standard SSH key, not Tailscale SSH — the Mac GUI app doesn't
  support Tailscale SSH server). Devin can now build and test on the
  Mac directly via SSH over the Tailscale IP, eliminating the
  push/pull/paste loop. Visual demo still requires the product owner.
- Skipped: Streaming (uses /v1/voice/chat, not /stream — deferred).
  Audio (Phase 3). Connection settings accessible after first-run
  (needs a menu item or button — deferred to Phase 2+).
- Demo: Mac app → connection setup (enter aios-core Tailscale URL) →
  type "hello" + Cmd+Return → HORI replies in a bubble. Follow-up turn
  has context. 54 tests pass.

### SLICE-MACOS-00: First Impression + Foundation — COMPLETE
- Branch: `slice/macos-00-first-impression` (merged to rebrand/hori pending)
- What was built: The HORI macOS app skeleton — xcodegen project,
  design system (theme, typography, animations, shapes), five
  foundational decisions (accessibility, keyboard, localization,
  multi-window, undo), and the empty state view (koi + "What do you
  want to make today?"). 5 Swift test files (Theme, EmptyStateView,
  Accessibility, WindowState, UndoManager).
- Surprises: Six compile/test issues surfaced on first Mac build — all
  were API mismatches between the SDK the source was written against and
  macOS 15 Sequoia / Xcode 16:
  1. `Font.custom` has no overload taking both `weight:` and `relativeTo:`
     → chain `.weight()` as a modifier (app + test)
  2. `registerUndo(withTarget:handler:)` requires a class, not `Any`
     → shared no-op class instance as target
  3. `.toolbarVisibility` not a Scene modifier in this SDK → removed
     (`.windowStyle(.plain)` already gives transparent titlebar)
  4. `NSColorSpace.sRGBColorSpace` renamed to `.sRGB` → updated
  5. `UndoManager.groupsByEvent` defaults to true, causing all explicit
     undo groups to nest inside one event group → set to false so each
     register() is a top-level group (fixes chained undo)
  6. `.windowStyle(.plain)` dropped the traffic lights entirely on
     Sequoia (no close/minimize/zoom, no draggable titlebar) → removed,
     standard chrome restored. Transparent titlebar deferred to a later
     cosmetic refinement.
- Skipped: Transparent titlebar (needs AppKit window customization, not
  just .windowStyle(.plain) — deferred). DM Sans custom font not bundled
  yet (SF Pro fallback works).
- Demo: Mac build → 5 test files pass → Cmd+R → warm near-black window
  with koi + "What do you want to make today?", violet accent, standard
  window chrome, multi-window works (Cmd+N), VoiceOver navigable.
- Pre-push hook fix: Check 3 now uses --diff-filter=AM so deletions of
  sensitive files aren't flagged as additions (was blocking the push).

### SLICE-08: hori init setup wizard — COMPLETE
- Branch: slice/08-hori-init
- What was built: `hori/init.py` (setup wizard), `hori/test_init.py` (9 tests).
  `hori init` combines hardware detection + config creation: detects GPU/VRAM,
  recommends a model tier, writes `~/.config/hori/hori.yaml` with the recommended
  model and SQLite backend, creates `~/.local/share/hori/`, prints next steps.
  `--force` overwrites existing config, `--quiet` suppresses the report.
  Updated cli.py, README quickstart, CONTRIBUTING.md.
- Surprises: None. The detect module was already clean, so init was just
  orchestration + config writing + next-steps printing.
- Skipped: Nothing.
- Demo: Fresh clone → `pip install -e .` → `hori init` → working config
  with recommended model, data dir created, next steps printed.

### SLICE-07: README + LICENSE + CONTRIBUTING — COMPLETE
- Branch: slice/07-readme-license
- What was built: Root `README.md` (project overview, quickstart,
  architecture diagram, project structure, testing commands),
  `LICENSE` (Apache-2.0), `CONTRIBUTING.md` (slice workflow, build/test,
  code style, safety-first testing, crash recovery). Updated
  `docs/README.md` to point at the root README.
- Surprises: None. Straightforward documentation slice.
- Skipped: Nothing.
- Demo: A stranger can clone the repo, read the README, and understand
  what HORI is, how to install it, and how to run it.

### SLICE-06: SQLite memory backend — COMPLETE
- Branch: slice/06-sqlite-memory
- What was built: `hori/sqlite_memory.py` (SQLite backend with cosine similarity),
  `hori/test_sqlite_memory.py` (24 tests). Refactored `services/aios_core/memory.py`
  to dispatch to either Qdrant or SQLite backend based on `memory.backend` config.
  Updated `intent_graph.py` to use `scroll_all()` instead of direct Qdrant client.
  Updated `_retrieve_memory_batch` in main.py to use the backend-agnostic API.
  Added `memory.backend` to `hori/config.py` and `config.reference.yaml`.
- Surprises: The memory.py refactor was straightforward — the interface was already
  clean. The main.py `_retrieve_memory_batch` was directly importing qdrant_client,
  which needed to be refactored to use the public `retrieve_memory` API instead.
  The intent_graph.py `build_from_qdrant` had nested loop indentation that needed
  careful untangling when switching from scroll() to scroll_all().
- Skipped: `memory_consolidation.py` still uses Qdrant directly — it's a standalone
  script, not part of the hot path. Will be updated when consolidation is refactored.
- Demo: Set `memory.backend: sqlite` in hori.yaml, chat with HORI — memories stored
  and retrieved from `~/.local/share/hori/memory.db` with no Qdrant running.

### SLICE-05: hori detect — COMPLETE
- Branch: slice/05-hori-detect
- What was built: `hori/detect.py` (hardware detection + model tier recommendations),
  `hori/cli.py` (CLI entry point), `hori/test_detect.py` (25 tests).
  Detects AMD (ROCm via rocm-smi or DRM sysfs fallback), NVIDIA (nvidia-smi),
  Apple Silicon (sysctl), and CPU-only. Recommends one of 5 model tiers
  (heavy/medium/light/micro/nano) based on VRAM, outputs a hori.yaml snippet.
- Surprises: rocm-smi's `--showproductname` doesn't include VRAM — needed a
  separate `--showmeminfo vram` call. The kfd sysfs path has no properties
  on this kernel, but DRM sysfs (`/sys/class/drm/cardN/device/mem_info_vram_total`)
  works perfectly. The GPU name key in rocm-smi JSON is "Card Series" (capital S).
- Skipped: Nothing.
- Demo: `python -m hori.detect` → detects Radeon AI PRO R9700 (31.9GB VRAM),
  recommends heavy tier (Qwen3.6-27B).

### Pre-squash: De-personalization (SLICE-01 through SLICE-04)
- History was squashed to a single initial commit to remove personal data
  (conversation logs, IPs, hostnames, paths) from git history.
- All code, tests, scripts, and docs now use configurable paths via hori.yaml
  or auto-detect from the environment (Path.home(), script location, SUDO_USER).
- 290 unit tests, 121 adversarial tests, 141 integration tests pass.
- 2 integration failures are expected deployment drift (installed service files
  need reinstall after the path renames).

### [SLICE-KB-03] Open-WebUI qwen-128k/qwen-long project-discovery fix (completed 2026-08-23)

**Problem:** `qwen-128k` was missing from the Open-WebUI `model` table, so it fell back to generic defaults and denied local filesystem access. `qwen-long` had a workspace record but invoked `query_knowledge_files` across all attached knowledge collections for a broad project-survey question, producing a >100 KB tool result that triggered `LineTooLong` in the Open-WebUI tool-call continuation stream.

**What was built:**
- Created Open-WebUI workspace records for `qwen-128k` and `qwen-256k-batch`, cloned from `qwen-long` with the right `num_ctx`/`context_length`, `function_calling: native`, local tool injection, and knowledge attachments.
- Updated `services/open-webui/fix_tooling.py`, `set_context_lengths.py`, and `make_models_workspace.py` to include the new aliases and the revised system prompts.
- Added a shared `Tool-use rules` paragraph to all Qwen/Devstral prompts: `kb_exec` is disabled, prefer `run_safe_command`/`read_file` for project/directory surveys, and only use `query_knowledge_files` for conceptual/historical questions.
- Reset `rag.top_k` and `rag.top_k_reranker` to 5 in the live DB to bound knowledge retrieval output.
- Documented the alias rationale and the fix in `the global AGENTS.md (outside the repo)` (global project memory) and the slice log.

**Demo:** `curl` to Open-WebUI `qwen-128k` and `qwen-long` with the project-survey question → both models now emit a `run_safe_command` call (e.g. `ls -la <user-home>/Projects/`) instead of a broad `query_knowledge_files` call.

**Branch:** `slice/macos-project-context` (current working branch). `services/open-webui/` helper scripts and global `AGENTS.md` updated.

## Notes

- Branch: `rebrand/hori` (integration), slice branches to be created as `slice/NN-description`
- The soak period for the safety spine is considered complete (Tier 2+3 stable,
  92 adversarial tests, reboot survival fixed). The redistribution work is the
  new focus. The soak will be re-run on the redistributed codebase before HORI 2.0.
