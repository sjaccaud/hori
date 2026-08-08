# Hardware
- All devices Tailscale meshed, WOL
- Local Headless AI Rig ("<your-hostname>"), Ubuntu 26.04, Radeon AI PRO R9700 32GB VRAM, Ryzen 7800X3D, 2TB NVME (sys), 4TB USB SSD (projects, cache), 8TB USB NAS HDD RAID 10+1 (RAG ingestion, long term storage), llama.cpp / ROCm compiled
- MacBook Air Mobile IDE & Music Production Rig (M1, 2020) 8GB RAM, 256GB SSD, 2TB NVME USB (audio vst sample libraries), 4K Acer Monitor, Pluggable Dual Displaoy Port USB doc, Audient Evo 4 audio interface
- Raspberry Pi4 Voice Controlled Tabletop RPG Enhancer ("pi4"), Pi OS 64bit Lite, 4GB RAM, Argon40 case, 1TB M.2 SSD, Neewer KM18 mic, Positive Grid Spark Mini BT/USB, running Vosk / Whisper for voice activation
- iPhone 15 Pro Max, remote ssh, device control on the go, voice activation surface / siri

# Networking
- Amplifi Alien mesh with static IP's for all devices
- Arris SB8200 Cable Modem (RCN 1000/100 cable provider)
- Tailscale mesh all devices

# Software
- Google Gemini Pro frontier strategy and reasoning ($20/mo), 5TB Google Drive and Google Suite, AI Studio + AI Credits, Developer
- Devin Pro for frontier cloud AI, deep analysis and refactors (formerly Windsurf, $20/mo)
- VS Code + Cline for local-first / private AI testing, projects
- Completely open to new software possibilities, models, technologies - with preference for FREE, Open Source, automatable, secure, and LOCAL-FIRST

# Local-First AI Model Ethos
1. The Engine: Standardize on llama.cpp (ROCm/HIP)
- We want to use advanced quantization techniques and have deep control over context windows, so need to move away from Ollama for our primary heavy-lifting models.
- Why ditch Ollama? Ollama is great for beginners, but it hides our models in unreadable blob folders and limits the ability to use bleeding-edge quantizations.
- The Solution: Use llama.cpp compiled specifically with the ROCm/HIP backend. This speaks directly to your AMD R9700's native compute architecture, giving you maximum tokens per second and zero CPU fallback.

2. The Model Strategy: I-Quants + TurboQuant KV Cache
- Current model: Qwen3.6-27B IQ4_NL (15GB), hybrid Gated DeltaNet + SSM architecture
- The Magic of I-Quants: Instead of standard quants (like Q4 or Q8) that compress every neural connection equally and destroy niche coding skills, use Importance Matrix Quants (I-Quants) like IQ4_NL. I-Quants evaluate the model's brain and preserve the highly active reasoning and coding pathways in near-perfect definition, while aggressively squishing the "useless" connections.
- TurboQuant KV Cache: Using the Spiritbuun fork's turbo4 KV cache type (4.125 bpv) with flash attention. This compresses the KV cache 4x compared to f16, allowing 16K context in ~16GB VRAM total (model + KV). ROCm 7.14 required - earlier versions produced corrupted output with Qwen3.6's hybrid architecture.
- ROCm 7.14: Installed via runfile installer to /opt/rocm. Required for Qwen3.6 support (fixes HipVMM crashes) and turbo4 KV cache compatibility. SDK headers assembled at ~/rocm-sdk/core-7.14.

3. The Consolidation Workflow: One Folder to Rule Them All
- Stop scattering models across different UI folders and scratch drives.
- Maintain a Single Source of Truth: our current directory on the Ubuntu server ~/ai-models.
- Use the Modern hf CLI: Use the new, unified Hugging Face CLI tool (hf) to download IQ4_NL .gguf files directly into that single folder. (Example: hf download bartowski/Qwen_Qwen3.5-27B-GGUF --include "IQ4_NL.gguf" --local-dir ~/ai-models)

4. The Frictionless Serving Strategy: Caching Flags & TurboQuant
- The llama-server binary runs as a systemd service with advanced execution flags locked in.
- Binary: Spiritbuun fork at ~/llama-builds/spiritbuun-714/bin/llama-server (built against ROCm 7.14)
- Model: Qwen3.6-27B IQ4_NL at ~/ai-models/qwen3.6-27b-IQ4_NL-with-MTP.gguf (15GB)
- Aggressive Prompt Caching: NOTE: --cache-reuse 256 was removed Aug 2026 — it is incompatible with turbo4 KV cache (silently disabled at startup). Prompt caching is now handled by aios-core's context trimming (last 6 turns + memory retrieval) and the slot-level KV checkpoint mechanism. See docs/operations.md for details.
- Flash Attention (-fa on): Replaces standard attention computation with an optimized kernel. This prevents VRAM bloat and severe slowdowns when we push massive codebases or markdown memory logs into your context window.
- TurboQuant KV Cache (-ctk turbo4 -ctv turbo4): 4.125 bpv KV cache quantization, fused inside flash-attention decode. 16K context in ~16GB VRAM total.

5. Accessing It
- Once llama-server is running, it exposes a native, lightning-fast web interface (llama-ui) directly on port 8080.
- From anywhere: Simply navigate to tailscale IP at http://<your-tailscale-ip>:8080/v1 from MacBook or iPhone to use the chat UI and model-switcher.
- From IDEs: Point VS Code, Cline, or Roo directly to the OpenAI-compatible endpoint at http://<your-tailscale-ip>:8080/v1.