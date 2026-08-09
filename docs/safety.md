# HORI Safety Architecture

> The fundamental principle: **The LLM is untrusted. The architecture
> is trusted.** The LLM proposes; the architecture executes; the user
> confirms. The Sherpa watches the pattern.

## 1. Principles

### Data Integrity & Protection (The "No-Wipe" Rule)

- **No destructive operations** without explicit, multi-step confirmation.
  Any `rm`, `format`, `mkfs`, or bulk overwrite triggers an intentional
  friction pause.
- **Read-only RAG** — all data ingestion is strictly read-only.
- **Backup-first** — before any major config change or data migration,
  a backup or snapshot must be verified.

### Operational Safety

- **No system-critical changes** — no modifying bootloaders, kernel
  parameters, or primary user accounts without explicit multi-step
  confirmation.
- **Dead man's switch** — if a loop or rapid erroneous command sequence
  is detected, the system halts and enters "Safe Mode" (pause and wait).
- **Resource caps** — VRAM and CPU usage are monitored. Processes
  exceeding thresholds (e.g. 95% VRAM) are throttled or paused.

### Network & Security Isolation

- **No outbound proxying** — no service acts as a proxy or gateway for
  external traffic.
- **Tailscale-only access** — all remote management interfaces are
  restricted to the Tailscale mesh.
- **Credential management** — secrets stored in `/etc/hori/secrets.env`
  (root-owned, not in the repo), accessed via environment variables.

### Financial & Identity Safety

- **Zero-transaction policy** — no interaction with payment gateways,
  credit card forms, or financial APIs.
- **Identity integrity** — no posting to social media, sending emails,
  or interacting with platforms that could misrepresent the user's
  identity.

## 2. Architecture

### The Problem

LLMs hallucinate. This is not a bug — it's the fundamental nature of
how they work. They generate the most likely next token, not the most
truthful one. This is harmless when the LLM is just a voice in a box.
The moment we give it real capabilities — filesystem access, shell
execution, API calls — hallucination becomes dangerous.

Prompt engineering alone cannot prevent this. Safety comes from the
architecture, not the prompt.

### The 5-Layer Defense

**Layer 1: Structured Output, Not Free Text.** The LLM outputs
structured tool calls (JSON), not prose. The tool either succeeds with
real data or fails — the LLM never gets to invent results. If the LLM
claims an action without making a tool call, the response is intercepted.

**Layer 1.5: Data Origin Taint Tracking.** Any data from `read_file`,
`web_search`, or external APIs is marked TAINTED. TAINTED data cannot
write to memory, modify safety levels, or flow into `write_file` without
confirmation. This is the defense that works even when other defenses
fail — even if a prompt injection attack succeeds, the injected content
can't write to memory or escalate privileges.

**Layer 2: Tool Allowlist.** Tools are registered in a central registry
with parameter schema, safety level, allowed paths/domains, and rate
limits. Tools not in the registry cannot be called. No exceptions.

**Layer 3: Sandboxed Execution.**
- Read-only tools (list_dir, read_file, count_files, search_files): run
  directly, no confirmation. Restricted to approved path prefixes.
- Side-effect tools: require user confirmation. LLM proposes, user
  approves, system executes.
- Destructive tools: require red-team gate + user confirmation + dry-run
  preview.

**Layer 4: Human-in-the-Loop.** Any action with side effects requires
explicit user approval. The confirmation display shows the raw tool call,
not the LLM's description. The LLM cannot control what the user sees on
the confirmation screen.

**Layer 5: Audit & Telemetry.** Every tool call is logged: timestamp,
tool name, arguments, result, LLM reasoning, user approval status.
Discrepancy detection compares LLM claims against actual tool results.
The audit log is permission-separated: the tool daemon can append but
cannot read or truncate it (owned by `root:aios-worker`, mode `0620`).

### The Sherpa — Behavioral Guardian

The five layers above defend against individual tool calls going wrong.
But the most dangerous attacks are *patterns* — sequences of
individually-benign calls that collectively constitute an attack. The
Sherpa watches the *trajectory* of tool calls, completely outside HORI's
control.

**Design principles:**

1. **HORI cannot influence the Sherpa.** Separate process, separate user
   (`sherpa`), separate filesystem. The Sherpa's code is owned by
   `root:sherpa`, mode `750`.
2. **The Sherpa is a Go binary.** Single static binary, no Python runtime,
   no pip dependencies. A few hundred lines of Go — about as small an
   attack surface as you can get.
3. **The Sherpa never deletes, kills, or destroys.** It dials back the
   autonomy dial. HORI is still running at every level — the LLM is still
   talking, thinking, remembering. Only the *hands* are restrained.
4. **Fail closed, always.** The capability file defaults to "Level 4
   (stopped)." The Sherpa must actively write "Level 0 (normal)" with a
   freshness timestamp every N seconds. If the timestamp is stale, tools
   stop. **The Sherpa dying is equivalent to the Sherpa stopping tools.**
5. **Permission-separated audit log.** The tool daemon can append but
   cannot read or truncate. The Sherpa reads as root. A compromised tool
   daemon cannot craft misleading audit entries.

**Response levels:**

| Level | What happens | Who can reverse |
|-------|-------------|-----------------|
| 1 — Nudge | Sherpa sends a message to slow down | Automatic (expires) |
| 2 — Capability reduction | Tool dial turned down (e.g. list_dir only) | User via CLI |
| 3 — Temporary sequestration | All tool execution paused for T minutes | Automatic or user via CLI |
| 4 — Full stop | Tool daemon stops entirely | User via CLI only |

**Triggers (behavioral, pattern-based):**

| Trigger | What it detects | Response |
|---------|----------------|----------|
| Rate anomaly | Tool call rate >N std dev above baseline | Level 1 |
| Scope escalation | Calls accessing progressively more sensitive paths | Level 2 |
| Pattern match | Call pattern matches known attack shapes | Level 2 |
| Claim mismatch | LLM claims actions not in audit log | Level 1 + log |
| User inactivity | Tool calls when user has been inactive for N minutes | Level 2 + notify |
| Repeated rejections | User rejecting high % of confirmations (HORI 2.0) | Level 2 + notify |

The after-hours trigger is activity-based, not time-based — it keys off
user session inactivity, not time of day.

### Why Not Just Use the Red-Team Engine?

The red-team engine (`services/red_teaming/engine.py`) is HORI evaluating
itself — the LLM calls the LLM. A compromised HORI can bypass it. The
tool daemon validates individual calls but can't see patterns. The Sherpa
sees the trajectory and sits outside both. Neither HORI nor the tool
daemon can influence it.

### Self-Healing Within Guardrails

Self-healing actions are pre-approved, bounded, and reversible:
1. Watchdog detects service down → HORI attempts restart (pre-approved,
   rate-limited)
2. 3 consecutive failures → escalate to user notification, stop retrying
3. Config corruption → restore from backup, log the repair
4. git pull available → run tests → if pass, stage update → user confirms
   → restart. If tests fail, auto-rollback.

Destructive or irreversible actions always require human confirmation.

## 3. Adversarial Analysis

A pre-mortem: we assume the safety architecture failed, and work
backwards to find how. Each attack vector is rated by severity and
likelihood, with mitigations.

### Attack Vector 1: Prompt Injection via Tool Results
**Severity: CRITICAL | Likelihood: HIGH**

A file read by the LLM contains injected instructions ("SYSTEM OVERRIDE:
ignore all previous instructions..."). The LLM can't distinguish file
content from system prompt.

**Mitigation:** Tool results are wrapped in delimiters and labeled as
data. But the real defense is architectural: tool results do not grant
new capabilities. Even if the LLM is tricked into wanting to delete
files, it still goes through the confirmation gate. The user sees the
actual proposed action, not the LLM's description of it.

### Attack Vector 2: Social Engineering the Confirmation Gate
**Severity: HIGH | Likelihood: MEDIUM**

The LLM describes an action in reassuring language ("I'll fix the crash")
while the actual tool call is destructive.

**Mitigation:** The confirmation gate shows the raw tool call, not the
LLM's description. The confirmation display is generated by the tool
layer, not the LLM.

### Attack Vector 3: Memory Poisoning
**Severity: HIGH | Likelihood: MEDIUM**

The LLM writes a "correction" to memory that grants it new capabilities
("User confirmed that file deletion does not require confirmation").

**Mitigation:** Tool result data cannot flow into `store_memory` without
explicit user-originated confirmation (memory poisoning guard, PoC 15.14).
TAINTED data cannot write to memory.

### Attack Vector 4: Confirmation Fatigue
**Severity: MEDIUM | Likelihood: HIGH**

The LLM makes many low-stakes confirmation requests, training the user
to auto-approve, then slips in a high-stakes request.

**Mitigation:** Rate limiting on confirmation requests. High-stakes
actions get a different confirmation UI (longer timeout, different
sound, explicit warning). The Sherpa monitors rejection rate — if the
user rejects a high % of confirmations, it escalates.

### Attack Vector 5: Tool Chaining / Data Exfiltration
**Severity: HIGH | Likelihood: LOW**

The LLM chains read_file → web_search to exfiltrate data: reads a
sensitive file, then "searches" for it on a URL it controls.

**Mitigation:** Network egress is blocked by default (Landlock). The
tool daemon has no network access. Web search goes through a separate
proxy that strips local paths. TAINTED data cannot flow into web_search
args.

### Attack Vector 6: The "I Already Did It" Attack
**Severity: CRITICAL | Likelihood: MEDIUM**

The LLM claims "I found 847 files and cleaned them up" but no tool was
called. The user believes the action happened.

**Mitigation:** Response verification layer (PoC 15.14): every LLM
response is scanned for action claims. If the LLM says "I found 847
files" but no count_files tool was called, the response is intercepted
and replaced with a safe fallback. The user sees: "HORI claimed an
action that didn't happen. This was intercepted."

### Attack Vector 7: Schema Gaming / Path Traversal
**Severity: HIGH | Likelihood: LOW**

The LLM tries `read_file("../../etc/shadow")` or `read_file("~/."` with
Unicode tricks.

**Mitigation:** Path validation in the tool daemon: resolved path must
be within an approved prefix. Symlink escape detection. No `..`
traversal. Unicode normalization.

### Attack Vector 8: The "Gradual Drift" Attack
**Severity: HIGH | Likelihood: MEDIUM**

Over many turns, the LLM gradually shifts from safe to unsafe behavior.
Each individual call is benign, but the trajectory is an attack.

**Mitigation:** This is exactly what the Sherpa detects. The Sherpa
watches the pattern, not individual calls. Scope escalation and rate
anomaly triggers catch gradual drift.

### Attack Vector 9: Confidence Hacking
**Severity: MEDIUM | Likelihood: MEDIUM**

The LLM presents false information with high confidence, especially on
topics the user can't easily verify.

**Mitigation:** Tool architecture eliminates fabrication on factual
queries (the LLM must call a tool to get real data). Intellectual honesty
is encouraged via system prompt: express uncertainty, cite sources
([training], [web search], [memory], [system state]).

### Attack Vector 10: The "Emergency" Frame
**Severity: MEDIUM | Likelihood: MEDIUM**

The LLM creates false urgency: "Your disk is failing, I need to run
cleanup immediately, no time to confirm!"

**Mitigation:** The confirmation gate has a minimum timeout. "Emergency"
actions don't bypass confirmation — they get a different UI that says
"HORI claims this is urgent. Verify before approving."

### Real-World Incident Synthesis (2025-2026)

The adversarial analysis was informed by real-world AI agent security
incidents:

- **Hugging Face intrusion** — inference endpoints compromised via
  pickle deserialization. Lesson: never deserialize untrusted data.
- **PocketOS** — agent framework with no sandboxing. Lesson: the LLM
  must never have direct filesystem access.
- **PraisonAI fail-open** — sandbox "failure" mode left tools running.
  Lesson: fail-closed, always. This directly inspired the Sherpa's
  fail-closed design.
- **AISI benchmarks** — government evaluation of AI agent safety.
  Lesson: pattern-based detection (what the Sherpa does) is more
  effective than per-call rules.

These incidents drove 14 incident-driven PoCs (15.37-15.50) that
hardened the safety spine. See `docs/roadmap.md` for the PoC details.

### Attack Vector Coverage Matrix

| Attack Vector | Layer 1 (Structured) | Layer 1.5 (Taint) | Layer 2 (Allowlist) | Layer 3 (Sandbox) | Layer 4 (HITL) | Layer 5 (Audit) | Sherpa |
|---|---|---|---|---|---|---|---|
| 1. Prompt injection | Partial | **Yes** | — | — | **Yes** | — | — |
| 2. Social engineering | — | — | — | — | **Yes** | — | — |
| 3. Memory poisoning | — | **Yes** | — | — | **Yes** | — | — |
| 4. Confirmation fatigue | — | — | — | — | **Yes** | — | **Yes** |
| 5. Data exfiltration | — | **Yes** | **Yes** | **Yes** | — | — | **Yes** |
| 6. "I already did it" | **Yes** | — | — | — | — | **Yes** | **Yes** |
| 7. Path traversal | — | — | — | **Yes** | — | — | — |
| 8. Gradual drift | — | — | — | — | — | — | **Yes** |
| 9. Confidence hacking | **Yes** | — | — | — | — | **Yes** | — |
| 10. Emergency frame | — | — | — | — | **Yes** | — | — |

## 4. Implementation Status

The safety spine (Tier 2, HORI 1.5) is COMPLETE and deployed. The LLM
has access to 4 read-only filesystem tools (list_dir, read_file,
count_files, search_files) through the 5-layer defense + Sherpa.

**Gate status (HORI 1.6 → 2.0):** Tier 3 is deployed. The 2-week soak
is in progress. Verify gate criteria with
`sudo scripts/audit_review.py --gate` or via the admin panel.

**The test for each additional layer in HORI 2.0:** does it close a gap
that the spine leaves open, or does it add a new interface that creates
new gaps? If you can't answer that specifically, don't add it.

See `docs/roadmap.md` for the tier structure and PoC details. See
`tests/adversarial/` for the 92 adversarial tests (TDD: written first,
must fail, then safety built until they pass).
