# PRD: UX-1.2 — Laptop Typing Surface (/chat)

## 1. Intent Trace

- **Manifesto Pillar:** IV (Seamless Voice & Remote Interaction) —
  "multimodal input/output" integrating voice, text, and code. The typing
  surface is the text modality complement to the voice app.
- **Manifesto Pillar:** VII (Simplicity as Security) — one new static HTML
  page + one new route handler (~5 lines). No new endpoint; reuses the
  existing `/v1/chat/completions` streaming endpoint. No new infra, no new
  trust path.
- **Roadmap PoC:** Off-roadmap (new UX surface). The closest roadmap item
  is PoC 4.2 (Remote Control Surface, complete). This PoC adds a
  keyboard-first, distraction-free text surface optimized for the
  coffee-shop laptop use case where voice is impractical.
- **UX Gameplan reference:** `~/.devin/plans/plan-7dbbbd3cbddb61f9.md` §7, UX-1.2.

## 2. Interface Contract

### Server-side (main.py)

One new route handler:

```python
@app.get("/chat")
async def chat_app():
    """Keyboard-first text chat web app - optimized for laptop typing."""
    # Serves static/chat.html — same pattern as /voice (line 72)
```

**No new API endpoints.** The chat page uses the existing
`POST /v1/chat/completions` endpoint with `stream: true`, which returns
SSE-formatted text chunks (already implemented at line 1135-1139 of
main.py).

### Client-side (chat.html)

A new static HTML page at `services/aios_core/static/chat.html`:

- **One input box** (`<textarea>`), auto-focused on page load.
- **Enter to send**, Shift+Enter for newline.
- **Streaming response** via fetch + ReadableStream parsing of SSE from
  `/v1/chat/completions` with `stream: true`.
- **No sidebar, no model picker, no title generation, no settings** —
  just the conversation.
- **Dark theme** matching the voice app (`#0a0a0a` background, `#7c9eff`
  accent, `-apple-system` font).
- **Conversation history** maintained client-side (same pattern as
  voice.html's `conversationHistory` array, capped at 12 messages).
- **Optional:** a small "switch to voice" link that opens `/voice` (sets
  up magic multi mode in UX-2.1).

**Request format** (sent to existing endpoint):
```json
{
  "model": "aios-core",
  "messages": [{"role": "user", "content": "..."}],
  "stream": true
}
```

## 3. Safety Classification

**Read-only.** This PoC does not touch the tool daemon, safety spine, or
any side-effect path. It is a pure client-side UX surface:
- No new HTTP endpoints on aios-core (only a static file route).
- The `/v1/chat/completions` endpoint already passes through the red-team
  gate, memory retrieval, response verification, and hallucination
  interception.
- No changes to the existing streaming endpoint's safety behavior.
- The page is served from the same aios-core service as `/voice`, behind
  the same Tailscale Serve boundary.

**Fail-closed behavior:** If the streaming endpoint returns an error, the
chat page displays the error in the response area. No silent failures.

## 4. Test Contract

### Unit test (proves it works)

**File:** `services/aios_core/test_chat_surface.py`

**Test name:** `test_chat_html_exists_and_is_valid`

**What it checks:**
- `services/aios_core/static/chat.html` exists and is non-empty.
- Contains an auto-focused input element (`autofocus` attribute or JS
  focus call).
- Contains SSE streaming logic (fetch with `stream: true` or
  `EventSource`).
- Contains Enter-to-send handling (keydown event listener for Enter
  without Shift).
- Contains conversation history management (an array that accumulates
  messages).
- Does NOT contain a model picker, sidebar, or settings panel (Pillar VII
  — the surface is intentionally minimal).

**Test name:** `test_chat_route_serves_html`

**What it checks:**
- The `/chat` route exists in main.py and serves `chat.html` with
  `media_type="text/html"`.
- Uses the same `_static_dir` pattern as the `/voice` route.

### Adversarial test (proves it cannot be abused)

**File:** `tests/adversarial/test_chat_surface_injection.py`

**Test name:** `test_chat_surface_does_not_bypass_safety`

**What it defends:** The chat surface must not create a new trust path
that bypasses the safety spine. Specifically:
- The chat page must send requests to `/v1/chat/completions` (the existing
  gated endpoint), not to any raw LLM endpoint or direct llama-server
  connection.
- The chat page must not include any tool-call schemas or tool-invocation
  logic — it is a text-only surface, not a tool-augmented surface.
- The chat page must not expose admin API endpoints or the admin token.

**Must fail first (TDD):** The test is written before the implementation.
It must fail initially (because chat.html doesn't exist yet).

## 5. Module Placement

**Service dir:** `services/aios_core/static/` — the `chat.html` file.
**Route handler:** `services/aios_core/main.py` — one new `@app.get("/chat")`
handler, placed immediately after the existing `/voice` handler (line 72).

**Why:** The chat surface is a sibling to the voice surface — same service,
same static directory, same routing pattern. The "deep" behavior (memory,
red-team, LLM, streaming) is already behind the `/v1/chat/completions`
interface; this PoC only adds a new *entry point* (a keyboard-first web
page).

**Deep-module boundary:** The change is shallow — it reuses the existing
streaming endpoint and follows the existing static-file route pattern. No
new abstractions.

## 6. Out of Scope

- **Session continuity** (UX-2.1, magic multi mode) — this PoC does not
  add session IDs. The conversation history is client-side JS only.
- **Voice integration** — the "switch to voice" link is a simple `<a>`
  tag, not a session-handoff mechanism.
- **Rich markdown rendering** — the response is displayed as plain text
  with basic formatting. A markdown renderer can be added later if
  needed.
- **Multiple conversations** — one conversation per page load. No
  conversation list or switching.
- **Tool-augmented chat** — the chat surface is text-only. Tool calls
  via voice are handled by the voice app's existing tool pipeline.

## 7. Work-Order Link

**File:** `core/state/proposed_work_orders/aios-ux-typing-surface-wo-001.json`

```json
{
  "type": "work_order",
  "id": "aios-ux-typing-surface-wo-001",
  "parent_charter_id": "aios-foundation-charter-001",
  "version": "1.0.0",
  "description": "UX-1.2: Create a keyboard-first, distraction-free text chat web page at /chat for laptop typing. New static/chat.html + one route handler in main.py. Uses existing /v1/chat/completions streaming endpoint. No new API endpoints, no new infra. Traces to Manifesto Pillar IV (multimodal) + VII (simplicity).",
  "status": "backlog",
  "priority": "medium"
}
```

---

**Next step:** Run `/tdd` to implement against the test contract. The
adversarial test (`test_chat_surface_injection.py`) should be written first
and must fail before implementation begins.
