# HORI Uncertainty Register

> Things Devin is not sure about. Not bugs, not TODOs — genuine unknowns.
> The product owner can read this anytime and weigh in on anything.

## Open uncertainties

### Config system complexity
- **Question:** Should `hori.yaml` be a single flat file, or should it support
  includes/profiles (e.g., `hori.yaml` + `hori.local.yaml`)?
- **Why it matters:** Flat is simpler. Includes are more flexible for users
  who want a base config + per-machine overrides.
- **Current leaning:** Flat for now. Add includes only if a user actually
  needs them. YAGNI.

### SQLite memory backend — query patterns
- **Question:** Can SQLite handle the memory retrieval patterns (semantic
  search) that Qdrant handles, or do we need to fall back to keyword search?
- **Why it matters:** If SQLite can only do keyword search, the "minimal
  mode" experience is significantly worse than "full mode."
- **Current leaning:** SQLite gets keyword search + simple recency ranking.
  Semantic search (vector similarity) requires Qdrant or an in-process
  vector store (sqlite-vss, but that's a dependency). Document the tradeoff
  honestly: "minimal mode = keyword search, full mode = semantic search."

### Docker safety spine equivalence
- **Question:** Is the Landlock + seccomp safety spine inside a Docker
  container equivalent to bare metal, or are there gaps?
- **Why it matters:** If Docker is the "try it in 5 minutes" path, the
  safety spine needs to work there too.
- **Current leaning:** Landlock and seccomp are kernel features — they
  should work inside a container as long as the container has the right
  capabilities. Need to verify, not assume.

### Setup wizard: CLI vs web
- **Question:** Should the first-run setup wizard be a CLI conversation
  (terminal) or a web page (browser)?
- **Why it matters:** CLI is simpler to build, works over SSH, and fits
  the "converse first" ethos. Web is more visual and approachable for
  non-technical users.
- **Current leaning:** CLI first. The "converse first" flow is a
  conversation — a terminal is a natural place for a conversation. Web
  onboarding can come later as part of the experience vision.

### Model download: auto vs ask
- **Question:** Should `hori setup` auto-download a model, or ask the user
  first?
- **Why it matters:** Models are 2-20GB. Auto-downloading without asking
  is hostile. Asking is polite but adds friction.
- **Current leaning:** Ask. Show the recommended model for their hardware,
  show the size, ask for confirmation. "I'd like to download
  Qwen3-8B (4.2GB). Is that okay?"

### macOS/Windows degraded mode — how to document
- **Question:** How do we communicate "you get user-space validation only,
  not kernel-enforced safety" without scaring people away or being
  misleading?
- **Why it matters:** The safety spine is the differentiator. On
  macOS/Windows, it's weaker. We need to be honest without underselling.
- **Current leaning:** A clear table in the README. "Linux/WSL2: full
  spine. macOS/Windows: user-space validation only. The Sherpa still
  works (it's Go, cross-platform). Kernel isolation does not."

### GUI surfaces — the GPU server is headless, not the daily driver
- **Context:** The GPU server (Linux) is headless — the GUI was removed
  to free VRAM. Users interact with HORI from their phone or laptop over
  the network. The GPU server is where the LLM lives, not where the user
  interacts.
- **Why it matters for design:** The "converse first" experience, the
  themes, the voice interaction — these need to be tested on the user's
  actual devices (phone, laptop), not on a Linux desktop. The GPU server
  is the backend.
- **Why it matters for the audience:** Most users are on macOS, Windows,
  or iOS/Android. HORI's GUI surfaces (voice app, chat app, admin panel)
  are web-based and accessed from the user's device over the network
  (Tailscale or LAN). The LLM can live anywhere — a headless Linux box,
  a cloud endpoint, or even locally on a laptop if it has enough VRAM.
- **Current leaning:** Design and test the GUI surfaces on mobile + desktop.
  The GPU server is the reference backend (where the safety spine runs
  at full strength). Document the architecture as: "your GPU box runs
  HORI's backend; your phone and laptop are the frontends." This is
  actually the natural deployment model for most users — a headless
  machine (or cloud) runs the model, the user interacts from their
  daily device.

## Resolved uncertainties

(none yet)
