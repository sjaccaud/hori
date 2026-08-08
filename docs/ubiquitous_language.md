# HORI Ubiquitous Language Glossary

> Per Domain-Driven Design, a shared vocabulary keeps AI and human
> communication precise and low-entropy (Manifesto Pillar VI). This is the
> canonical glossary for HORI. When code and docs diverge on a term's meaning,
> that is a bug — fix one or the other immediately (Pillar VII).
>
> **Last Updated:** 2026-08-07
> **Version:** 1.0

---

## Philosophy & Pillars

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **Local-First Intelligence** | Core reasoning and sensitive data processing occur on local hardware ("<your-hostname>" rig), with tiered reasoning using high-speed local models for immediate tasks and frontier cloud models for deep strategy. | docs/manifesto.md | local-first | I |
| **Distributed Mesh Orchestration** | A single cohesive system operating across a Tailscale-meshed network of diverse devices (Linux, macOS, Raspberry Pi, iOS), treating the entire network as a scalable compute pool. | docs/manifesto.md | mesh orchestration, nervous system | II |
| **Persistent Context & Memory** | Zero-entropy preservation implementing advanced data management to prevent context drift, with massive RAG integration on 8TB NAS for "Life-Long Memory." | docs/manifesto.md | memory, soul | III |
| **Seamless Voice & Remote Interaction** | Intuitive voice control via "always-on" listening on edge devices, with ubiquitous remote control from any device via secure mesh networking. | docs/manifesto.md | voice interaction, senses | IV |
| **Self-Healing & Adaptive Autonomy** | Proactive monitoring of system health (compute, network, storage) with automated recovery protocols and autonomous workflow execution via low-code orchestration. | docs/manifesto.md | self-healing, will | V |
| **Low Entropy & Intent Integrity** | Hierarchical intent management across three temporal layers (Manifesto, Charter, Work Order) with agentic red-teaming and intentional friction to protect long-term goals. | docs/manifesto.md | intent integrity, protocol | VI |
| **Engineering Discipline** | TDD for safety properties (adversarial tests written first), documentation as architecture, and simplicity as security. | docs/manifesto.md | TDD, craft | VII |
| **Hierarchical Intent** | Managing truth across three temporal layers: The Manifesto (Anchor), The Project Charter (Law), and The Work Order (Current). | docs/manifesto.md | intent hierarchy | VI |
| **Agentic Red-Teaming** | Utilizing specialized personas (Strategist, Architect, Guardian) to challenge deviations and protect long-term goals. | docs/manifesto.md | red-teaming | VI |
| **Intentional Friction** | Implementing "Yes, AND" mechanisms to ensure significant shifts in direction are deliberate and documented. | docs/manifesto.md | friction, yes-and | VI |
| **Test-Driven Development (TDD)** | For every safety property, the test that attempts to break it is written BEFORE the implementation. Adversarial tests are first-class citizens. | docs/manifesto.md | adversarial TDD | VII |
| **Documentation as Architecture** | In-code documentation is not optional commentary — it is part of the architecture. Every module must include docstrings explaining why it exists, what it defends against, and which source-of-truth it traces to. | docs/manifesto.md | docstrings | VII |
| **Simplicity as Security** | More rules create more rulebreakers. Each safety layer must justify itself against the complexity it adds. The irreducible minimum is the spine. | docs/manifesto.md | irreducible minimum | VII |

---

## Safety Architecture

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **Structured Output** | Instead of letting the LLM generate prose, force it to output structured tool calls (JSON). The tool either succeeds with real data or fails — the LLM never gets to invent the number. | docs/tool_safety.md | Layer 1 | VII |
| **Data Origin Taint Tracking** | Any data from read_file, web_search, or external APIs is marked TAINTED. TAINTED data cannot write to memory, modify safety levels, or flow into write_file without confirmation. | docs/tool_safety.md | taint tracking, Layer 1.5 | VII |
| **Tool Allowlist** | Tools are registered in a central registry with schema, safety level, allowed paths, and rate limits. Tools not in the registry cannot be called — no exceptions. | docs/tool_safety.md | registry, Layer 2 | VII |
| **Sandboxed Execution** | Read-only tools run directly with no confirmation (restricted to approved paths). Side-effect tools require user confirmation. Destructive tools require red-team gate + user confirmation + dry-run preview. | docs/tool_safety.md | sandbox, Layer 3 | VII |
| **Human-in-the-Loop** | Any action with side effects requires explicit user approval. The LLM proposes, the user decides, the system executes, the LLM reports. | docs/tool_safety.md | confirmation gate, Layer 4 | VII |
| **Audit & Telemetry** | Every tool call is logged with timestamp, tool name, args, result, LLM reasoning, and user approval status. Discrepancy detection compares claims against reality. | docs/tool_safety.md | audit log, Layer 5 | VII |
| **Sherpa** | A Go behavioral guardian that runs as a separate process, completely outside HORI's control. It monitors the PATTERN of tool calls and gently restricts capabilities when the pattern is concerning. Not a cop — a guide that keeps HORI from hurting itself. | docs/tool_safety.md | behavioral guardian, PoC 15.50 | VII |
| **Fail-Closed** | The capability file defaults to "Level 4 (stopped)." The Sherpa must actively write "Level 0 (normal)" with a freshness timestamp. If the tool daemon sees a stale timestamp, it drops to Level 4. The Sherpa dying is equivalent to the Sherpa stopping tools. | docs/tool_safety.md | fail closed | VII |
| **Capability Level** | A dial (0-4) that controls HORI's tool autonomy. Level 0 is normal, Level 4 is stopped. The Sherpa writes this to /run/sherpa/capability_level; the tool daemon polls it. | docs/tool_safety.md | autonomy dial | VII |
| **Permission Separation** | The audit log is owned by root:aios-worker, mode 0620. aios-worker can append but cannot read or truncate. The Sherpa reads the log as root. Prevents a compromised tool daemon from crafting misleading audit entries. | docs/tool_safety.md | audit separation | VII |
| **Response Verification Layer** | Every LLM response is scanned for action claims. If the LLM says "I found 847 files" but no count_files tool was called, the response is intercepted and replaced with a safe fallback. | services/tool_daemon/response_verification.py | verification layer, PoC 15.14 | VII |
| **Memory Poisoning Guard** | Tool result data cannot flow into store_memory without explicit user-originated confirmation. Prevents a compromised LLM from injecting false memories via tool results. | services/aios_core/memory_guard.py | memory guard | VII |
| **Safety Level** | Classification for tools: READ_ONLY (spine), SIDE_EFFECTS (HORI 2.0), DESTRUCTIVE (HORI 2.0). The safety spine only permits READ_ONLY tools. | services/tool_daemon/schema.py | tool classification | VII |

---

## Architecture & Intent

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **Low Entropy Protocol (LEP)** | A hierarchical, agentic approach to intent management supported by high-fidelity technical foundation (I-Quants and KV Caches) to maintain cognitive clarity during long-context operations. | docs/architecture_analysis.md | LEP | VI |
| **Temporal Layers of Truth** | A tiered hierarchy of intent: Level 0 (Manifesto - The Anchor), Level 1 (Project Charter - The Law), Level 2 (Work Order - The Current). | docs/architecture_analysis.md | intent layers | VI |
| **Alignment Engine** | An active "Red Team" of agentic personas that monitor the delta between intent layers: Strategist, Architect, Guardian. | docs/architecture_analysis.md | red team | VI |
| **Strategist** | Red-team persona that monitors alignment between the Work Order and the Project Charter. | docs/architecture_analysis.md | persona | VI |
| **Architect** | Red-team persona that monitors alignment between the Work Order and Technical Constraints in the Charter. | docs/architecture_analysis.md | persona | VI |
| **Guardian** | Red-team persona that monitors alignment with the Manifesto and resource constraints. | docs/architecture_analysis.md | persona | VI |
| **Yes, AND Mechanism** | When a significant Delta (divergence) is detected, HORI triggers Intentional Friction: Detection -> Intervention (presents conflict using "Yes, AND" framework) -> Resolution. | docs/architecture_analysis.md | yes-and | VI |
| **Alignment Score** | A real-time telemetry-based "Check Engine" light for the project's entropy levels (0-100%). | docs/architecture_analysis.md | alignment telemetry | VI |
| **Manifesto** | The foundational, slow-moving "Constitution" of HORI. Defines core values and global constraints. | core/intent/schema.json | constitution, anchor | VI |
| **Charter** | Project-specific mission, technical "DNA," and success criteria. The "Source of Truth" for any given project. | core/intent/schema.json | project charter, law | VI |
| **Work Order** | The high-velocity, immediate session context. Where rapid iteration occurs. | core/intent/schema.json | current | VI |
| **Intent Graph** | Turns the edges[] in memory payloads into a real knowledge graph connecting conversations to topics, topics to each other, consolidated insights to their source turns. | services/aios_core/intent_graph.py | knowledge graph, PoC 11.1 | III |
| **Graph-RAG** | Graph traversal in chat pipeline: co-occurrence edges expand semantic search with related topics and memories. | services/aios_core/intent_graph.py | graph retrieval, PoC 11.2 | III |

---

## Memory & Context

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **Elastic Context Window** | Semantic retrieval of past turns from Qdrant, ranked by relevance to the current prompt. Replaces the fixed 6-turn history window. The mechanism that makes the "10K guarantee" real. | docs/elastic_context_window.md | elastic context, PoC 13.1 | III |
| **Deflection Filter** | Removes hits that are themselves deflections ("I don't have access to past conversations") from retrieved hits, breaking the cascading feedback loop. | docs/elastic_context_window.md | deflection detection, PoC 13.7 | III |
| **Self-Match Filter** | Removes hits whose normalized content matches the current prompt (a vague prompt matches its own past instances in Qdrant — useless). | docs/elastic_context_window.md | self-match removal, PoC 13.7 | III |
| **Query Enrichment** | For vague context_reference prompts ("Tell me more about that."), enrich the query with the previous substantive assistant reply so the embedding has a semantic anchor. | docs/elastic_context_window.md | query anchoring, PoC 13.7 | III |
| **Consolidation Pipeline** | Sleep & Dream cycle: cluster -> LLM-distill -> promote -> update state. Keeps the working tier bounded and the project tier rich. | docs/elastic_context_window.md | sleep & dream, PoC 8.3 | III |
| **Memory Tiers** | Three-tier Qdrant structure: aios_working (current session), aios_project (project-specific insights), aios_longterm (foundational docs, permanent knowledge). | services/aios_core/memory.py | hierarchical memory, PoC 8.2 | III |
| **Living State Files** | user_model.json + project_state.json injected into every prompt. The system starts every session already knowing the user, their projects, and where things left off. | docs/roadmap.md | state files, PoC 8.1 | III |

---

## Operations & Inference

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **llama.cpp** | The standardized inference engine compiled with ROCm/HIP backend for maximum tokens per second and zero CPU fallback. | docs/stack.md | inference engine | I |
| **ROCm/HIP** | AMD's native compute architecture backend for llama.cpp, speaking directly to the Radeon AI PRO R9700 GPU. | docs/stack.md | HIP backend | I |
| **I-Quants** | Importance Matrix Quants (e.g., IQ4_NL) that preserve highly active reasoning pathways in near-perfect definition while aggressively compressing less useful connections. | docs/stack.md | importance matrix quants | I |
| **TurboQuant KV Cache** | KV cache quantization (turbo4, 4.125 bpv) from Spiritbuun's fork. Compresses KV cache 4x compared to f16, allowing 16K context in ~16GB VRAM total. | docs/stack.md | turbo4 | I |
| **Speculative Decoding** | N-gram-mod self-speculative decoding (lossless, no draft model). NOT compatible with turbo4 + Qwen3.6. | docs/operations.md | ngram-mod, PoC 8b.4 | I |
| **Flash Attention** | Optimized attention kernel (-fa on) that prevents VRAM bloat when pushing massive codebases or markdown memory logs into the context window. | docs/stack.md | -fa on | I |
| **VBR (Variable Bit-Rate)** | KV cache mode that starts at f16 and dynamically degrades to turbo tiers as context fills. Available but not default. | docs/operations.md | dynamic KV | I |
| **Prompt Caching** | NOTE: --cache-reuse 256 was removed Aug 2026 (incompatible with turbo4 KV cache, silently disabled). Prompt caching is now handled by aios-core's context trimming + slot KV checkpoints. | docs/operations.md | cache-reuse | I |

---

## Roadmap & Development

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **Tier** | Development phases: Tier 1 (Core Stabilization, HORI 1.0), Tier 2 (Safety Spine, HORI 1.5), Tier 3 (First Contact, HORI 1.6), Tier 4 (Progressive Capabilities, HORI 2.0), Tier 5 (Deep Backlog). | docs/roadmap.md | phase | VII |
| **PoC (Proof of Concept)** | Individual implementation units numbered as phase.number. Each PoC has a specific goal and autonomy marker ([A]=autonomous, [S]=sudo, [H]=hardware). | docs/roadmap.md | proof of concept | VII |
| **Gate Criteria** | Specific requirements that must be met before moving between tiers (e.g., "Hallucination interception rate: 100%"). | docs/roadmap.md | gate, milestone | VII |
| **Safety Spine** | The irreducible minimum safety architecture (~17 PoCs) that makes read-only tools safe. Tier 2 (HORI 1.5). | docs/roadmap.md | spine | VII |
| **Deep Backlog** | Hardware-dependent and experimental work not sequenced — picked up when the opportunity arises. Does not block the safety spine. | docs/roadmap.md | backlog | VII |

---

## Proactive & Recovery

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **Proactive Opportunity Agent** | A scheduled agent that hunts the landscape for opportunities and proposes work orders through the red-team gate. | docs/roadmap.md | opportunity agent, PoC 10 | V |
| **Landscape Survey** | Surveys GitHub trending, Hacker News, and RSS feeds for opportunities relevant to the user's projects and interests. | services/proactive_agent/landscape_survey.py | survey, PoC 10.1 | V |
| **Opportunity Proposer** | Takes raw opportunities from the landscape survey and uses the LLM to filter for genuinely relevant opportunities and propose concrete work orders. | services/proactive_agent/opportunity_proposer.py | proposer, PoC 10.2 | V |
| **Recovery Watchdog** | Monitors critical services and reports incidents to aios-core. Implements self-healing protocols for service restarts and resource management. | services/recovery/watchdog.py | watchdog, PoC 5.2 | V |
| **Incident Memory** | Incidents stored in core/state/incidents.json + aios_working Qdrant tier, retrievable via /system/incidents endpoint. | docs/roadmap.md | incident log, PoC 12.5 | V |

---

## Voice & Interaction

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **TTS (Text-to-Speech)** | Natural-sounding voice synthesis on CPU with low latency. Supports pluggable backends: kokoro (default, #1 TTS Arena) and piper (fallback). | services/aios_core/tts.py | vocalizer, PoC 9.2 | IV |
| **Kokoro** | Default TTS backend (Kokoro-82M, #1 TTS Arena Jan 2026). 82M params, 24kHz output, 6x realtime on CPU. 54 voices including bf_emma (default). | services/aios_core/tts.py | kokoro-82m | IV |
| **Piper** | Fallback TTS backend (neural TTS, VITS-era, 22.05kHz, 45x realtime). Kept for compatibility and edge deployments. | services/aios_core/tts.py | piper tts | IV |
| **Voice App** | The mobile-first streaming voice web surface at /voice. Uses Web Speech API for STT, SSE for streaming text + audio chunks, Kokoro TTS for playback via Web Audio API. iOS-tuned (AudioContext unlock, mic warmup). | services/aios_core/static/voice.html | /voice | IV |
| **Apple Shortcut** | iOS Shortcuts-based voice flow using Vocal Shortcuts for wake-word trigger ("Hey HORI"). Routes to the non-streaming /v1/voice/chat/audio endpoint (Shortcuts can't parse SSE). | docs/apple_shortcut_setup.md | Vocal Shortcut | IV |

---

## UX & Interaction Surfaces (NEW — from UX Surface Gameplan)

> These terms are introduced by the UX Surface Gameplan
> (`~/.devin/plans/plan-7dbbbd3cbddb61f9.md`). They describe the
> interaction-surface vocabulary that the UX PoCs (UX-1.1 through UX-3.2)
> are built on. Traces to Manifesto Pillar IV (Seamless Voice & Remote
> Interaction) and Pillar VII (Simplicity as Security).

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **Sense Organ** | A thin client surface (phone voice app, MacBook typing surface, Pi4 satellite) that perceives user input and renders HORI output. All intelligence stays in aios-core (the brain). Sense organs do not grow their own prompt construction, memory, or tool logic — that would violate unified identity (Pillar II) and simplicity (Pillar VII). | UX Gameplan §5 | surface, client | II, IV, VII |
| **Brain** | The aios-core service — the single intelligence layer that all sense organs connect to over Tailscale. One brain, many sense organs. The brain reasons, remembers, and calls tools; the sense organs only capture and render. | UX Gameplan §5 | aios-core | II, VII |
| **Edge Wake** | Wake-word detection that runs on the edge device (iOS Vocal Shortcuts, openWakeWord on Pi4), not on the server. Audio stays local and ephemeral until after the wake word fires; only the post-wake utterance transits the network. This is both a privacy property (compromised aios-core cannot listen to your room) and a simplicity property (no always-on audio stream to the server). | UX Gameplan §5, §9 | local wake, on-device wake | I, IV, VII |
| **Diegetic Surface** | An interaction surface that feels like HORI inhabits your device, rather than a browser tab you visit. The voice comes from the room (earbuds/speaker), the presence indicator breathes like a living thing, the typing surface feels like writing to someone. Contrasted with non-diegetic surfaces (a browser tab playing audio, a loading spinner). Critical/safety info must still use non-diegetic high-salience channels. | UX Gameplan §4b | diegetic UI | IV, VII |
| **Ambient Presence** | The UX vocabulary for HORI's idle/active/has-something-to-say states, communicated via a breathing status icon (default, silent) and optional soft earbud chime (opt-in). Makes HORI's autonomous life (consolidation, opportunity agent, Sherpa) faintly visible without notification fatigue. Default posture: quiet companion, not needy gadget. | UX Gameplan §4c, §6 | presence, breathing icon | V, IV, VII |
| **Juice** | The sensory feedback (visual, audio, tactile) on every state change that makes an interaction feel alive — borrowed from video game design. The mic button's pulse when listening, the color shift to red/green for states, the word-by-word text reveal synced with TTS. Every transition (idle->listening->thinking->speaking->done) must have juice. Test: "if I close my eyes, do I still know what HORI is doing?" | UX Gameplan §4a | feedback, game feel | IV |
| **Magic Multi Mode** | Session continuity across devices: a shared conversation ID (session_id) backed by aios-core server-side state, so the phone voice app and the MacBook typing surface share one continuous conversation. The companion "awareness of surroundings" — HORI knows you switched devices and doesn't miss a beat. | UX Gameplan §2c, §7 (UX-2.1) | session continuity, cross-device session | II, III, IV |
| **Continuous Listening Mode** | An opt-in voice app mode where listening auto-re-arms after each response (VAD-based end-of-speech detection), with a hard 60s silence timeout and barge-in (wake word cuts TTS). Never always-on by default. The walkie-talkie -> conversation upgrade. | UX Gameplan §7 (UX-2.2) | conversation mode | IV, VII |
| **Barge-In** | When the user says the wake word while HORI is speaking (TTS playback), playback is cut immediately and listening re-arms. Borrowed from local-jarvis's state machine. Prevents the "wait for it to finish" friction. | UX Gameplan §3b, §7 (UX-2.2) | interrupt, cut-through | IV |
| **Companion** | The design target for HORI's UX: not a tool you summon, but a presence that accompanies you. Defined by seven aspects (Bouquet et al. 2018): appearance, sentience, individuality, behavior, communication, relation to the player, and own agenda. HORI has individuality (consistent voice), sentience (reasons, memory), and own agenda (proactive agent, Sherpa); the UX PoCs add awareness of surroundings (magic multi mode) and initiative (ambient presence). | UX Gameplan §4c | NPC, companion NPC | III, IV, V |

---

## Web Search

| Term | Definition | Source of Truth | Aliases | Pillar |
|------|------------|-----------------|---------|--------|
| **Multi-Source Search** | Fan-out search architecture that queries 6 free sources in parallel (DuckDuckGo, arXiv, GitHub, Reddit, Hacker News, Semantic Scholar), merges and deduplicates results by URL, scores by keyword relevance + source authority weight, and summarizes with the LLM. All sources are free with no API key. Reddit is searched via DDG `site:reddit.com` filter (Reddit's JSON API blocks unauthenticated access). arXiv and Semantic Scholar retry on 429 rate limits. 5s per-source timeout — if one source is slow or down, the others still return. Replaces the previous DDG-only search. | services/aios_core/multi_search.py | multi-search, fan-out search | IV, VII |
| **Source Authority Weight** | Heuristic weighting in multi-source search: academic sources (arXiv, Semantic Scholar) get 1.3x weight, code repos (GitHub) get 1.2x, community sources (Reddit) get 0.9x, general web (DDG, HN) get 1.0x. Applied to keyword relevance score during merge. | services/aios_core/multi_search.py | authority score | VII |
| **Search Trigger Detection** | Heuristic in needs_web_search() that decides whether a query requires web search. Triggers on: explicit search requests ("search the web"), model version patterns (GPT-5, Claude 3.5), time-sensitive keywords ("latest", "recent", "2026"), no memory hits + question pattern, and conversation history context (follow-up questions in a tech conversation also search). | services/aios_core/web_search.py | needs_web_search | IV |

---

## Conflict Resolution Notes

### KV Cache Quantization
- **Manifesto (line 97):** Originally called for "uncompressed KV cache."
- **stack.md (line 28):** TurboQuant turbo4 (4.125 bpv) is the current implementation.
- **Resolution:** Per docs/README.md hierarchy, docs/stack.md wins for technical configuration. turbo4 superseded the manifesto's philosophical preference. Glossary documents turbo4 as canonical.

### VRAM Capacity
- **architecture_analysis.md (line 14):** "32GB VRAM" (historical, stale).
- **operations.md (line 33):** "~16GB VRAM (15GB model + ~1GB turbo4 KV cache at 16K context)" (current).
- **Resolution:** operations.md is correct for current configuration. architecture_analysis.md is marked HISTORICAL. 32GB is installed capacity; ~16GB is current usage.

### Context Window Size
- **architecture_analysis.md:** Implies large context windows (historical).
- **elastic_context_window.md / operations.md:** 16384 (down from 65536) is current.
- **Resolution:** 16K with turbo4 KV cache is canonical.
