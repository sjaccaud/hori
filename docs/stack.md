# Hardware

HORI is designed to run on a local GPU server with remote access via
Tailscale. The reference setup:

- **GPU server:** Headless Linux box with an AMD Radeon AI PRO R9700
  (32GB VRAM), Ryzen 7800X3D, ROCm 7.14. This is where llama.cpp and
  aios-core run.
- **Laptop:** Any macOS/Linux/Windows laptop as a thin client (chat
  surface, IDE, admin panel). No GPU needed.
- **Phone:** Any smartphone for voice chat via the mobile web app.
- **Optional edge device:** A Raspberry Pi or similar SBC for
  wake-word detection and always-on voice.

All devices are connected via Tailscale mesh VPN.

# Networking

- Tailscale mesh for all devices (Tailnet-only HTTPS, no public exposure)
- Any router/modem that provides internet access
- aios-core listens on port 5680, accessible via Tailscale Serve

# Software

- **llama.cpp** (ROCm/HIP backend) for local LLM inference
- **Hugging Face CLI** (`hf`) for downloading GGUF models
- **Qdrant** (optional) for vector memory, or **SQLite** (zero deps)
- **Kokoro-82M** or **Piper** for text-to-speech
- Any OpenAI-compatible LLM endpoint (llama.cpp, Ollama, cloud providers)

# Local-First AI Model Ethos

1. The Engine: Standardize on llama.cpp (ROCm/HIP)
- llama.cpp compiled with the ROCm/HIP backend speaks directly to AMD
  GPUs, giving maximum tokens per second and zero CPU fallback.
- Ollama is supported as an alternative for beginners, but llama.cpp
  gives deeper control over context windows and quantization.

2. The Model Strategy: I-Quants + TurboQuant KV Cache
- Current model: Qwen3.6-27B IQ4_NL (15GB), hybrid Gated DeltaNet + SSM
- Importance Matrix Quants (I-Quants) like IQ4_NL preserve the highly
  active reasoning and coding pathways in near-perfect definition while
  aggressively compressing less important connections.
- TurboQuant KV Cache (turbo4): 4x KV cache compression with near-zero
  quality loss. 16K context in ~16GB VRAM total (model + KV). Requires
  ROCm 7.14+ for Qwen3.6's hybrid architecture.

3. The Consolidation Workflow: One Folder to Rule Them All
- Maintain a single model directory (e.g. `~/ai-models`).
- Use the `hf` CLI to download IQ4_NL .gguf files directly.

4. The Frictionless Serving Strategy: Caching Flags & TurboQuant
- The llama-server binary runs as a systemd service with advanced
  execution flags.
- Flash Attention (`-fa on`): prevents VRAM bloat with large contexts.
- TurboQuant KV Cache (`-ctk turbo4 -ctv turbo4`): 4.125 bpv KV cache
  quantization, fused inside flash-attention decode.

5. Accessing It
- llama-server exposes a web UI on port 8080.
- From anywhere on your Tailnet: navigate to `http://<your-tailscale-ip>:8080/v1`
- From IDEs: point VS Code, Cline, or any OpenAI-compatible client to
  the endpoint.
