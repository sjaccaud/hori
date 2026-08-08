# The HORI Manifesto

## The Carver and the Koi

**HORI** (彫り) — Japanese for *the carving*. The craft of the horishi (彫師),
the master carver who makes precise, permanent marks with discipline and intent.

The mascot is the **koi** (鯉) — the most enduring motif in Japanese tattoo
art. The koi swims upstream against the current. At the Dragon Gate
waterfall, the koi that perseveres and makes the leap transforms into a
dragon.

Together they tell the story of this system:

- The **carver** is HORI itself — the runtime that makes precise, permanent
  marks: audit trails, memory, intent. It works with discipline and craft.
  It respects the medium. Its work is lasting.
- The **koi** is the builder — swimming upstream against entropy,
  hallucination, and non-determinism. Persisting through difficulty.
  Earning transformation: read-only becomes side-effect becomes destructive,
  gated at each leap. The koi that makes the climb becomes something more
  than it was.

The carver makes the marks. The koi makes the leap. The system is the craft;
the builder is the transformation.

---

## Mission Statement

To build a local-first agent runtime with a safety spine that helps latent
builders solve problems at the speed of intent, not the speed of AI.

HORI is not an "AI Operating System." That term is overloaded — it means six
different things in the industry and invites comparisons to traditional
OSes that this project does not claim to be. HORI is narrower and more
honest: a runtime that carves precise, permanent marks while keeping the
LLM untrusted and the architecture trusted.

**Who HORI is for:** Latent builders — people with the potential to build
things who need a system that helps them realize it. Not "enterprise
customers." Not "software engineers." Latent builders. The ones who have
ideas but don't want to learn prompt engineering just to make the machine
spit out magic. The ones who want to *converse first, build second*.

**The thesis:** Local models are becoming more open, more powerful, and
easier to integrate on reasonable hardware every month. They are an
increasingly valuable private alternative to frontier paid-trap ecosystems
that meter your thinking, harvest your data, and disappear when the
business model shifts. HORI is built on the belief that the best AI system
is the one you own, the one that stays, the one that knows you across
years — not the one that bills you per thought.

---

## Converse First, Build Second

Most AI tools are built on a single assumption: *one-shot do it now.* You
type a prompt, the machine spits out an answer, you paste it somewhere.
This forces people to learn the system — to engineer their prompts so the
AI produces magic on the first try. It is a tool designed by software
engineers, for software engineers.

HORI rejects this. The natural workflow is:

1. **Converse** — talk through the idea, the constraints, the non-goals.
   HORI asks questions. HORI pushes back. HORI refuses to code until a
   shared design concept is reached. (This is the `/grill-me` skill: an
   adversarial design interview that enforces intentional friction.)
2. **Charter** — the conversation distills into a project charter: the
   law that governs the work. Technical DNA, success criteria, constraints.
3. **Build** — only then does HORI execute, in strict red/green/refactor
   loops. Tests first. Safety properties first. The build is disciplined,
   not improvised.

This maps to the intent hierarchy that already exists in the system:
Manifesto (anchor) → Charter (law) → Work Order (current). The converse-
first flow is not new scope — it is the positioning of what the system
already does.

---

## The Seven Pillars of HORI

### I. Local-First Intelligence (The Hearth)
*   **Privacy by Default:** Core reasoning and sensitive data processing
    occur on local hardware (the "<your-hostname>" rig). The warmth is inside; the
    frost is outside.
*   **Tiered Reasoning:** A hybrid approach utilizing high-speed local
    models (llama.cpp/ROCm) for immediate tasks and frontier cloud models
    (Gemini Pro) for deep, complex strategy.
*   **Compute Optimization:** Maximizing local VRAM and hardware
    capabilities to provide responsive, intelligent interaction.

### II. Distributed Mesh Orchestration (The Nervous System)
*   **Unified Identity:** A single, cohesive system operating across a
    Tailscale-meshed network of diverse devices (Linux, macOS, Raspberry
    Pi, iOS).
*   **Resource Fluidity:** The ability to distribute workloads across the
    mesh, treating the entire network as a single, scalable compute pool.
*   **Low-Latency Connectivity:** Prioritizing secure, fast communication
    to ensure real-time responsiveness across the mesh.

### III. Persistent Context & Memory (The Carved Record)
*   **Zero-Entropy Preservation:** Implementing advanced data management to
    prevent context drift and ensure long-term knowledge retention. The
    carver's marks are permanent.
*   **Massive RAG Integration:** Leveraging vast storage (8TB NAS) to
    create a deep, searchable, and contextually aware "Life-Long Memory."
*   **Continuous Learning:** An adaptive system that evolves alongside the
    user, learning preferences, workflows, and project histories.

### IV. Seamless Voice & Remote Interaction (The Senses)
*   **Intuitive Voice Control:** An "always-on" listening capability (via
    edge devices) that enables natural, hands-free interaction with the
    system.
*   **Ubiquitous Access:** Remote control and monitoring from any device,
    anywhere, via secure mesh networking.
*   **Multimodal Input/Output:** Integrating voice, text, and code to
    provide a rich, natural interface for human-AI collaboration.

### V. Self-Healing & Adaptive Autonomy (The Koi's Persistence)
*   **Proactive Monitoring:** Continuous observation of system health
    (compute, network, storage) to detect and mitigate issues before they
    escalate.
*   **Automated Recovery:** Implementing self-healing protocols to maintain
    system stability and uptime. The koi persists through the current.
*   **Autonomous Workflow Execution:** Utilizing low-code orchestration to
    automate complex, multi-step tasks and optimize system performance.

### VI. Low Entropy & Intent Integrity (The Protocol)
*   **Hierarchical Intent:** Managing truth across three temporal layers:
    The Manifesto (Anchor), The Project Charter (Law), and The Work Order
    (Current).
*   **Agentic Red-Teaming:** Utilizing specialized personas (Strategist,
    Architect, Guardian) to challenge deviations and protect long-term
    goals.
*   **Intentional Friction:** Implementing "Yes, AND" mechanisms to ensure
    significant shifts in direction are deliberate and documented.

### VII. Engineering Discipline (The Craft)
*   **Test-Driven Development:** For every safety property, the test that
    attempts to break it is written **before** the implementation. The test
    must fail (because the safety doesn't exist yet), then the safety is
    built until the test passes. This is the opposite of "write tests for
    what the code does" — it is "write tests for what the code must NOT
    do." Adversarial tests are first-class citizens, not afterthoughts.
    Every safety PoC ships with its adversarial test already passing.
*   **Documentation as Architecture:** In-code documentation is not
    optional commentary — it is part of the architecture. Every module,
    function, and safety mechanism must include docstrings that explain
    *why* it exists, *what* it defends against, and *which source-of-truth
    document it traces to. A developer handed this codebase cold should be
    able to understand the safety architecture from the code alone, with
    the docs as the authoritative reference. When the code and docs
    diverge, that is a bug — fix one or the other, immediately.
*   **Simplicity as Security:** More rules create more rulebreakers. More
    layers create more interfaces, more fallback paths, more
    misconfiguration opportunities. Each safety layer must justify itself
    against the complexity it adds. The irreducible minimum is the spine;
    everything else is defense-in-depth that must earn its place. When in
    doubt, build less and let reality tell you what you need.

---

## Core Values
*   **Craft:** The carver's discipline — precision, permanence, respect for
    the medium. TDD for safety properties. Documentation that stays in sync
    with code.
*   **Privacy:** Local-first. Encrypted communication. Your data, your
    hardware, your model. The private alternative to frontier paid-trap
    ecosystems.
*   **Joy:** Building should feel good. The system invites exploration,
    iteration, and creativity — not the anxious prompt-engineering of
    one-shot tools.
*   **Honor:** The system does what it says. The LLM is untrusted; the
    architecture is trusted. The Sherpa watches. The audit trail is
    permanent. When the system says it did something, it did it.
*   **Simplicity:** Maximizing existing tools and minimizing unnecessary
    complexity. The irreducible minimum is the spine.

---

## The Experience Vision (North Star)

This section captures the design direction for HORI's user experience. It
is a north star, not a current deliverable — the engineering foundation
comes first, the experience layers on top once the safety spine is proven.

**Design language:** Clean, minimal, not abrasive. Not an "AI tech bro
demo." More like a video game in usability and design language than
enterprise software. Approachable and pleasant to create in, but powerful
underneath. Screams 2027+, not 2022.

**Baked themes:** Three aesthetic environments, each tuned to remind the
builder of a different strength:

- **Koi (Default)** — perseverance, transformation, the climb. Deep black,
  crimson, gold, indigo. The primary building theme. The koi in mid-leap
  at the waterfall.
- **Guardian** — the Sherpa, the safety spine, the watcher. Muted, steady,
  protective. The theme for work that demands vigilance.
- **Blossom (Cherry)** — sakura (桜), impermanence, beauty. Soft, warm,
  contemplative. The theme for exploration and iteration. The most zen
  mode.

**Zen and Immersive modes:** Zen reduces the surface to the essential —
one conversation, no chrome. Immersive extends the theme into the voice
and speech features — the system's voice matches the aesthetic, the
interaction feels like being inside a place, not using a tool.

**Harmonic, interchangeable colors:** The palette is not seen much in
other software. The colors are harmonic across themes — a builder can
switch from Koi to Blossom without jarring transitions. The system feels
like a designed environment, not a settings panel.

**Voice matched to theme:** The voice features — wake word, response
voice, ambient sounds — are designed to match the active theme. The koi
theme has a voice with weight and determination. The blossom theme has a
voice with warmth and light. The guardian theme has a voice with
steadiness and clarity.

---

## A Word About Models - A Local-First, Flexible Ethos

The manifesto establishes the philosophy. For current technical configuration
(model names, quantization types, runtime flags, VRAM usage), see
**docs/stack.md** - it is the canonical source of truth and is kept current.

1. The Engine: Standardize on llama.cpp (ROCm/HIP)
- We want to use advanced quantization techniques and have deep control over
  context windows. Ollama was decommissioned early in the project (see roadmap
  PoC 1.1c) because it hides models in unreadable blob folders and limits
  bleeding-edge quantizations.
- The Solution: Use llama.cpp compiled specifically with the ROCm/HIP backend.
  This speaks directly to the AMD GPU's native compute architecture, giving
  maximum tokens per second and zero CPU fallback.

2. The Model Strategy: I-Quants + TurboQuant KV Cache
- To ensure the 10,000th turn of our conversation is just as smart as the first
  without devolving into madness, we need high-fidelity short-term memory.
- I-Quants (IQ4_NL): Preserve highly active reasoning and coding pathways in
  near-perfect definition while aggressively compressing less useful connections.
- TurboQuant KV Cache (turbo4): 4x KV cache compression with near-zero quality
  loss. Originally the manifesto called for "uncompressed KV cache" but the
  Spiritbuun fork's TurboQuant proved to be a better tradeoff - same quality,
  4x less VRAM, enabling 16K context in ~16GB total VRAM.

3. The Consolidation Workflow: One Folder to Rule Them All
- Maintain a Single Source of Truth: ~/ai-models on the Ubuntu server.
- Use the Modern hf CLI to download IQ4_NL .gguf files directly into that folder.

4. The Frictionless Serving Strategy: Caching Flags & TurboQuant
- The llama-server binary runs as a systemd service with advanced execution
  flags locked in. See docs/stack.md and docs/operations.md for current flags.
- Aggressive Prompt Caching: NOTE: --cache-reuse 256 was removed Aug 2026
  (incompatible with turbo4 KV cache, silently disabled). Prompt caching is
  now handled by aios-core's context trimming + slot KV checkpoints.
  See docs/operations.md.
- Flash Attention (-fa on): Optimized attention kernel prevents VRAM bloat.

5. Accessing It
- llama-server exposes an OpenAI-compatible endpoint on port 8080.
- From anywhere on the Tailscale mesh: http://<your-tailscale-ip>:8080/v1
- From IDEs: Point VS Code, Cline, or Roo to the same endpoint.
