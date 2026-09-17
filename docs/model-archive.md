# Offline Model Archive — /mnt/4TB/model-archive

**Status as of 2026-09-17:** ~577 GB, fully downloaded (no `.incomplete`/`.partial`
files). Started 2026-09-04 via `hf download --local-dir` — the
"digital prepper" open-weights haul — and has grown well past the original
stash into a living, curated library (added categories: `uncensored/`,
`legal/`, `medical/`, `moe/`, `translation/`, `multilingual/`).

This archive is **storage**, not the live serving set. Live models used by the
llama.cpp router stay in `/opt/ai-models/` (NVMe root); the archive on the 4TB
drive is the cold copy / source for new presets.

## Layout

```
/mnt/4TB/model-archive/
├── llm/          (~530G) large language models
│   ├── general/      (~224G) qwen-2-5-72b-q4-k-m, llama-3-3-70b-q4-k-m,
│   │                       qwen-3-8-27b-{q8-0,q6-k,ud-q6-k,iq4-nl}, …
│   ├── reasoning/    (~76G)  qwen-2-5-math-72b-q4, deepseek-r1-distill-qwen-32b, …
│   ├── coding/       (~73G)  qwen-2-5-coder-32b-q4, opencode-reasoning-32b-q4,
│   │                       devstral-coding-24b, …
│   ├── long-context/ (~46G)  qwen-3-8-27b-128k-q4, qwen-3-8-256k-batch-q4, …
│   ├── uncensored/   (~39G)  qwen3-6-35b-a3b-abliterated-mtp,
│   │                       qwen3-8-27b-abliterated (→ /opt qwen-free), …
│   ├── moe/          (~25G)  mixtral-8x7b-q4, …
│   ├── multilingual/ (~22G)
│   ├── translation/  (~17G)  nllb-200-3-3b, …
│   ├── medical/      (~7.9G)
│   └── legal/        (~4.1G)
├── vlm/          (~19G)  vision-language models
│   ├── llama-3-2-11b-vision-q4/  → llama3-2-vision-11b Q4_K_M
│   └── qwen-2-5-vl-7b-q4/        → Qwen2.5-VL-7B Q4_K_M + f16 mmproj
├── stt/          (~14G)  speech-to-text
│   ├── distil-whisper-large-v3/  → safetensors + fp32
│   └── whisper-large-v3-turbo-q8/→ Q8_0 GGUF
├── slm/          (~5.9G) small language models
│   ├── llama-3-2-3b-instruct-q4/
│   └── qwen-2-5-1-5b-instruct-q4/
├── embedding/    (~7.9G) embedding models
│   ├── bge-base-en-v1-5/         (also the live Open-WebUI embedding model)
│   └── nomic-embed-text-v1-5/
└── tts/          (~1.1G) text-to-speech
    └── kokoro-82m/
```

## Checking download status

- Completed downloads leave `.cache/huggingface/download/*.lock` + `*.metadata`
  inside each model dir (hf `--local-dir` layout). Interrupted downloads show
  `.incomplete` files — none exist as of 2026-09-17.
- Quick check: `find /mnt/4TB/model-archive -name '*.incomplete'` → empty = all done.
- Inventory check: `du -sh /mnt/4TB/model-archive/*/` or `ls -lt` per category.
