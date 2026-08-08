# HORI Architecture Analysis: Foundation Inventory

> **HISTORICAL DOCUMENT** - This was the original planning document from
> project inception. It contains stale references (Ollama, "32GB VRAM",
> software suggestions that have since been deployed). Read this for
> design philosophy and the Low Entropy Protocol concept, not for current
> configuration. For current config, see **docs/stack.md** and
> **docs/operations.md**. For project status, see **docs/roadmap.md**.

## 1. Stack Constraints

Based on the current hardware and software inventory, the following constraints define the operational boundaries of the HORI:

*   **Compute/VRAM Ceiling:** The primary local intelligence is bound by the **32GB VRAM** on the "<your-hostname>" rig. While excellent for high-performance 8B-30B parameter models (e.g., Gemma 4, Qwen 3.6), it will struggle with massive frontier-class models (100B+) without significant quantization or offloading, which impacts speed. **Note: The transition to I-Quants (Importance Matrix Quants) and uncompressed KV Caches is designed to maximize this ceiling by intelligently allocating VRAM between model weights and high-fidelity short-term memory.**
*   **Mobile/Client Bottleneck:** The MacBook Air (8GB RAM) is a significant constraint for local AI execution. It must function primarily as a **Thin Client/Orchestrator** rather than a compute node.
*   **Connectivity Dependency:** The system's "remote control" and "mesh" capabilities are heavily reliant on **Tailscale**. While highly secure, latency over non-local networks will impact real-time voice/control responsiveness.
*   **Storage/RAG Throughput:** While the 8TB NAS provides massive capacity for RAG (Retrieval-Augmented Generation), the ingestion and retrieval speed will be limited by the interface (USB/Network) compared to the local NVME.

## 2. Opportunities for Maximization

*   **Hybrid Intelligence Strategy:** We can implement a "Tiered Reasoning" model. 
    *   *Tier 1 (Local/Fast):* "<your-hostname>" handles routine tasks, privacy-sensitive data, and immediate voice responses.
    *   *Tier 2 (Cloud/Deep):* Gemini Pro handles complex planning, long-context reasoning, and high-level strategy.
*   **Edge-to-Core Voice Loop:** Edge devices (such as the Pi4) are perfectly positioned as low-power "Always-On" listeners. By offloading the heavy Whisper/Vosk processing to "<your-hostname>" via Tailscale, we can achieve high-accuracy voice control without taxing the edge hardware.
*   **Unified Mesh Identity:** Using Tailscale and static IPs, we can treat the entire stack as a single, distributed computer. The HORI can "spawn" processes on "<your-hostname>" and "monitor" them from the MacBook seamlessly.
*   **Massive Context/RAG:** The 8TB NAS allows us to build a "Life-Long Memory" for the HORI, indexing everything from project files to music libraries, making the HORI contextually aware of your entire digital life.

## 3. Capabilities vs. Limitations

### What the HORI will be GREAT at:
*   **Private, Local-First Development:** Using Cline/VS Code on "<your-hostname>" for secure, private coding.
*   **Context-Aware Personal Assistant:** Managing projects and information using the massive RAG capability.
*   **Distributed Orchestration:** Controlling smart home/IoT or tabletop RPG elements via edge devices.
*   **Creative Workflow Support:** Bridging music production (MacBook) with AI-driven ideation/coding (the host).

### What the HORI might be UNDERPOWERED for:
*   **Heavy Local Training/Fine-tuning:** 32GB VRAM is great for inference, but large-scale model training is out of reach.
*   **High-Fidelity Real-time Video Processing:** The current stack is optimized for text, voice, and code, not real-time computer vision/video streaming.
*   **Local Execution of Massive Models:** Running 70B+ models at high speed will be a challenge.

## 4. Maximizing Existing Investments

To honor your preference for simplicity and maximizing current value, the HORI will leverage your existing subscriptions as follows:

*   **Gemini Pro (Tier 2 Intelligence):** Instead of trying to run massive models locally, we will use Gemini Pro via API for high-level reasoning, complex planning, and long-context tasks. This keeps "<your-hostname>" fast and responsive while providing "frontier" intelligence when needed.
*   **Devin Pro (Specialized Engineering):** We will treat Devin as a "Senior AI Engineer" on call. While the HORI handles the day-to-day orchestration and local coding, Devin will be invoked for deep architectural refactors and complex debugging that require its specialized cloud-based capabilities.

## 5. Software Suggestions & Redundancy Check

### Redundancy Check:
*   Currently, the stack is lean. No immediate software redundancies were identified.

### Elevating Software Suggestions:

**A. The Nervous System (Orchestration & Automation):**
*   **n8n (Self-hosted via Docker):** A powerful, low-code workflow automation tool. This would be the "glue" connecting your voice (edge devices), your files (NAS), and your AI (llama.cpp/Gemini).
*   **Home Assistant:** Even if not a "smart home" enthusiast, Home Assistant is the gold standard for device orchestration and could manage the "physical" aspects of your HORI (WOL, lights, status LEDs).

**B. The Memory (Vector Database & RAG):**
*   **Qdrant or ChromaDB:** To manage the massive amount of data on your 8TB NAS, we need a dedicated, high-performance vector database running on "<your-hostname>".

**C. The Interface (Monitoring & Control):**
*   **Prometheus + Grafana:** To monitor the "health" of the HORI (VRAM usage on the host, edge device temperature, Tailscale latency). This provides the "Self-Healing" visibility required.
*   **Apprise:** For unified notification delivery (sending HORI alerts to your iPhone, MacBook, or even a physical light via edge devices).

**D. The Voice (Advanced Interaction):**
*   **Faster-Whisper:** To ensure the voice-to-text pipeline on edge devices is as low-latency as possible when offloading to "<your-hostname>".

## 6. The Low Entropy Protocol (LEP)

To prevent "predictive drift" and ensure long-term project integrity, the HORI utilizes a hierarchical, agentic approach to intent management, **supported by a high-fidelity technical foundation (I-Quants and uncompressed KV Caches) to maintain cognitive clarity during long-context operations.**

### A. Temporal Layers of Truth
We move away from flat context windows to a tiered hierarchy of intent:

*   **Level 0: The Manifesto (The Anchor):** The foundational, slow-moving "Constitution" of the HORI. Defines core values and global constraints.
*   **Level 1: The Project Charter (The Law):** Project-specific mission, technical "DNA," and success criteria. This is the "Source of Truth" for any given project.
*   **Level 2: The Work Order (The Current):** The high-velocity, immediate session context. This is where "vibe coding" and rapid iteration occur.

### B. The Alignment Engine (The Red Team)
Instead of a passive assistant, the HORI employs an active "Red Team" of agentic personas to monitor the delta between these layers:

*   **The Strategist:** Monitors alignment between the **Work Order** and the **Project Charter**. (e.g., *"This feature is cool, but it's delaying our MVP launch. Is this a strategic pivot?"*)
*   **The Architect:** Monitors alignment between the **Work Order** and the **Technical Constraints** in the Charter. (e.g., *"This implementation violates our 'Local-First' principle. Should we refactor?"*)
*   **The Guardian:** Monitors alignment with the **Manifesto** and resource constraints. (e.g., *"This request exceeds our current VRAM ceiling on '<your-hostname>'."*)

### C. Intentional Friction & The "Yes, AND" Mechanism
When a significant "Delta" (divergence) is detected, the HORI does not blindly execute. It triggers **Intentional Friction**:
1.  **Detection:** The Alignment Engine identifies a conflict.
2.  **Intervention:** The HORI pauses and presents the conflict using a "Yes, AND" framework.
3.  **Resolution:** The user either accepts the risk (updating the Charter/Manifesto) or corrects the course (refining the Work Order).

### D. Observability: The Alignment Score
The system maintains a real-time **Alignment Score (0-100%)**, providing a telemetry-based "Check Engine" light for the project's entropy levels.

## 7. Bleeding-Edge Research for LEP Implementation

To implement the Low Entropy Protocol effectively, the following emerging technologies and techniques should be monitored and integrated:

| Technique | Pros | Cons | HORI Application |
| :--- | :--- | :--- | :--- |
| **Long-Context Models (1M+ tokens)** | Reduces the need for complex RAG; keeps more "raw" history available. | Can still suffer from "lost in the middle" and high compute cost. | Provides the "Working Memory" for the current session. |
| **Agentic Reasoning (CoT/ToT)** | Allows the AI to "think" through implications before acting. | Slower; can lead to infinite loops if not constrained. | The core of the "Vibe-to-Spec" and "Drift Detection" loops. |
| **Graph-RAG (Knowledge Graphs)** | Provides structured, relational context that resists drift better than vector search. | Higher complexity to build and maintain the graph. | The foundation of our **Intent Graph** (The Soul). |
| **Speculative Decoding** | Significantly speeds up local inference. | Requires a smaller "draft" model to be running alongside the main one. | Essential for maintaining the "speed of thought" interaction. |
| **Hierarchical Memory Architectures** | Mimics human brain (Working vs. Long-term memory). | Requires sophisticated management of what is "promoted" or "archived." | Managing the transition from "Current Task" to "Project History." |

## 8. Model Loading & Serving Architecture

To maximize the utility of the "<your-hostname>" rig and ensure a frictionless developer experience, the model loading and serving layer follows these principles:

### A. Model Consolidation (Single Source of Truth)
To minimize fragmentation and simplify management, all models are maintained in a single, unified directory: `~/ai-models`. The Hugging Face CLI (`hf`) is used to download specific I-Quants (e.g., `IQ4_NL.gguf`) directly into this location, ensuring a centralized repository for all local intelligence.

### B. Frictionless Serving (Router Mode & Optimization)
The serving layer utilizes `llama-server` in **Router Mode** (`--models-dir ~/ai-models`), enabling seamless, hot-swappable model selection via a unified UI. Performance and responsiveness are maximized through:
* **Aggressive Prompt Caching**: NOTE: `--cache-reuse 256` was removed Aug 2026 (incompatible with turbo4 KV cache, silently disabled at startup). Prompt caching is now handled by aios-core's context trimming (last 6 turns + memory retrieval) and slot-level KV checkpoints. See docs/operations.md.
* **Flash Attention** (`-fa on`): Optimizes attention computation to prevent VRAM bloat and maintain high-speed inference even when processing massive codebases or long-term memory logs.

### C. Access & Integration
The serving layer exposes an OpenAI-compatible endpoint and a native web interface (`llama-ui`) on port 8080. This is accessible via Tailscale from any device (MacBook, iPhone, etc.) and is designed for direct integration with IDEs like VS Code, Cline, and Roo.

### Future Trajectory
The industry is moving away from "Chat-as-Interface" toward **"Agent-as-Partner."** We expect a shift from models that simply predict the next token to models that can maintain internal state, reason about long-term goals, and interact with structured knowledge (Graphs) as natively as they do with text. The HORI is being designed to sit at the forefront of this shift.
