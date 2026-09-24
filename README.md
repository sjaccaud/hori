# HORI

> **HORI** (彫り) — *the carving*. A local-first agent runtime with a safety spine.

HORI carves precise, permanent marks — audit trails, memory, intent —
while keeping the LLM untrusted and the architecture trusted. It runs on
your hardware, your network, your terms, and nothing leaves the machine
unless you say so.

## Why HORI?

Most AI assistants forget you exist the moment the tab closes. They trust
the model blindly, rent you a context window, and call it memory. HORI is
built the other way around:

- **Local-first** — runs on your own GPU (AMD ROCm, NVIDIA CUDA, Apple
  Silicon, or CPU). No data leaves your machine unless you choose a cloud
  LLM provider.
- **Safety spine** — the LLM is untrusted. A 5-layer defense (kernel
  sandbox, tool registry, validation, guard rails, behavioral guardian)
  means the model can propose but never execute without architecture
  enforcement.
- **Memory that survives the session** — workstreams keep their own state:
  a `state.md` per thread for what's true right now, dated episodes for how
  it got there, and the whole store is queryable over MCP. Sessions open
  with your active workstreams injected and close with a journal entry.
  The context window stops being the ceiling.
- **A second brain that knows when to abstain** — a `/decide` service runs
  typed questions on a 3B sidecar and returns probabilities over your
  options. When it doesn't know, it says so — `insufficient_context` and
  `no_answer` are first-class results, not errors, and it'll fail closed
  with a 503 before it fabricates an answer.
- **Voice + text surfaces** — streaming voice chat, keyboard-first text,
  ambient presence. Mobile-first via Tailscale HTTPS.

## This week the runtime grew a memory and a conscience

> "The stack is really starting to be the well honed machine."

Take the last seven days of commits as one example of the pace. The
workstream memory shipped end to end — a thread/episode store, four MCP
tools (`thread_list`, `thread_read`, `episode_append`, `episode_search`),
session hooks that inject the index at start and journal at end, and a
nightly consolidation timer that folds stale threads and mines the review
queue for reusable rules.

Sherpa, the Go behavioral guardian, found and fixed its own blind spot:
for five weeks it'd been reading a frozen audit log while the daemon wrote
a live one at a different path. Path agreement, entry-time attribution,
and L2 auto-recovery are repaired, and the health check now asserts the
audit pipe stays live — the guardian now watches its own eyesight.

Then there's `/decide`. A typed-decision service on the nano sidecar took
its first real job this week: shadowing idea-factory triage. Every item
the big model scores, the 3B answers the same five questions and logs the
pairs — 80 question-pairs on the first live run, ~200 a day after that.
The early data is already honest about where the little model's ceiling
sits: it gates hard-no and verdict questions well (92% agreement where it
commits), won't judge money potential at all, and abstains instead of
guessing. That's the point of shadow mode — the small model doesn't get
promoted on vibes, it earns it on agreement stats.

And the stack underneath it grew a new trick: fine-tuning now runs on the
same GPU that serves inference. A voice adapter went from corpus to LoRA
to a served GGUF in one evening — the machine that runs the models now
makes them too.

## Quickstart

### 1. Install

```bash
git clone https://github.com/your-username/hori.git
cd hori
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 2. Initialize

```bash
hori init
```

This detects your hardware, recommends a model tier, and creates
`~/.config/hori/hori.yaml` with the recommended settings. It also
creates the data directory at `~/.local/share/hori/`.

### 3. Review config (optional)

```bash
cat ~/.config/hori/hori.yaml
```

The defaults work out of the box. Key settings you might want to change:

```yaml
llm:
  api_url: "http://localhost:8080/v1/chat/completions"
  model: "qwen-fast"            # or whatever your endpoint serves

memory:
  backend: "sqlite"              # "sqlite" (no external deps) or "qdrant"
```

### 4. Run

```bash
# Start the core service (port 5680)
PYTHONPATH=. python -m uvicorn services.aios_core.main:app --host 0.0.0.0 --port 5680

# Or via systemd (see services/systemd/ for service files)
```

Then open `http://localhost:5680/chat` in your browser.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   User Surfaces                  │
│  Voice (SSE)  ·  Chat (text)  ·  Admin (web)    │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│              aios-core (port 5680)               │
│  FastAPI · Elastic context · Memory retrieval    │
│  Tool-augmented chat · Safety events · MCP /SSE  │
└──────┬───────────────┬──────────────────────────┘
       │               │
┌──────▼──────┐  ┌─────▼──────────────────────────┐
│  LLM (8080) │  │  Tool Daemon (Unix socket)      │
│  llama.cpp  │  │  Landlock + seccomp + registry  │
│  / Ollama   │  │  4 read-only tools, fail-closed │
│  / Cloud    │  └─────┬──────────────────────────┘
└──────┬───────┘        │
       │            ┌───▼────────┐
       │            │  Sherpa    │  Go behavioral guardian
       │            │  (Go)      │  Fail-closed if it dies
       │            └────────────┘
       │
┌──────▼─────────────────────────────────────────┐
│  Sidecars & stores                              │
│  nano 3B (:8090) · /decide (:8773) · threads/   │
│  episodes + MCP tools · Qdrant or SQLite memory │
└─────────────────────────────────────────────────┘
```

### Safety spine (5 layers)

1. **Kernel** — Landlock + seccomp isolate the tool daemon (filesystem
   allowlist, no network, limited syscalls)
2. **Registry** — only 4 read-only tools exist (list_dir, read_file,
   count_files, search_files). No dynamic registration.
3. **Validation** — path traversal, symlink escape, prefix matching
4. **Guard rails** — rate limiting, emergency stop, response verification
5. **Sherpa** — Go behavioral guardian. If Sherpa dies, the system
   fails closed (no tool calls allowed).

**The fundamental principle:** The LLM is untrusted. The architecture is
trusted. The LLM proposes; the architecture executes; the user confirms.

### Memory

Two backends, selected via `memory.backend` in `hori.yaml`:

- **Qdrant** (default) — production-grade vector database with HNSW index.
  Best for large datasets (10K+ memories).
- **SQLite** — zero external dependencies. Embeddings stored as JSON,
  cosine similarity computed in Python. Best for getting started and
  small-to-medium datasets.

Both backends expose the same interface: `store_memory`,
`retrieve_memory`, `retrieve_conversation_turns`.

On top of that sits the workstream layer: per-thread `state.md` files and
dated episode logs under `~/.local/share/hori/threads/`, exposed to agents
as MCP tools (`thread_list`, `thread_read`, `episode_append`,
`episode_search`). A nightly consolidation job folds stale threads and
mines recurring rules for review.

## Project structure

```
hori/                   # HORI Python package (config, detect, SQLite memory)
services/
  aios_core/            # The intelligence layer (FastAPI, chat, voice, memory)
  aios_decide/          # Typed-decision service on the nano sidecar (:8773)
  tool_daemon/          # Sandboxed tool execution (Landlock, seccomp, registry)
  sherpa/               # Go behavioral guardian
  proactive_agent/      # Landscape survey + opportunity proposer
  red_teaming/          # Adversarial test engine
  telemetry/            # Alignment engine + monitoring
  recovery/             # Watchdog
  systemd/              # Service files
  integration_tests/    # Cross-service contract tests
scripts/
  hardening/            # Install + audit scripts
  memory_consolidation.py
  ingest_*.py           # RAG ingestion scripts
tests/
  adversarial/          # Safety property tests (TDD: written first, must fail)
  regression/           # Regression tests
  stress/               # Entropy + safety stress tests
docs/                   # All documentation (see docs/README.md)
```

## Testing

```bash
make test              # all tests
make test-unit         # unit tests (510 tests, ~30s)
make test-adversarial  # safety property tests (163 tests — written first, must fail)
make test-integration  # cross-service contract tests
make test-stress       # smoke stress tests (~120s)
```

## Documentation

- **[docs/manifesto.md](docs/manifesto.md)** — mission, 7 pillars, core values
- **[docs/operations.md](docs/operations.md)** — hardware, build/test, inference, endpoints
- **[docs/roadmap.md](docs/roadmap.md)** — development roadmap (Tiers 1-7)
- **[docs/safety.md](docs/safety.md)** — safety principles, architecture, adversarial analysis
- **[docs/apple_shortcut_setup.md](docs/apple_shortcut_setup.md)** — iOS voice shortcut setup

See [docs/README.md](docs/README.md) for the full documentation index.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
