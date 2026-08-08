# HORI Voice Shortcut Setup (iOS 26+)

This guide walks you through creating an Apple Shortcut that lets you talk to HORI from your iPhone using Vocal Shortcuts.

## Prerequisites

- HORI running on your server (aios-core on :5680)
- Your iPhone on the same Tailscale network as the server
- Server Tailscale IP: `<your-tailscale-ip>`
- iOS 26 or later

## How It Works

1. You build a shortcut in the **Shortcuts** app that:
   - Captures your speech (via Dictate Text or keyboard dictation)
   - Sends it to HORI via HTTP POST
   - Plays the audio response
2. You assign a **Vocal Shortcut** trigger phrase (e.g., "Hey HORI") in Settings
3. You just say "Hey HORI" anytime - no Siri needed, works even when locked

---

## Step 1: Create the Shortcut

1. Open the **Shortcuts** app
2. Tap **+** (top right) to create a new shortcut
3. Name it **"HORI Voice"** (tap the title at top)

### Action 1: Capture Your Speech

You have two options here:

**Option A: Dictate Text action** (if available)
- Tap **Add Action** (or the search bar)
- Search for: `dictate`
- Look for **"Dictate Text"** (under Text category)
- If found: add it, set Language to English, Stop Listening to "After Pause"
- This listens to your voice and converts to text automatically

**Option B: Ask for Input + keyboard dictation** (fallback)
- Tap **Add Action**
- Search for: `ask`
- Add **"Ask for Input"**
- Type: Text
- Prompt: "Ask HORI anything"
- When this runs, tap the **microphone icon** on the keyboard to dictate
- (This uses iOS's built-in keyboard dictation - always available)

**Option C: Transcribe Audio** (for pre-recorded audio)
- Tap **Add Action**
- Search for: `transcribe`
- Add **"Transcribe Audio"**
- This works on audio files, not real-time - less useful for voice chat

### Action 2: Send to HORI

You have two options:

**Option A: Streaming deep-link with wake (recommended — UX-1.1)**

This opens the streaming voice web app, which gives you first audio in
~1-2s instead of waiting for the full WAV response (~5-8s). The web app
handles STT, streaming, and TTS playback. The wake call ensures the page
re-arms listening even if Safari already has the tab open.

**Action 2a: Wake the voice page**
- Tap **Add Action** (or + at the bottom)
- Search for: `get contents` or `url`
- Add **"Get Contents of URL"**
- URL: `https://<your-tailnet>.ts.net/v1/wake`
- Method: **POST** (tap the method and change from GET)
- This hits the wake endpoint so the voice page (if already open) polls
  it and re-arms listening. If the page isn't open yet, the wake is
  harmless — it just sets a timestamp.

**Action 2b: Open the voice page**
- Tap **+** at the bottom
- Search for: `open` or `open url`
- Add **"Open URL"**
- URL: `https://<your-tailnet>.ts.net/voice?autolisten=1`
- This opens the voice app and auto-starts listening (the `?autolisten=1`
  param triggers the `checkAutoListenParam()` handler on page load)
- You can skip Action 3 (Play the Response) — the web app handles audio
  playback via Web Audio API with streaming SSE
- **After the first response, the mic auto-re-arms** — you can keep
  talking without saying "Hey HORI" again. Say "Hey HORI" again only
  when you want to start a new conversation after a pause.

**Option B: Non-streaming WAV (original — fallback)**

This sends text directly and gets back a WAV file. Slower (~5-8s) but
works without opening a browser tab.

- Tap **Add Action** (or + at the bottom)
- Search for: `url` or `get contents`
- Add **"Get Contents of URL"**
- Configure:
  - URL: `http://<your-tailscale-ip>:5680/v1/voice/chat/audio`
  - Method: **POST** (tap the method and change from GET)
  - Headers: tap "Add new header"
    - Key: `Content-Type`
    - Value: `application/json`
  - Request Body: tap "Add new field"
    - Type: **JSON**
    - Content: `{"text": "<your text variable>"}`
    - (Insert the variable from Action 1 - tap where it says "text" and select the output from Dictate Text or Ask for Input)
- This sends your text to HORI and gets back a WAV audio file

### Action 3: Play the Response (only needed for Option B above)

- Tap **+** at the bottom
- Search for: `play`
- Add **"Play Sound"**
- Tap the input and select the file from "Get Contents of URL"
- This plays HORI's voice response through your speaker/earbuds

---

## Step 2: Assign a Vocal Shortcut Trigger

1. Open **Settings** app
2. Go to **Accessibility** -> **Vocal Shortcuts** (in the Speech section)
3. Tap **Set Up Vocal Shortcuts** (if first time)
4. Tap **Add Action**
5. Choose **Shortcut** -> select **"HORI Voice"**
6. **Train the phrase**: Say "Hey HORI" (or whatever you want) 3-5 times
7. Done!

---

## Using It

- Just say **"Hey HORI"** anytime - no need to say "Siri" first
- Your phone listens for the phrase even when locked
- It captures your question, sends it to HORI, and plays the audio response
- Works over Tailscale from anywhere (5G/LTE counts if Tailscale is connected)

---

## Alternative: Use Apple Intelligence + HORI

iOS 26 has a new **"Use Model"** action that taps into Apple Intelligence. You can use this as a fallback when you don't have network access to your HORI server:

1. Add **"Dictate Text"** or **"Ask for Input"**
2. Add **"Use Model"** (choose On-Device or Cloud)
3. Enter your prompt with the dictated text variable
4. Add **"Speak Text"** to read the response aloud

This runs entirely on your phone - no server needed. But it doesn't have your HORI memory, codebase awareness, or web search.

---

## Testing

After setup, test with:
- "Hey HORI" ... "What is HORI about?"
- "Hey HORI" ... "How is the system doing?"
- "Hey HORI" ... "What's the latest in AI news?"
- "Hey HORI" ... "Is Qwen 3.8 worth looking at?"

---

## Troubleshooting

- **Can't find "Dictate Text"**: Search for "dictate" or look under the Text category. If still not found, use "Ask for Input" and tap the microphone icon on the keyboard to dictate.
- **Connection refused**: Make sure Tailscale is connected on your phone. Check the IP: `http://<your-tailscale-ip>:5680/health` should return `{"status":"ok"}` in Safari.
- **No audio response**: Check that TTS voices are available on the server: `curl http://<your-tailscale-ip>:5680/v1/audio/voices`
- **Slow response**: First call may take 2-3s (model warmup), subsequent calls faster
- **Web search questions**: May take 10-30s (search + fetch + summarize)
- **Vocal Shortcut not triggering**: Retrain the phrase in Settings -> Accessibility -> Vocal Shortcuts
- **Audio quality**: Default voice is `bf_emma` (British female). For other voices, see `GET /v1/audio/voices`. For a male voice, try `"voice": "bm_george"`.
- **Shortcut aborts on "No"**: Known iOS 26.1 bug with "Ask for Input" - try re-creating the action

---

## API Reference

- `POST /v1/voice/chat/stream` - Streaming SSE (text + audio chunks as they
  generate). Used by the web voice app. First audio in ~1-2s for memory
  queries, ~10s for fresh web searches. Not compatible with Apple Shortcuts
  (Shortcuts can't parse SSE).
- `POST /v1/voice/chat` - Returns JSON with text + base64 audio (non-streaming)
- `POST /v1/voice/chat/audio` - Returns raw WAV audio directly (recommended for
  Apple Shortcuts)
- `POST /v1/audio/speech` - OpenAI-compatible TTS (text in, WAV out)
- `GET /v1/audio/voices` - List available TTS voices
- `GET /health` - Health check

## Latency (Aug 2026, after Kokoro TTS migration)

- TTS synthesis: ~0.7s per sentence (bf_emma voice, Kokoro-82M on CPU, 6x realtime)
- Chat response (non-streaming): 2-3s without web search, 10-16s with
- Streaming first audio: 1-2s without web search, 10s with fresh web search
- Web search (cached): 1-2s (5-minute TTL cache)
- Vocal Shortcut trigger: ~0.5s (on-device, no Siri round-trip)

Note: Apple Shortcuts use the non-streaming `/v1/voice/chat/audio` endpoint
because Shortcuts can't parse SSE streams. The web voice app at
`http://<your-tailscale-ip>:5680/voice.html` uses the streaming endpoint for
lower perceived latency.
