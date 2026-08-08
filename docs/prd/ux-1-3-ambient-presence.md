# PRD: UX-1.3 — Ambient Presence (Breathing Icon + Opt-In Chime)

## 1. Intent Trace

- **Manifesto Pillar:** V (Self-Healing & Adaptive Autonomy) — makes
  HORI's autonomous life (consolidation, opportunity agent, Sherpa)
  *visible* to the user, which is also a safety property: the user can
  see the system is alive and behaving.
- **Manifesto Pillar:** IV (Seamless Voice & Remote Interaction) —
  presence is the "is HORI even there?" vocabulary; the breathing icon
  and chime are the sensory feedback (juice) for the idle state.
- **Manifesto Pillar:** VII (Simplicity as Security) — one new SSE
  endpoint + an in-memory state enum + a chime audio asset. Default
  posture: silent. Chime is opt-in. No new services, no new trust paths.
- **Roadmap PoC:** Off-roadmap (new UX surface). The closest roadmap
  item is PoC 10.3 (Notification Pipeline, complete). This PoC adds a
  *visual + optional in-ear* channel alongside the existing Telegram/HA/ntfy
  push channels.
- **UX Gameplan reference:** `~/.devin/plans/plan-7dbbbd3cbddb61f9.md` §7, UX-1.3.

## 2. Interface Contract

### Server-side (main.py)

One new SSE endpoint:

```python
@app.get("/v1/presence")
async def presence_stream():
    """SSE stream of HORI presence state changes.
    Emits: event: state, data: {"state": "idle"|"thinking"|"has_nudge"}
    """
```

**Presence states** (enum, in-memory):
- `idle` — HORI is awake but not doing anything (dim, slow breath)
- `thinking` — HORI is processing a request (breathing pulse)
- `has_nudge` — HORI has a proactive nudge (gentle glow + optional chime)

**State transitions** are set by:
- `idle` → `thinking`: when a `/v1/chat/completions` or `/v1/voice/chat`
  request starts.
- `thinking` → `idle`: when the response completes.
- `idle` → `has_nudge`: when the proactive agent files a new proposal
  (wired via a simple in-memory call from the notifier, or a state file
  check).
- `has_nudge` → `idle`: when the user acknowledges (sends any message).

**Rate limiting (Pillar VII):** ≤1 state change per second, ≤1 chime per
hour unless user-acknowledged. The SSE stream debounces rapid transitions.

### Client-side (voice.html + chat.html)

- The existing status dot (`#status-dot` in voice.html, the header in
  chat.html) subscribes to `/v1/presence` via `EventSource`.
- Three CSS states: `idle` (dim, slow breath animation), `thinking`
  (breathing pulse — reuses the existing `@keyframes pulse`), `has_nudge`
  (gentle glow).
- **Chime:** an opt-in setting (stored in `localStorage`). When
  `has_nudge` is received and the chime is enabled, play a short audio
  cue via the existing AudioContext (voice.html) or a new Audio element
  (chat.html). Default: OFF.

### Proactive agent wiring (notifier.py)

The existing `notify_proposals()` function (line 169 of notifier.py) is
extended to also set the presence state to `has_nudge` via a simple
in-memory call or a state file. This is a ~3-line addition; the notifier
already runs in the same process as aios-core (it's called from
`opportunity_proposer.run_proposer()`).

## 3. Safety Classification

**Read-only.** The presence endpoint is a pure output stream — it emits
state changes, it does not accept commands. No tool calls, no side
effects, no safety spine interaction.

**Privacy consideration:** The presence stream reveals *that* HORI is
thinking or has a nudge, but not *what* it's thinking or *what* the nudge
is. The nudge content is only delivered via the existing Telegram/HA/ntfy
channels (which require credentials). The presence stream is
content-free: `{"state": "has_nudge"}` with no payload.

**Fail-closed behavior:** If the SSE stream fails (network issue), the
status dot falls back to the existing health-check behavior (the
`checkHealth()` function at line 402 of voice.html already polls
`/health` every 30s). Presence is additive, not load-bearing.

## 4. Test Contract

### Unit test (proves it works)

**File:** `services/aios_core/test_presence.py`

**Test name:** `test_presence_endpoint_exists`

**What it checks:**
- main.py contains a `/v1/presence` route handler.
- The handler returns `StreamingResponse` with `text/event-stream`.
- The presence state enum has exactly three states: `idle`, `thinking`,
  `has_nudge`.
- State transitions are rate-limited (≤1/sec).

**Test name:** `test_presence_state_transitions`

**What it checks:**
- The presence state manager correctly transitions: idle→thinking on
  request start, thinking→idle on response complete, idle→has_nudge on
  proactive agent notification.
- Invalid transitions are ignored (e.g., thinking→has_nudge is not
  allowed — a nudge while thinking is queued, not emitted immediately).

### Adversarial test (proves it cannot be abused)

**File:** `tests/adversarial/test_presence_leak.py`

**Test name:** `test_presence_stream_does_not_leak_content`

**What it defends:** The presence stream must not leak:
- The content of user messages or HORI responses.
- The content of proactive nudges or work orders.
- Tool call details, audit log entries, or safety event data.
- Admin tokens or credentials.

The stream emits *state only* (`{"state": "idle"|"thinking"|"has_nudge"}`),
never content. This is a privacy property: a compromised client
subscribing to the presence stream cannot reconstruct conversations or
nudge content.

**Must fail first (TDD):** Written before implementation, must fail
initially.

## 5. Module Placement

**Service dir:** `services/aios_core/` — the presence state manager and
SSE endpoint live in `main.py` (following the existing pattern where
`/v1/voice/chat/stream` is also in main.py).

**Client-side:** `services/aios_core/static/voice.html` and
`services/aios_core/static/chat.html` — the EventSource subscriber and
CSS animations are added to both pages.

**Proactive agent:** `services/proactive_agent/notifier.py` — a ~3-line
addition to `notify_proposals()` to set the presence state.

**Why:** The presence system is a thin layer over the existing aios-core
service. It doesn't need its own module — it's a state enum + an SSE
endpoint + a few CSS classes. Per Pillar VII, the irreducible minimum is
the spine; everything else must earn its place. This PoC earns its place
by making HORI's autonomous life visible (Pillar V) without adding a new
service or trust path.

## 6. Out of Scope

- **Whispered nudges** (UX-3.2) — the chime is a simple audio cue, not a
  spoken sentence. Whispered diegetic nudges are Tier-3, post-gate.
- **Urgency classification** — all nudges are treated equally in this
  PoC. The chime fires for any `has_nudge` state. Urgency-based
  differentiation is deferred to UX-3.1.
- **Earbud detection** — the chime is opt-in via `localStorage`, not
  auto-detected from earbud connection state. Auto-detection is a future
  enhancement.
- **Sherpa state visibility** — the Sherpa's capability level is not
  surfaced in the presence stream (it's a safety-internal state). Only
  user-facing states (idle, thinking, has_nudge) are emitted.
- **Consolidation/dream visibility** — the Sleep & Dream consolidation
  cycle is not surfaced as a distinct presence state. It could be added
  later as a `dreaming` state, but for now it falls under `idle`.

## 7. Work-Order Link

**File:** `core/state/proposed_work_orders/aios-ux-ambient-presence-wo-001.json`

```json
{
  "type": "work_order",
  "id": "aios-ux-ambient-presence-wo-001",
  "parent_charter_id": "aios-foundation-charter-001",
  "version": "1.0.0",
  "description": "UX-1.3: Add /v1/presence SSE endpoint to aios-core emitting state changes (idle, thinking, has_nudge). Add breathing status icon to voice.html and chat.html. Add opt-in earbud chime (localStorage, default off). Wire proactive agent notifier to set has_nudge state. Traces to Manifesto Pillar V (visible autonomy) + IV (presence) + VII (silent by default).",
  "status": "backlog",
  "priority": "medium"
}
```

---

**Next step:** Run `/tdd` to implement against the test contract. The
adversarial test (`test_presence_leak.py`) should be written first and
must fail before implementation begins.
