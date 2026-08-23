# Local AI / llama.cpp Decision Log

This file records the key decisions, experiments, and rationale behind the local LLM stack. It is a canonical source for `aios-system-state.md` (under `## Project Decisions`) and is also bundled with `manifesto.md`, `safety.md`, and `roadmap.md` for the Open-WebUI `Project Decisions & Architecture` collection.

## Why llama.cpp + ROCm/HIP instead of Ollama

- **Ollama was decommissioned** because it hides quantization, context-window, batching, and speculative-decoding controls. Bugs or model-loading issues became impossible to debug without switching to a direct `llama-server` anyway.
- **Direct `llama-server`** gives deterministic model loading, exact `--ctx-size` / `-np` / KV-cache control, and an OpenAI-compatible `/v1/models` + `/v1/chat/completions` endpoint that every client (Aider, Cline, Open-WebUI, Apple Shortcuts) can use.
- **ROCm/HIP on `gfx1201` (Radeon AI PRO R9700)** is the primary compute path. Vulkan/RADV was benchmarked and rejected: it matched ROCm bandwidth within noise but could not use the same KV-cache optimizations and gave no net win on this workload.

## Why Qwen3.8-27B

- Qwen3.8-27B is a 262k native-context model with a built-in **MTP draft head**, which makes `draft-mtp` speculative decoding essentially free for Qwen presets.
- The model fits in 32GB VRAM with practical quantizations and provides a good speed / quality / context trade-off.
- All production Qwen3.8-27B weights are now **Unsloth** versions, verified from `general.quantized_by` and `general.name` GGUF metadata.

## Quantization and KV cache choices

| Alias | Weights | KV cache | Speculative | Rationale |
|---|---|---|---|---|
| `qwen-fast` / `qwen-long` / `qwen-think` / `qwen-128k` / `qwen-256k-batch` | IQ4_NL | `q4_0` | `draft-mtp` n=4 | Fast, low VRAM, long contexts. `draft-mtp` recovers interactive speed. |
| `qwen-balanced` | Unsloth `UD-Q6_K` | `q4_0` | none | Quality/VRAM balance; the Unsloth Q6_K is ~18% faster on long prompts and uses ~450 MB less VRAM than the Bartowski-style Q6_K it replaced. |
| `qwen-quality` | Unsloth Q8_0 | `q8_0` | `draft-mtp` n=4 | Highest-quality coding / reasoning; Q8_0 KV preserves full precision where it matters. |
| `devstral-coding` | IQ4_NL | `f16` | `ngram-mod` | Devstral-Small-2-24B benefits from n-gram speculative decoding; `ngram-mod` is kept for this model only. |

- **Q4/Q6 Qwen weights use `q4_0` KV** because it is faster, smaller, and has full needle-in-haystack recall on Qwen3.8-27B in our tests.
- **Q8_0 weights keep `q8_0` KV** to avoid any measurable quality loss at the high-quality end.
- **Q8_0 vs Q6_K speed:** Q8_0 is ~41 t/s on short prompts and ~35 t/s on long prompts; Q6_K is ~24.5 t/s generation but with much faster prompt processing due to smaller weights. The right alias depends on prompt length and quality needs.

## Speculative decoding experiments

- **Qwen:** `draft-mtp` (MTP draft head) is the clear winner. It is stable and gives a measurable speedup.
- **Devstral:** `ngram-mod` works well. Do **not** use `ngram-mod` with Qwen — it is not beneficial with the MTP architecture and can increase overhead.
- **DFlash draft models:** tested for Qwen3.5-27B but could not fit alongside the 27B target in 32GB VRAM (target + draft + KV + compute left too little headroom). Abandoned until a 48GB+ GPU is available.
- **TurboQuant / `turbo4` KV:** used during the Qwen3.6 era but is no longer the default for Qwen3.8. The current KV types are `q4_0` / `q8_0` / `f16` depending on the alias.

## Router and alias design

- Router aliases are **descriptive** (`qwen-fast`, `qwen-quality`, `qwen-balanced`, `qwen-long`, `qwen-128k`, `qwen-256k-batch`, `qwen-think`, `devstral-coding`).
- Legacy names `plan` and `act` were removed to avoid confusion.
- Each alias maps to a file, context size, KV type, and speculative-decoding preset in `~/llama-builds/spiritbuun-714/router.ini`.
- Clients select an alias from `/v1/models` and never load a model by raw filename.

## Why Unsloth weights replaced Bartowski-style quantizations

- **Q8_0:** No material speed or VRAM difference, but the Unsloth file carries the published Unsloth imatrix/chat template and is the consistent upstream for Qwen3.8.
- **Q6_K:** Unsloth `UD-Q6_K` is a clear win — prompt tps were 753 vs 639 for `long_prompt` and 779 vs 657 for `long_context` in the 8k benchmark, with ~450 MB less VRAM. Generation speed was unchanged.
- This led to the Bartowski-style Q8_0/Q6_K files being removed from `/opt/ai-models` and the Unsloth files being renamed in place.

## ROCm and environment decisions

- **Build:** Spiritbuun's `buun-llama-cpp` fork, compiled with HIP for `gfx1201` at `~/llama-builds/spiritbuun-714`.
- **Runtime `LD_LIBRARY_PATH`:** `/opt/rocm/core-7.14/lib`, `.../rocm_sysdeps/lib`, and `.../llvm/lib` are required for the ROCm runtime to resolve all dependencies.
- **`GPU_MAX_HW_QUEUES=1`:** was added as a preventive measure against the ROCm idle-100%-GPU bug seen on R9700. It was later removed from the systemd override during the Aug 2026 cleanup because the system was not affected, but the history is preserved here in case it needs to be re-applied.
- **ROCm 7.14 vs 7.1:** 7.14 was required to eliminate HipVMM crashes and corrupted KV output with earlier Qwen3.6 experiments. The current Qwen3.8 build continues to use 7.14.

## Models and tools that were removed

- **Ollama** — decommissioned.
- **Qwen3.6-27B, Qwen3.5-27B, Qwen2.5-Coder-32B-Instruct-IQ4_NL** — removed after migrating to Qwen3.8-27B.
- **Nomic embedding model** — removed; local embedding now uses `BAAI/bge-base-en-v1.5` via the Open-WebUI sentence-transformers path (CPU, 768-dim).
- **Gemma-4-26B** — removed as it was not used.
- **Old benchmark directories and temporary `unsloth-bench/`** — cleaned up to free ~86 GB.

## Context length and practical limits

- Qwen3.8-27B is 262k native context, but 32GB VRAM imposes practical limits.
- **16K** is the default interactive context for `qwen-fast/quality/balanced/think`.
- **65K / 128K** are available for long documents and planning (`qwen-long` / `qwen-128k`).
- **256K is batch-only** (`qwen-256k-batch`) because a full prefill at 256K would take ~12 minutes interactively.

## Open-WebUI knowledge strategy

- `aios-system-state.md` (in `Local Benchmarks`) is the live runtime doc: current `AGENTS.md`, `router.ini`, and the latest benchmark results.
- `project-decisions.md` (in `Project Decisions & Architecture`) is the curated decision archive: manifesto, safety, roadmap, and this local-AI decision log.
- The two are kept separate so runtime answers do not get mixed with stale historical values, while the rationale and philosophy remain retrievable.
