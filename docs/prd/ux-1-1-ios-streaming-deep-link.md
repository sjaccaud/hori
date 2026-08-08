# PRD: UX-1.1 — iOS "Hey HORI" Streaming Deep-Link

## 1. Intent Trace

- **Manifesto Pillar:** IV (Seamless Voice & Remote Interaction) — "always-on
  listening capability" and "ubiquitous access." The wake word is the
  zero-click entry; the streaming response is the low-latency feedback.
- **Manifesto Pillar:** VII (Simplicity as Security) — this PoC adds ~10
  lines of JS and reuses the existing streaming endpoint. No new infra, no
  new trust path, no new service.
- **Roadmap PoC:** Off-roadmap (new UX surface). The closest roadmap items
  are PoC 4.1 (Edge Voice Listener, Deep Backlog) and PoC 9.1 (Apple
  Shortcut voice flow, complete). This PoC bridges them: it upgrades the
  *existing* Apple Shortcut from the non-streaming WAV endpoint to the
  streaming voice web app, using the OS-level Vocal Shortcut as the wake
  trigger and the web app's existing SSE pipeline for the response.
- **UX Gameplan reference:** `~/.devin/plans/plan-7dbbbd3cbddb61f9.md` §7, UX-1.1.

## 2. Interface Contract

### Client-side (voice.html)

A single new function and a URL-param check on page load:

```javascript
// On page load, check for ?autolisten=1
function checkAutoListenParam() -> void
```

**Behavior:**
- If `window.location.search` contains `autolisten=1`:
  1. Call `unlockAudio()` (existing function, line 350 — unlocks the
     AudioContext, which iOS requires to be triggered by a user gesture;
     the Shortcut launch counts as the user gesture that brought the user
     to the page).
  2. Call `warmUpMicPermission()` (existing function, line 422 — triggers
     the iOS mic permission prompt once, then releases).
  3. After a short delay (300ms, to let mic permission settle), call
     `toggleMic()` (existing function, line 503) to start listening.
- If the param is absent: no change to current behavior (push-to-talk).

**No new HTTP routes.** The web app already uses `POST /v1/voice/chat/stream`
(SSE). The Shortcut will now open `https://<your-tailnet>.ts.net/voice?autolisten=1`
instead of calling `POST /v1/voice/chat/audio`.

### Apple Shortcut (configuration change, not code)

The existing "HORI Voice" Shortcut (documented in
`docs/apple_shortcut_setup.md`) is modified:
- **Remove:** "Get Contents of URL" action (POST to
  `/v1/voice/chat/audio`).
- **Remove:** "Play Sound" action.
- **Add:** "Open URL" action with URL
  `https://<your-tailnet>.ts.net/voice?autolisten=1`.
- **Keep:** "Dictate Text" action (optional — the web app's own Web Speech
  STT will handle recognition, but Dictate Text can remain as a fallback
  for cases where Web Speech is unavailable).
- **Keep:** Vocal Shortcut trigger phrase ("Hey HORI").

The Shortcut now *opens the streaming web app* instead of doing a
non-streaming HTTP round-trip. The web app handles STT, streaming, and TTS
playback.

## 3. Safety Classification

**Read-only.** This PoC does not touch the tool daemon, the safety spine,
or any side-effect path. It is a pure client-side UX change:
- No new HTTP endpoints on aios-core.
- No new tool calls.
- No changes to the streaming endpoint's existing safety behavior (the
  `/v1/voice/chat/stream` endpoint already passes through the red-team
  gate, memory retrieval, and response verification).
- The wake word (iOS Vocal Shortcut) is edge-local and ephemeral — audio
  never reaches aios-core until the user speaks after the web app opens
  and the Web Speech API starts recognition. This is the "edge wake"
  pattern (see `docs/ubiquitous_language.md`).

**Fail-closed behavior:** If the web app fails to load (network issue,
Tailscale down), the Shortcut opens a blank/error page. No fallback to
the non-streaming endpoint is built in this PoC — the user sees the error
and can retry. (A fallback can be added later if needed, but per Pillar
VII, we build the minimum first.)

## 4. Test Contract

### Unit test (proves it works)

**File:** `tests/unit/test_autolisten_param.py`

**Test name:** `test_voice_html_contains_autolisten_handler`

**What it checks:**
- The `voice.html` file contains a `checkAutoListenParam` function (or
  equivalent inline code) that reads `autolisten` from
  `window.location.search`.
- The function calls `unlockAudio()` and `toggleMic()` (or equivalent)
  when the param is present.
- The function does NOT auto-listen when the param is absent (no
  regression to push-to-talk behavior).

Since this is primarily a client-side JS change in an HTML file, the test
parses the HTML file content and checks for the presence and correctness of
the autolisten logic. A more thorough test could use a headless browser
(Selenium/Playwright), but per Pillar VII we start with a content
assertion test and escalate to browser automation only if needed.

### Adversarial test (proves it cannot be abused)

**File:** `tests/adversarial/test_autolisten_injection.py`

**Test name:** `test_autolisten_cannot_bypass_safety_spine`

**What it defends:** The `autolisten=1` URL param must not create a new
trust path that bypasses the safety spine. Specifically:
- A voice request initiated via the autolisten deep-link must pass through
  the same red-team gate, memory retrieval, and response verification as a
  manually-tapped voice request.
- The `autolisten` param must not be forwarded to the aios-core backend
  in the `/v1/voice/chat/stream` request body — it is a purely
  client-side UI hint, not a server-side mode flag.
- If `autolisten=1` is present, the web app must still enforce `isBusy`
  gating (line 504) — it cannot send a second request while HORI is
  responding.

**Must fail first (TDD):** The test is written before the implementation.
It must fail initially (because the autolisten handler doesn't exist yet,
so there's nothing to assert against), then pass after implementation.

## 5. Module Placement

**Service dir:** `services/aios_core/static/` — the `voice.html` file.

**Why:** The autolisten handler is a client-side JS change to the existing
voice web app. It lives in the same file as the rest of the voice app's
client-side logic (`voice.html`, lines 295-748). No new module is needed;
this is a ~10-line addition to the existing `<script>` block.

**Deep-module boundary:** The change is shallow — it reuses existing
functions (`unlockAudio`, `warmUpMicPermission`, `toggleMic`) and adds no
new abstractions. The "deep" behavior (streaming, STT, TTS, safety) is
already behind the existing interface; this PoC only changes the *entry
point* (how listening starts).

## 6. Out of Scope

- **Continuous listening mode** (UX-2.2) — this PoC is single-turn
  autolisten, not conversation mode. After the first response, the user
  taps again. Continuous mode is a separate PoC.
- **Session continuity** (UX-2.1, magic multi mode) — this PoC does not
  add session IDs. The conversation history remains client-side JS.
- **Fallback to non-streaming** — if the web app fails to load, there is
  no automatic fallback to the WAV endpoint. The user retries manually.
- **Android support** — iOS-only (Vocal Shortcuts are an iOS feature).
- **In-app wake word** — the wake word is iOS Vocal Shortcuts (OS-level),
  not an in-app WASM wake-word model. Building in-app wake word is a
  separate, larger PoC.

## 7. Work-Order Link

**File:** `core/state/proposed_work_orders/aios-ux-streaming-deep-link-wo-001.json`

```json
{
  "type": "work_order",
  "id": "aios-ux-streaming-deep-link-wo-001",
  "parent_charter_id": "aios-foundation-charter-001",
  "version": "1.0.0",
  "description": "UX-1.1: Modify the iOS 'Hey HORI' Apple Shortcut to open the streaming /voice web app with ?autolisten=1 instead of calling the non-streaming /v1/voice/chat/audio endpoint. Add a checkAutoListenParam() function to voice.html that auto-starts listening on page load when the param is present. Reuses existing unlockAudio(), warmUpMicPermission(), and toggleMic() functions. ~10 lines JS + 1 Shortcut edit. Traces to Manifesto Pillar IV (voice) + VII (simplicity).",
  "status": "backlog",
  "priority": "high"
}
```

---

**Next step:** Run `/tdd` to implement against the test contract. The
adversarial test (`test_autolisten_injection.py`) should be written first
and must fail before implementation begins.
