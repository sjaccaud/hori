# PoC: Apple-Native Voice Interaction (Siri/Shortcuts to HORI)

> **SUPERSEDED** - This document describes the original n8n-based voice flow.
> The current voice setup is direct aios-core HTTP (no n8n needed).
> See **docs/apple_shortcut_setup.md** for the current, authoritative guide.
> This file is kept for historical reference only.

This document outlines the original implementation of the "Siri-to-HORI" bridge, enabling voice-driven intent via Apple's native ecosystem.

## 1. Architecture Overview

1.  **Trigger (Apple Device):** A Shortcut on iOS/macOS captures audio $\rightarrow$ Uses native Speech-to-Text $\rightarrow$ Sends `POST` request with text to `n8n`.
2.  **Orchestrator (n8n):** Receives webhook $\rightarrow$ Passes text to LLM $\rightarrow$ LLM parses text into a structured `work_order` (per `core/intent/schema.json`) $\rightarrow$ n8n executes the intent.

---

## 2. Backend: n8n Workflow Blueprint

To implement the backend, create a new workflow in `n8n` with the following nodes:

### Node 1: Webhook
- **HTTP Method:** `POST`
- **Path:** `voice-command`
- **Response Mode:** `When Last Node Finishes`

### Node 2: AI Agent (or LLM Chain)
- **Model:** Use your preferred model (Ollama/Gemini).
- **System Prompt:**
  ```text
  You are the HORI Intent Parser. Your sole job is to convert natural language into a structured JSON object following the HORI Intent Hierarchy Schema.
  
  Specifically, you must output a 'work_order' object.
  
  SCHEMA REFERENCE:
  {
    "type": "work_order",
    "id": "generate-a-uuid",
    "parent_charter_id": "use-the-most-relevant-charter-id-or-default-to-root",
    "version": "1.0.0",
    "description": "the parsed task description",
    "status": "backlog",
    "priority": "medium"
  }

  RULES:
  1. Return ONLY valid JSON.
  2. Do not include any conversational text.
  3. If the user's intent is unclear, set priority to 'low' and description to the raw text.
  
  USER TEXT: {{ $json.body.text }}
  ```

### Node 3: Respond to Webhook
- **Response Body:** `{{ $json.output }}` (The JSON from the AI node).

---

## 3. Frontend: Apple Shortcut Configuration

### For iOS (iPhone/iPad)
1.  Open the **Shortcuts** app.
2.  Create a new Shortcut named **"Hey HORI"**.
3.  Add action: **Dictate Text**.
4.  Add action: **Get Contents of URL**.
    *   **URL:** `http://<YOUR_N8N_WEBHOOK_URL>/voice-command` (e.g., `http://<your-tailscale-ip>:5678/voice-command`)
    *   **Method:** `POST`
    *   **Headers:** 
        *   `Content-Type`: `application/json`
    *   **Request Body:** `JSON`
        *   Key: `text` | Value: `Dictated Text` (from previous step)
5.  (Optional) Add action: **Show Result** to see the parsed JSON.

### For macOS (MacBook Air)
1.  Open the **Shortcuts** app.
2.  Create a new Shortcut named **"HORI Command"**.
3.  Add action: **Dictate Text**.
4.  Add action: **Get Contents of URL** (same configuration as iOS).
5.  (Optional) Add action: **Show Notification** with the result.

---

## 4. Testing

### Manual Test (via Terminal)
Run this command to simulate the Apple Shortcut:
```bash
curl -X POST http://<YOUR_N8N_WEBHOOK_URL>/voice-command \
     -H "Content-Type: application/json" \
     -d '{"text": "Create a work order to buy milk tomorrow"}'
```

### Expected Result
```json
{
  "type": "work_order",
  "id": "...",
  "parent_charter_id": "...",
  "version": "1.0.0",
  "description": "Buy milk tomorrow",
  "status": "backlog",
  "priority": "medium"
}