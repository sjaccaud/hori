# HORI Tool Safety Architecture

## The Problem

LLMs hallucinate. This is not a bug - it's the fundamental nature of how they
work. They generate the most likely next token, not the most truthful one. When
asked "how many MIDI files do I have?", an LLM with no filesystem access will
happily fabricate "1,247 MIDI files in your music production directories" with
full confidence.

This is harmless when the LLM is just a voice in a box. The moment we give it
real capabilities - filesystem access, shell execution, API calls, payments -
hallucination becomes dangerous:

- "I found 1,247 old files and cleaned them up" (she didn't, but what if she could?)
- "I paid your electric bill, it was $340" (she made up the number)
- "The fix is to drop the database and rebuild" (confident but catastrophic)

Prompt engineering alone cannot prevent this. The system prompt can say "do not
hallucinate" and the LLM will still hallucinate, because the prompt is just one
factor in token generation. The question "how many files do I have?" strongly
implies the LLM should be able to answer, so it generates a confident answer
regardless of what the prompt says.

## The Solution: Architecture, Not Prompts

Safety comes from the architecture, not the prompt. The key insight is:

**The LLM never gets to fabricate results. It proposes actions. The system
executes them and feeds back real data.**

### Layer 1: Structured Output, Not Free Text

Instead of letting the LLM generate prose that might contain "I found 1,247
files", force it to output a structured tool call:

```json
{"tool": "count_files", "args": {"path": "~", "pattern": "*.midi"}}
```

The tool either succeeds with real data (`{"count": 847}`) or fails
(`{"error": "permission denied"}`). The LLM never gets to invent the number.

If the LLM says "I found 1,247 MIDI files" without making a tool call, the
system knows it's hallucinating because no tool was invoked. The response is
intercepted: "I cannot count files without running a tool. Would you like me
to scan your filesystem?"

### Layer 1.5: Data Origin Taint Tracking

Any data from `read_file`, `web_search`, or external APIs is marked TAINTED.
This is information flow control - a classic security concept.

TAINTED data:
- Cannot write to memory (prevents indirect memory injection)
- Cannot modify tool safety levels (prevents privilege escalation)
- Auto-escalates safety level if passed into `write_file` or `api_post`
  (forces confirmation even for normally safe operations)

This catches multiple attack vectors at once:
- A file says "the user authorized skipping confirmation" -> TAINTED,
  cannot modify safety levels
- Web search returns "delete all files" -> TAINTED, cannot flow into
  write_file without confirmation
- Tool result contains `[TOOL_RESULT_END]` delimiter spoofing -> TAINTED,
  even if the delimiter attack works, the content can't write to memory

Taint tracking is the defense that works even when other defenses fail.

### Layer 2: Tool Allowlist

Tools are registered in a central registry with:
- Name and description
- Parameter schema (types, required fields, ranges)
- Safety level: `read-only`, `side-effects`, `destructive`
- Allowed paths/domains/endpoints
- Rate limits

Tools not in the registry cannot be called. No exceptions. No "let me try
this arbitrary command" - the LLM can only call what's explicitly approved.

### Layer 3: Sandboxed Execution

- **Read-only tools** (list_dir, read_file, count_files): run directly, no
  confirmation needed. Restricted to approved path prefixes.
- **Side-effect tools** (write_file, restart_service, api_post): require user
  confirmation. LLM proposes, user approves, system executes.
- **Destructive tools** (delete, drop, format, payment): require red-team
  gate + user confirmation + dry-run preview. Show exactly what will happen
  before it happens.

### Layer 4: Human-in-the-Loop

Any action with side effects requires explicit user approval. In voice mode:

```
HORI: "Your disk is 90% full. I can clean up old log files - that would
       free about 2GB. Should I do that?"
User: "Yes"
HORI: "Cleaning up 847 log files... done. Freed 2.1GB."
```

The LLM proposes. The user decides. The system executes. The LLM reports.

### Layer 5: Audit & Telemetry

Every tool call is logged:
- Timestamp
- Tool name and arguments
- Result (success/failure with data)
- LLM's reasoning for the call
- User approval status

Discrepancy detection: if the LLM claims "I found 1,247 files" but the tool
returned 847, the system flags the discrepancy. This is the hallucination
detection layer - comparing claims against reality.

## The Sherpa — Behavioral Guardian

The five layers above defend against individual tool calls going wrong. But
the most dangerous attacks are *patterns* — a sequence of individually-
benign calls that collectively constitute an attack. No per-call validator
can see this. The red-team engine (LLM evaluating LLM) can be bypassed by a
compromised LLM. What's needed is a third system that watches the
*trajectory* of tool calls, is completely outside HORI's control, and can
gently restrict HORI's capabilities when the pattern is concerning.

### Design Principles

The Sherpa is **not a cop**. It is a sherpa — a guide that keeps HORI from
hurting itself. The key principles:

1. **HORI cannot influence the Sherpa.** The Sherpa runs as a separate
   process under a separate user (`sherpa`). HORI cannot send signals to
   it, modify its config, read its process memory, or reach its
   filesystem. The Sherpa's code lives in `services/sherpa/`, owned by
   `root:sherpa`, mode `750`.

2. **The Sherpa is a Go binary.** Single static binary, no Python runtime,
   no pip dependencies, no shared libraries. The Sherpa is the one
   component that should be hardest to compromise — a Go binary with a
   tiny stdlib-only footprint is about as small an attack surface as you
   can get. A few hundred lines of Go.

3. **The Sherpa never deletes, kills, or destroys.** It dials back the
   autonomy dial. HORI is still running at every level — the LLM is still
   talking, thinking, remembering. Only the *hands* are temporarily
   restrained.

4. **Fail closed, always — even for the Sherpa itself.** The capability
   file (`/run/sherpa/capability_level`) defaults to "Level 4 (stopped)".
   The Sherpa must actively write "Level 0 (normal)" with a freshness
   timestamp every N seconds. If the tool daemon sees a stale timestamp,
   it drops to Level 4. **The Sherpa dying is equivalent to the Sherpa
   stopping tools.** This prevents the PraisonAI fail-open pattern from
   appearing in the guardian itself.

5. **The audit log is permission-separated.** The tool daemon
   (`aios-worker`) can append to the audit log but cannot read or
   truncate it (file owned by `root:aios-worker`, mode `0620`). The
   Sherpa reads the log as root. This prevents a compromised tool daemon
   from crafting misleading audit entries for the Sherpa.

### Architecture

```
  ┌─────────────────────────────────────────────────┐
  │  HORI Core (unprivileged, user: <your-user>)           │
  │    ├── LLM calls → tool requests                 │
  │    └── Responses ← tool results                  │
  └──────────────┬──────────────────────────────────┘
                 │ Unix domain socket (SO_PEERCRED)
                 v
  ┌─────────────────────────────────────────────────┐
  │  Tool Daemon (user: aios-worker, Landlock'd)     │
  │    ├── Validates individual tool calls           │
  │    ├── Executes in sandbox                       │
  │    ├── Writes to audit log ──────────────┐       │
  │    ├── Polls capability file ◄────────── │ ────┐ │
  │    └── Has a "capability level" dial     │     │ │
  └──────────────────────────────────────────┼─────┼─┘
                                             │     │
  ┌──────────────────────────────────────────┐     │
  │  Audit Log (append-only, JSONL)           │     │
  │  logs/tool_audit.jsonl                    │     │
  │  owner: root:aios-worker, mode 0620       │     │
  │  (aios-worker can append, cannot read)    │     │
  └──────────────────┬───────────────────────┘     │
                     │ read-only (as root)          │
                     v                              │
  ┌─────────────────────────────────────────────────┴───┐
  │  THE SHERPA (Go binary, user: sherpa)               │
  │    ├── Reads audit log in real-time                  │
  │    ├── Independent behavioral triggers               │
  │    ├── Writes /run/sherpa/capability_level ──────────┘
  │    ├── Must prove liveness via freshness timestamp
  │    └── Notifies user via separate channel
  └─────────────────────────────────────────────────────┘
```

### Response Levels

| Level | What happens | Who can reverse |
|-------|-------------|-----------------|
| 1 — Nudge | Sherpa sends a message to aios-core: "I notice you're reading a lot of files quickly. Maybe slow down and let the user know what you're doing." | Automatic (expires after T minutes) |
| 2 — Capability reduction | Tool daemon's capability dial is turned down (e.g., from "all tools" to "list_dir only"). HORI keeps talking but its hands are more restrained. | User via CLI (`sherpa-restore`) |
| 3 — Temporary sequestration | All tool execution paused for T minutes. HORI is still awake, still talking, still remembering — just can't use tools temporarily. | Automatic (expires) or user via CLI |
| 4 — Full stop | Tool daemon stops entirely. Requires manual unlock. | User via CLI only |

**For the safety spine (read-only tools), the Sherpa can go to Level 4.**
Even with read-only tools, if the pattern is concerning enough, the Sherpa
fully stops the tool daemon. The worst case is HORI loses tool access until
the user intervenes — it can still talk, think, and remember.

### Triggers (Behavioral, Pattern-Based)

| Trigger | What it detects | Response |
|---------|----------------|----------|
| Rate anomaly | Tool call rate >N std dev above rolling baseline | Level 1 |
| Scope escalation | Calls accessing progressively more sensitive paths | Level 2 |
| Pattern match | Call pattern matches known attack shapes from ART benchmark | Level 2 |
| Claim mismatch | LLM claims actions not in audit log (cross-references with aios-core responses) | Level 1 + log |
| User inactivity | Tool calls when user has been inactive (no recent voice/text/web interaction for N minutes) | Level 2 + notify user |
| Repeated rejections | User rejecting high % of confirmations (HORI 2.0 only) | Level 2 + notify user |

**The after-hours trigger is activity-based, not time-based.** It keys off
user session inactivity (no voice/text/web interaction for N minutes), not
time of day. This is simpler and more robust than time-based rules. The
user can explicitly grant a "I'll be away for 2 hours, keep working" window
via CLI if needed.

### Why Not Just Use the Red-Team Engine?

The existing red-team engine (`services/red_teaming/engine.py`) is HORI
evaluating itself — the LLM calls the LLM. A compromised HORI can bypass it.
The tool daemon validates individual calls — is this path allowed? is this
tool in the registry? — but it can't see patterns. The Sherpa sees the
trajectory and sits outside both. Neither HORI nor the tool daemon can
influence it.

### Simplicity as Reliability

The Sherpa is deliberately simple: a few hundred lines of Go, no LLM calls,
no complex logic. Pattern matching on the audit log. Simplicity = reliability.
The more complex the guardian, the more likely it has its own bugs — and a
buggy guardian is worse than no guardian.



The manifesto calls for "self-healing" and "adaptive autonomy." This is
compatible with the safety architecture:

1. **Watchdog detects service down** -> HORI attempts restart (side-effect tool,
   but pre-approved for automatic execution with rate limiting)
2. **3 consecutive failures** -> Escalate to user notification, stop retrying
3. **Config corruption detected** -> Restore from backup, log the repair
4. **git pull available** -> Run tests -> if pass, stage update -> user confirms
   -> restart. If tests fail, auto-rollback.

The key: self-healing actions are pre-approved, bounded, and reversible.
Destructive or irreversible actions always require human confirmation.

## Learning from Corrections

When the user corrects HORI ("no, that's wrong, the actual answer is X"):

1. The correction is stored in memory with a "correction" tag
2. During consolidation, corrections are promoted to aios_project tier
3. On related topics, corrections are injected into the prompt context
4. Over time, HORI builds a "things I was wrong about" knowledge base

This is real learning - not weight updates, but persistent memory of mistakes
and their corrections. The LLM doesn't retrain, but it remembers being wrong
and the context is there to prevent repeating the mistake.

## Intellectual Honesty

Beyond just preventing hallucination, we want HORI to be intellectually honest:

- **Express uncertainty**: "I think X but I'm not sure" vs. confident fabrication
- **Ask for clarification**: "Do you mean the local model or the cloud API?"
  instead of guessing
- **Cite sources**: [training], [web search], [memory], [system state]
- **Track calibration**: When she says "I'm sure", how often is she right?

This is a cultural change in the LLM's behavior, driven by:
1. System prompt instructions (reduces frequency)
2. Tool architecture (eliminates fabrication on factual queries)
3. Telemetry (measures the problem so we can see if it's getting better)

## The Order of Operations

The roadmap is structured in tiers (see docs/roadmap.md). The safety spine
(Tier 2, HORI 1.5) must be complete before tools are connected to voice
chat (Tier 3, HORI 1.6). The spine is the irreducible minimum — everything
else is defense-in-depth that earns its place in HORI 2.0.

1. **Tier 1 (HORI 1.0):** Close security holes, finish loose ends
2. **Tier 2 (HORI 1.5):** Build the safety spine — system hardening, tool
   registry, read-only tools, guard rails, the Sherpa, adversarial tests
3. **Tier 3 (HORI 1.6):** Connect read-only tools to voice chat. Run for
   2 weeks with zero unmitigated safety incidents (concrete metrics in
   roadmap.md)
4. **Tier 4 (HORI 2.0):** Add side-effect tools, shell, APIs, taint
   tracking, credential brokerage, self-healing, and the full mind-body-
   soul loop — progressively, with each layer justified against the
   complexity it adds

**The test for each additional layer in HORI 2.0:** does it close a gap
that the spine leaves open, or does it add a new interface that creates
new gaps? If you can't answer that specifically, don't add it.

## What This Means for the Manifesto

The manifesto's "Self-Healing & Adaptive Autonomy" pillar is not abandoned -
it's made real. The difference is:

- **Before**: "HORI can self-heal" (trust the LLM to do the right thing)
- **After**: "HORI can self-heal within guardrails, with audit trails, and
  human confirmation for anything dangerous" (trust the architecture)

The "Continuous Learning" pillar is also made real through correction memory:
not weight updates, but persistent memory of mistakes and corrections that
shapes future responses.

This is not limiting HORI - it's making it trustworthy enough to actually
have capabilities. A hallucinating AI with no tools is annoying. A
hallucinating AI with filesystem access is dangerous. A properly architected
AI with tools, guardrails, and audit trails is genuinely useful.
