# HORI

> **HORI** (彫り) — *the carving*. A local-first agent runtime with a safety spine.

HORI is a runtime that carves precise, permanent marks — audit trails,
memory, intent — while keeping the LLM untrusted and the architecture
trusted. It runs on your hardware, your network, your terms.

## Why HORI?

Most AI assistants are cloud-dependent, trust the LLM blindly, and have
no memory beyond a context window. HORI is different:

- **Local-first** — runs on your own GPU (AMD ROCm, NVIDIA CUDA, Apple
  Silicon, or CPU). No data leaves your machine unless you choose a cloud
  LLM provider.
- **Safety spine** — the LLM is untrusted. A 5-layer defense (kernel
  sandbox, tool registry, validation, guard rails, behavioral guardian)
  ensures the LLM can propose but never execute without architecture
  enforcement.
- **Persistent memory** — semantic recall across conversations via
  Qdrant or SQLite (zero external deps).
- **Voice + text surfaces** — streaming voice chat, keyboard-first text,
  ambient presence. Mobile-first via Tailscale HTTPS.

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
  model: "Qwen3.6-27B"          # or whatever your endpoint serves

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
│  Tool-augmented chat · Safety events             │
└──────┬───────────────┬──────────────────────────┘
       │               │
┌──────▼──────┐  ┌─────▼──────────────────────────┐
│  LLM (8080) │  │  Tool Daemon (Unix socket)      │
│  llama.cpp  │  │  Landlock + seccomp + registry  │
│  / Ollama   │  │  4 read-only tools, fail-closed │
│  / Cloud    │  └─────┬──────────────────────────┘
└─────────────┘        │
                  ┌────▼─────┐
                  │  Sherpa  │  Go behavioral guardian
                  │  (Go)    │  Fail-closed if it dies
                  └──────────┘
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

## Project structure

```
hori/                   # HORI Python package (config, detect, SQLite memory)
services/
  aios_core/            # The intelligence layer (FastAPI, chat, voice, memory)
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
make test-unit         # unit tests (339 tests, ~30s)
make test-adversarial  # safety property tests
make test-integration  # cross-service contract tests
make test-stress       # smoke stress tests (~120s)
```

## Documentation

- **[docs/manifesto.md](docs/manifesto.md)** — mission, 7 pillars, core values
- **[docs/stack.md](docs/stack.md)** — hardware, software, model config
- **[docs/operations.md](docs/operations.md)** — runtime details, build commands
- **[docs/roadmap.md](docs/roadmap.md)** — 6 tiers, 156 PoCs, status tracker
- **[docs/tool_safety.md](docs/tool_safety.md)** — safety architecture
- **[docs/SLICE_LOG.md](docs/SLICE_LOG.md)** — current work status

See [docs/README.md](docs/README.md) for the full documentation index.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
