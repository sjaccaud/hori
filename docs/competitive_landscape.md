# HORI Competitive Landscape

## Purpose

This document maps HORI against the existing landscape of AI agent
operating systems, runtimes, and memory systems. It is the evidence
backing STRAT-1 (the strategic positioning argument) and feeds into
STRAT-4 (External Review Package) and STRAT-5 (Open-Source Strategy).

The thesis: **Nobody has all 8 differentiators.** HORI occupies a
unique position at the intersection of local-first inference,
kernel-enforced safety, and voice-first interaction. Each competitor
below covers 1-3 of the 8; none covers all 8, and most don't cover
the 3 that matter most for personal agent safety.

---

## The 8 Differentiators

| # | Differentiator | What it means | Why it matters |
|---|---------------|---------------|----------------|
| 1 | **Local-first inference** | LLM runs on your hardware, no cloud calls for core reasoning | Privacy, no per-thought billing, no service disappearance |
| 2 | **Kernel-enforced safety** | Landlock + seccomp isolate the agent at the OS syscall level | The LLM cannot escape its sandbox even if compromised |
| 3 | **Fail-closed guardian** | Sherpa (Go) watches the agent (Python); if Sherpa dies, tools stop | Cross-language isolation, heartbeat pattern, no single-process trust |
| 4 | **Intent hierarchy** | Manifesto → Charter → Work Order governs what the agent does | The agent follows intent, not impulse |
| 5 | **Memory consolidation** | Working → Project → Long-term memory tiers with scheduled consolidation | 10,000th turn is as smart as the 1st |
| 6 | **Voice-first** | Primary interface is voice (Safari on-device STT + Kokoro TTS) | Hands-free, conversational, not prompt-engineering |
| 7 | **Progressive capability unlock** | Read-only → side-effect → destructive, gated at each level | The agent earns trust; it doesn't start with full access |
| 8 | **Adversarial TDD** | Safety properties have tests written FIRST that must fail, then pass | Safety is verified, not asserted |

---

## Competitor Analysis

### 1. AIOS (Rutgers University, COLM 2025)

**What it is:** An academic research project proposing an OS-like
abstraction layer for LLM agents. Provides agent scheduling (FIFO and
Round-Robin), context management, memory/storage management, tool
management, and basic access control via an "AIOS Kernel" and "AIOS SDK"
(Cerebrum). Achieves up to 2.1x faster execution through concurrent
agent execution and resource allocation.

**What it has:** Agent scheduling, context management, tool management,
basic access control (privilege groups, still under development).

**What it lacks:**
- No kernel-enforced safety — it's a Python abstraction layer over the
  OS kernel, not kernel-level isolation (#2)
- No fail-closed guardian (#3)
- No intent hierarchy (#4)
- No memory consolidation (#5)
- No voice interface (#6)
- No progressive capability unlock (#7)
- No adversarial TDD (#8)
- No production deployment — research prototype only

**Differentiators covered:** 0 of 8 (local-first inference is implied
but not enforced; the rest are absent)

**Position:** Academic scheduling optimization. Solves a different
problem (resource management for concurrent agents) with no safety,
deployment, or user interaction story.

---

### 2. oikOS (oikos-os/oikOS)

**What it is:** A local-first AI operating system with 51 MCP tools
behind a 7-layer Python middleware stack (auth, error handling, privacy,
autonomy, rate limiting, cost tracking, audit). Features a personal
knowledge vault with hybrid search, model-agnostic inference (Ollama,
Anthropic, OpenAI), and a "10-probe adversarial gauntlet" for security
testing. Data never leaves the machine unless explicitly allowed via
the `NEVER_LEAVE` privacy tier.

**What it has:** Local-first inference (#1), application-layer security
middleware, privacy tiers, MCP tool ecosystem.

**What it lacks:**
- No kernel-enforced safety — security is middleware, not Landlock/seccomp (#2)
- No fail-closed guardian (#3)
- No intent hierarchy (#4)
- Memory consolidation (KAIROS) is roadmap, not shipping (#5)
- Voice interface is planned, not implemented (#6)
- No progressive capability unlock (#7)
- Adversarial testing is a "gauntlet" (probes), not TDD (tests-first) (#8)

**Differentiators covered:** 1 of 8 (local-first inference)

**Position:** The closest competitor on philosophy (local-first, privacy)
but security is middleware, not kernel. The 51 MCP tools vs HORI's 4
read-only tools is a feature-count vs safety-depth tradeoff — oikOS
prioritizes breadth, HORI prioritizes depth.

---

### 3. AriaOS (Commercial + Open-Source Variants)

**What it is:** Two distinct projects share this name:
1. **Commercial AriaOS** — a sovereign AI OS for DDIL (Denied, Disrupted,
   Intermittent, Limited) environments with weighted voting governance,
   Context Kernel v3 for deterministic state management, and pre-LLM
   compliance layers.
2. **Open-source AriaOS** (jeremie225ci/ariaos) — a VM-based computer
   use agent OS with Terminal Aria, local voice mode, visible long-term
   memory, and browser/desktop automation inside an isolated VM.

**What it has (open-source variant):** Local inference (partial #1),
VM-level isolation, voice mode (partial #6), long-term memory (basic).

**What it lacks:**
- VM isolation is coarse-grained — the agent has full sudo inside the VM,
  maximum blast radius. No Landlock/seccomp fine-grained control (#2)
- No fail-closed guardian (#3)
- Governance is role-based voting (commercial) or absent (open-source),
  not intent hierarchy (#4)
- Memory consolidation is basic Markdown files, not tiered consolidation (#5)
- No progressive capability unlock — the agent starts with full VM access (#7)
- No adversarial TDD (#8)

**Differentiators covered:** 1.5 of 8 (partial local inference, partial voice)

**Position:** The VM approach is the opposite of HORI's philosophy. AriaOS
gives the agent a full computer to control — maximum capability, maximum
blast radius. HORI gives the agent minimal capability and makes it earn
more. The commercial DDIL variant is over-engineered for personal
productivity (weighted voting governance for a single user is unnecessary).

---

### 4. OpenKosmos (Microsoft/open-kosmos)

**What it is:** A local-first AI Agent Studio built on Electron with a
low-code visual editor for creating personalized AI agents. Features
multi-agent workspaces, MCP ecosystem integration, smart context
compression, agent library for sharing configurations, voice input via
Whisper with local GPU acceleration, and native file/system access.

**What it has:** Local-first inference (#1), voice input via Whisper
(partial #6), MCP tool ecosystem, visual agent builder.

**What it lacks:**
- No kernel-enforced safety — runs as a standard Electron app with full
  user privileges (#2)
- No fail-closed guardian (#3)
- No intent hierarchy (#4)
- Memory is SQLite persistence without consolidation (#5)
- Voice is an add-on feature, not voice-first design (#6)
- No progressive capability unlock (#7)
- No adversarial TDD (#8)

**Differentiators covered:** 1.5 of 8 (local-first, partial voice)

**Position:** A low-code agent studio, not a runtime. Prioritizes ease
of agent creation over safety. The Electron architecture means any
agent has full user-level access to the filesystem — there's no
isolation between the agent and the user's data.

---

### 5. Kronos Agent OS (spyrae/kronos-agent-os)

**What it is:** A self-hosted operating layer for durable AI agents with
memory (session history, FTS5 recall, Mem0 vectors, knowledge graph),
sleep-time consolidation, skills (workspace-local procedures), and MCP
tools. Features 5-layer defense (input validation, sanitization, output
redaction, PII masking, loop detection), capability gates with
conservative defaults, Docker sandbox for dynamic tool execution, and
optional swarm coordination mode.

**What it has:** Local-first inference (#1), memory consolidation via
sleep-time processing (partial #5), capability gates (partial #7),
Docker sandboxing, 5-layer defense middleware.

**What it lacks:**
- No kernel-enforced safety — Docker is container-level, not syscall
  filtering. Landlock/seccomp provide finer-grained control (#2)
- No fail-closed guardian (#3)
- No intent hierarchy (#4)
- No voice interface (#6)
- Capability gates are env-flag-based, not competency-based evidence gates (#7)
- No adversarial TDD (#8)

**Differentiators covered:** 2.5 of 8 (local-first, partial consolidation,
partial capability gates)

**Position:** The closest competitor on memory consolidation. Kronos
implements sleep-time consolidation, which HORI has designed but not
yet scheduled (PoC 8.5). However, Kronos's security is Docker containers
+ middleware, not kernel-level isolation. A Docker container escape
(a real CVE history) gives the agent full access; a Landlock escape
requires a kernel exploit.

---

### 6. MemGPT / Mnemoverse (Letta)

**What it is:** MemGPT introduced virtual context management —
OS-inspired hierarchical memory with paging between bounded "main
context" (prompt window) and larger "external context" (vector
database). Enables unbounded context for document analysis and
multi-session chat. The project has rebranded to Letta, then pivoted
to Letta Code.

**What it has:** Memory paging theory (partial #5).

**What it lacks:**
- No local-first inference — designed for cloud LLMs (#1)
- No kernel-enforced safety (#2)
- No fail-closed guardian (#3)
- No intent hierarchy (#4)
- Memory consolidation is interrupt-driven paging under memory pressure,
  not scheduled offline consolidation (#5)
- No voice interface (#6)
- No progressive capability unlock (#7)
- No adversarial TDD (#8)
- Research has shown persistent memory poisoning vulnerabilities
  (MemPoison, MemSecBench) that adversarial TDD could address

**Differentiators covered:** 0.5 of 8 (partial memory consolidation theory)

**Position:** A memory system, not a runtime. MemGPT's contribution is
the theory of virtual context management, which HORI builds on with
tiered consolidation. But MemGPT has no safety, no voice, no deployment
story, and has undergone multiple pivots reducing production readiness.

---

## Gap Analysis: The Differentiator Matrix

| Differentiator | HORI | AIOS | oikOS | AriaOS | OpenKosmos | Kronos | MemGPT |
|---------------|------|------|-------|--------|------------|--------|--------|
| 1. Local-first inference | **Yes** | Implied | **Yes** | Partial | **Yes** | **Yes** | No |
| 2. Kernel-enforced safety | **Yes** | No | No | No (VM) | No | No (Docker) | No |
| 3. Fail-closed guardian | **Yes** | No | No | No | No | No | No |
| 4. Intent hierarchy | **Yes** | No | No | No | No | No | No |
| 5. Memory consolidation | **Yes** | No | Roadmap | Basic | No | **Partial** | Partial |
| 6. Voice-first | **Yes** | No | Planned | Partial | Partial | No | No |
| 7. Progressive unlock | **Yes** | No | No | No | No | Partial | No |
| 8. Adversarial TDD | **Yes** | No | Probes | No | No | No | No |
| **Total** | **8/8** | **0/8** | **1/8** | **1.5/8** | **1.5/8** | **2.5/8** | **0.5/8** |

---

## Key Findings

### 1. Nobody has the safety spine

The most striking gap: **no competitor has kernel-enforced safety (#2)
or a fail-closed guardian (#3).** Every other project relies on either:
- Application-layer middleware (oikOS, Kronos, OpenKosmos)
- VM isolation with full agent privileges (AriaOS)
- No isolation at all (MemGPT, AIOS academic)

HORI's Landlock + seccomp + Sherpa combination is unique. The cross-
language guardian pattern (Go watching Python) with fail-closed
heartbeat is not found in any competitor. This is the strongest
patentable element (see STRAT-3).

### 2. Intent hierarchy is unaddressed

No competitor has a Manifesto → Charter → Work Order governance
structure. The closest is AriaOS's commercial DDIL governance (weighted
voting), but that's enterprise compliance, not personal intent
management. HORI's intent hierarchy is a novel contribution: the agent
follows a governance document, not just a system prompt.

### 3. Voice-first is rare

Only AriaOS (open-source) and OpenKosmos have any voice capability,
and both treat it as an add-on. HORI's voice-first design — with
streaming SSE, on-device STT (Safari), local TTS (Kokoro), and the
voice page as the primary interface — is unique. The 500-turn stress
test runs through the voice endpoint, not just text.

### 4. Adversarial TDD is unique

oikOS has a "10-probe adversarial gauntlet" which is testing, not TDD.
The distinction matters: HORI writes the test that breaks the safety
*before* building the safety (red/green/refactor). This is a
methodology contribution, not just a testing contribution.

### 5. Memory consolidation is the closest race

Kronos is the only competitor with shipping sleep-time consolidation.
HORI has the tiered architecture (working → project → long-term) but
scheduled consolidation (PoC 8.5) is still pending. This is the one
area where a competitor is ahead — but Kronos lacks the safety spine
that makes consolidation trustworthy (consolidated memories from a
compromised agent are a liability, not an asset).

---

## Strategic Implications

### For positioning (STRAT-1)
HORI is not "another AI OS." It is the only local-first agent runtime
with a kernel-enforced safety spine. The competitive landscape confirms
this: every other project either lacks safety entirely or relies on
application-layer controls that a compromised LLM can bypass.

### For patent exploration (STRAT-3)
The Sherpa pattern (cross-language fail-closed guardian) and the
progressive capability unlock (competency-based, not flag-based) are
the most novel and defensible elements. No competitor has either.

### For external review (STRAT-4)
The differentiator matrix is the one-page summary for reviewers. The
gap analysis shows that HORI's contribution is not incremental — it's
the intersection of safety + local-first + voice, which nobody else
occupies.

### For open-source strategy (STRAT-5)
The safety spine (Landlock + seccomp + Sherpa) is the differentiator
that would be hardest for a fork to replicate. It's also the part most
valuable to the community. An open-source release that leads with the
safety architecture would establish HORI as the reference implementation
for safe local agents.

---

## Methodology

This analysis was compiled from:
- Public documentation and repositories for each project
- The HORI red-team analysis (`docs/safety.md`)
- The HORI manifesto (`docs/manifesto.md`) for the 8 differentiators
- The HORI roadmap (`docs/roadmap.md`) for implementation status
- The 500-turn stress test results for memory and safety claims

Competitor information was gathered from public sources as of August 2026.
Projects evolve — this document should be refreshed quarterly.
