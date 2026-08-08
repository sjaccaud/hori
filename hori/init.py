"""HORI setup wizard — `hori init`.

Combines hardware detection + config creation into one command. A fresh
clone → `hori init` → working config.

What it does:
  1. Detects hardware (GPU, VRAM, CPU, RAM)
  2. Recommends a model tier
  3. Creates ~/.config/hori/hori.yaml with the recommended model
  4. Creates ~/.local/share/hori/ (data directory for SQLite memory, etc.)
  5. Prints next steps

If the config already exists, it asks before overwriting (unless --force).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import yaml

from hori.config import CONFIG_DIR, CONFIG_FILE, _load_reference
from hori.detect import detect, format_report


DATA_DIR = Path.home() / ".local" / "share" / "hori"


def run_init(force: bool = False, quiet: bool = False) -> bool:
    """Run the setup wizard.

    Args:
        force: overwrite existing config without asking
        quiet: suppress the hardware report (just create config)

    Returns:
        True if config was created/updated, False if user declined.
    """
    # --- Step 1: Check existing config ---
    if CONFIG_FILE.exists() and not force:
        if not _confirm_overwrite():
            print("Keeping existing config. No changes made.")
            return False

    # --- Step 2: Detect hardware ---
    result = detect()

    if not quiet:
        print(format_report(result))
        print()

    # --- Step 3: Build config with recommended model ---
    config = _build_config(result)

    # --- Step 4: Write config ---
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=True)
    if not quiet:
        print(f"Config written to {CONFIG_FILE}")

    # --- Step 5: Create data directory ---
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not quiet:
        print(f"Data directory created at {DATA_DIR}")
        print()
        _print_next_steps(result)

    return True


def _build_config(detect_result) -> dict:
    """Build a config dict from the reference yaml, with the recommended
    model and backend filled in."""
    config = _load_reference()

    # Set the recommended model
    rec = detect_result.recommendation
    model_map = {
        "Qwen3.6-27B IQ4_NL": "Qwen3.6-27B",
        "Qwen2.5-14B IQ4_NL": "qwen2.5:14b",
        "Llama-3.1-8B IQ4_NL": "llama3.1:8b",
        "Llama-3.2-3B IQ4_NL": "llama3.2:3b",
        "Qwen2.5-1.5B IQ4_NL": "qwen2.5:1.5b",
    }
    model_name = model_map.get(rec.model, rec.model)
    config["llm"]["model"] = model_name

    # Default to SQLite backend (zero external deps)
    config["memory"]["backend"] = "sqlite"

    return config


def _confirm_overwrite() -> bool:
    """Ask the user if they want to overwrite existing config."""
    print(f"Config already exists at {CONFIG_FILE}")
    print("Overwrite? [y/N] ", end="", flush=True)
    response = sys.stdin.readline().strip().lower()
    return response in ("y", "yes")


def _print_next_steps(detect_result) -> None:
    """Print what the user should do next."""
    rec = detect_result.recommendation
    print("=== Next steps ===")
    print()
    print(f"1. Start your LLM server")
    if rec.backend in ("rocm", "cuda"):
        print(f"   If using llama.cpp:")
        print(f"     llama-server --model ~/ai-models/<model>.gguf --host 0.0.0.0 --port 8080")
        print(f"   If using Ollama:")
        print(f"     ollama serve")
    elif rec.backend == "metal":
        print(f"   Ollama is easiest on macOS:")
        print(f"     ollama serve")
    else:
        print(f"   Ollama (CPU mode):")
        print(f"     ollama serve")
    print()
    print(f"2. Start the embedding server (for memory)")
    print(f"   If using llama.cpp:")
    print(f"     llama-server --model ~/ai-models/nomic-embed-text-v1.5.Q8_0.gguf"
          f" --host 127.0.0.1 --port 8081 --embedding --n-gpu-layers 0")
    print()
    print(f"3. Start HORI")
    print(f"   PYTHONPATH=. python -m uvicorn services.aios_core.main:app"
          f" --host 0.0.0.0 --port 5680")
    print()
    print(f"4. Open http://localhost:5680/chat in your browser")
    print()
    print(f"Edit config at any time: {CONFIG_FILE}")
