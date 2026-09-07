# Offline Model Archive — /mnt/4TB/model-archive

**Status as of 2026-09-05:** ~128 GB, fully downloaded (no `.incomplete`/`.partial`
files). Populated 2026-09-04 late evening via `hf download --local-dir` — the
"digital prepper" open-weights haul: an offline stash of models across
categories so the rig keeps working without network access.

This archive is **storage**, not the live serving set. Live models used by the
llama.cpp router stay in `/opt/ai-models/` (NVMe root); the archive on the 4TB
drive is the cold copy / source for new presets.

## Layout

```
/mnt/4TB/model-archive/
├── llm/          (~108G) large language models
│   ├── reasoning/    deepseek-r1-distill-qwen-32b/  → DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf
│   ├── general/      qwen-3-8-27b-q8-0/, qwen-3-8-27b-iq4-nl/
│   ├── long-context/ qwen-3-8-27b-128k-q4/          → Qwen3.8-27B-IQ4_NL.gguf
│   └── coding/       qwen-2-5-coder-32b-q4/         → Qwen2.5-Coder-32B-Instruct-Q4_K_M.gguf
│                     devstral-coding-24b/           → Devstral-Small-2-24B Q4_K_M
├── vlm/          (~12G)  vision-language models
│   ├── llama-3-2-11b-vision-q4/  → llama3-2-vision-11b Q4_K_M
│   └── qwen-2-5-vl-7b-q4/        → Qwen2.5-VL-7B Q4_K_M + f16 mmproj
├── stt/          (~5.1G) speech-to-text
│   ├── distil-whisper-large-v3/  → safetensors + fp32
│   └── whisper-large-v3-turbo-q8/→ Q8_0 GGUF
├── slm/          (~2.8G) small language models
│   ├── llama-3-2-3b-instruct-q4/
│   └── qwen-2-5-1-5b-instruct-q4/
├── embedding/    (~942M) embedding models
│   ├── bge-base-en-v1-5/         (also the live Open-WebUI embedding model)
│   └── nomic-embed-text-v1-5/
└── tts/          (~340M) text-to-speech
    └── kokoro-82m/
```

## Checking download status

- Completed downloads leave `.cache/huggingface/download/*.lock` + `*.metadata`
  inside each model dir (hf `--local-dir` layout). Interrupted downloads show
  `.incomplete` files — none exist as of 2026-09-05.
- Quick check: `find /mnt/4TB/model-archive -name '*.incomplete'` → empty = all done.
- Inventory check: `du -sh /mnt/4TB/model-archive/*/` or `ls -lt` per category.
