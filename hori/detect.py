"""Hardware detection and model tier recommendations.

`hori detect` inspects the host system and recommends a model tier
based on available VRAM (or RAM for CPU-only systems). This helps
new users pick the right model without reading the full docs.

Detection strategy (in priority order):
  1. AMD GPU (ROCm)  — via /sys/class/kfd or rocm-smi
  2. NVIDIA GPU      — via nvidia-smi
  3. Apple Silicon   — via system_profiler (macOS)
  4. CPU fallback    — via /proc/meminfo (Linux) or sysctl (macOS)

Model tiers (based on GGUF IQ4_NL quantization, ~0.55 bytes/param):
  - 24GB+ VRAM : 27B models  (Qwen3.6-27B, etc.)
  - 12GB+ VRAM : 14B models  (Qwen2.5-14B, etc.)
  - 8GB+  VRAM : 8B models   (Llama-3.1-8B, Qwen2.5-7B, etc.)
  - 4GB+  VRAM : 3B models   (Llama-3.2-3B, etc.)
  - <4GB       : 1.5B models (Qwen2.5-1.5B, etc.) or CPU-only

Traces to: docs/operations.md (hardware config), docs/operations.md (model config).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, asdict
from typing import Optional


# --- Model tiers ---
# (min_vram_gb, tier_name, example_model, approx_model_size_gb, notes)
TIERS = [
    (24, "heavy",  "Qwen3.6-27B IQ4_NL",  15, "27B class models. Best quality. Needs ROCm/CUDA."),
    (12, "medium", "Qwen2.5-14B IQ4_NL",   8, "14B class models. Good balance of speed and quality."),
    (8,  "light",  "Llama-3.1-8B IQ4_NL",  5, "8B class models. Fast, runs on consumer GPUs."),
    (4,  "micro",  "Llama-3.2-3B IQ4_NL",  2, "3B class models. Very fast, low VRAM."),
    (0,  "nano",   "Qwen2.5-1.5B IQ4_NL",  1, "1.5B class models. CPU-only or very low VRAM."),
]


@dataclass
class GPUInfo:
    vendor: str          # "amd", "nvidia", "apple", "cpu"
    name: str            # human-readable GPU name
    vram_mb: int         # VRAM in MB (0 for CPU)
    vram_gb: float       # VRAM in GB (0 for CPU)
    backend: str         # "rocm", "cuda", "metal", "cpu"


@dataclass
class SystemInfo:
    os: str              # "linux", "darwin", "windows"
    cpu: str             # CPU model name
    ram_gb: float        # Total system RAM in GB
    gpus: list[GPUInfo]  # Detected GPUs (empty if CPU-only)
    primary_gpu: Optional[GPUInfo]  # Best GPU (highest VRAM), or None


@dataclass
class ModelRecommendation:
    tier: str            # "heavy", "medium", "light", "micro", "nano"
    model: str           # Example model name
    size_gb: int         # Approximate model file size
    backend: str         # Recommended backend
    notes: str           # Additional notes


@dataclass
class DetectionResult:
    system: SystemInfo
    recommendation: ModelRecommendation
    config_snippet: str  # YAML snippet for hori.yaml


def _run(cmd: list[str], timeout: int = 5) -> str:
    """Run a command and return stdout, or empty string on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def _detect_amd_rocm() -> list[GPUInfo]:
    """Detect AMD GPUs via ROCm (rocm-smi) or DRM sysfs."""
    gpus = []

    # Try rocm-smi first (most reliable if ROCm is installed)
    # Need both --showproductname (for GPU name) and --showmeminfo vram (for VRAM)
    name_output = _run(["rocm-smi", "--showproductname", "--json"])
    vram_output = _run(["rocm-smi", "--showmeminfo", "vram", "--json"])
    if name_output:
        try:
            name_data = json.loads(name_output)
            vram_data = json.loads(vram_output) if vram_output else {}
            for key, gpu_data in name_data.items():
                if not key.startswith("card"):
                    continue
                name = gpu_data.get("Card Series",
                      gpu_data.get("Card series",
                      gpu_data.get("Card model", "AMD GPU")))
                # VRAM is in a separate rocm-smi call
                vram_str = vram_data.get(key, {}).get("VRAM Total Memory (B)", "0")
                vram_bytes = int(vram_str) if vram_str.isdigit() else 0
                vram_mb = vram_bytes // (1024 * 1024)
                gpus.append(GPUInfo(
                    vendor="amd", name=name,
                    vram_mb=vram_mb, vram_gb=round(vram_mb / 1024, 1),
                    backend="rocm",
                ))
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: DRM sysfs (works without ROCm installed, just needs amdgpu driver)
    if not gpus:
        drm_path = "/sys/class/drm"
        if os.path.isdir(drm_path):
            for entry in sorted(os.listdir(drm_path)):
                # Look for cardN (not cardN-Connector or renderDNN)
                if not entry.startswith("card") or "-" in entry or entry.startswith("renderD"):
                    continue
                card_path = os.path.join(drm_path, entry, "device")
                uevent_path = os.path.join(card_path, "uevent")
                vram_path = os.path.join(card_path, "mem_info_vram_total")

                # Check if this is an AMD GPU
                if not os.path.isfile(uevent_path):
                    continue
                try:
                    with open(uevent_path) as f:
                        uevent = f.read()
                    if "DRIVER=amdgpu" not in uevent:
                        continue
                except OSError:
                    continue

                # Get VRAM
                vram_bytes = 0
                if os.path.isfile(vram_path):
                    try:
                        with open(vram_path) as f:
                            vram_bytes = int(f.read().strip())
                    except (OSError, ValueError):
                        pass
                vram_mb = vram_bytes // (1024 * 1024)

                # Get GPU name from lspci (best effort)
                name = "AMD GPU"
                pci_slot = None
                for line in uevent.splitlines():
                    if line.startswith("PCI_SLOT_NAME="):
                        pci_slot = line.split("=", 1)[1]
                        break
                if pci_slot:
                    lspci_out = _run(["lspci", "-v", "-s", pci_slot])
                    for line in lspci_out.splitlines():
                        if "VGA compatible" in line or "Display" in line:
                            # Extract name after the colon
                            if ":" in line:
                                # "03:00.0 VGA compatible controller: AMD... [Radeon ...]"
                                parts = line.split(":", 2)
                                if len(parts) >= 3:
                                    name = parts[2].strip().rstrip("]")
                                    # Strip the [rev xx] part
                                    if " [" in name:
                                        name = name.split(" [")[0]
                                    break

                gpus.append(GPUInfo(
                    vendor="amd", name=name,
                    vram_mb=vram_mb, vram_gb=round(vram_mb / 1024, 1),
                    backend="rocm",
                ))

    return gpus


def _detect_nvidia() -> list[GPUInfo]:
    """Detect NVIDIA GPUs via nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return []

    output = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ])
    if not output:
        return []

    gpus = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            vram_mb = int(parts[1])
        except ValueError:
            continue
        gpus.append(GPUInfo(
            vendor="nvidia", name=name,
            vram_mb=vram_mb, vram_gb=round(vram_mb / 1024, 1),
            backend="cuda",
        ))
    return gpus


def _detect_apple() -> list[GPUInfo]:
    """Detect Apple Silicon unified memory (macOS only)."""
    if platform.system() != "Darwin":
        return []

    # Check for Apple Silicon
    output = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if "Apple" not in output:
        return []

    # Get unified memory size
    mem_output = _run(["sysctl", "-n", "hw.memsize"])
    try:
        ram_bytes = int(mem_output)
        ram_gb = ram_bytes / (1024 ** 3)
    except ValueError:
        return []

    # Apple Silicon shares RAM between CPU and GPU.
    # We report ~70% as "VRAM" (the GPU can use most of unified memory,
    # but the OS needs some for itself).
    vram_gb = round(ram_gb * 0.7, 1)
    vram_mb = int(vram_gb * 1024)

    return [GPUInfo(
        vendor="apple", name=output,
        vram_mb=vram_mb, vram_gb=vram_gb,
        backend="metal",
    )]


def _get_ram_gb() -> float:
    """Get total system RAM in GB."""
    if platform.system() == "Darwin":
        output = _run(["sysctl", "-n", "hw.memsize"])
        try:
            return round(int(output) / (1024 ** 3), 1)
        except ValueError:
            return 0.0

    # Linux: /proc/meminfo
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    # "MemTotal:       32783244 kB"
                    kb = int(line.split()[1])
                    return round(kb / (1024 * 1024), 1)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _get_cpu_name() -> str:
    """Get CPU model name."""
    if platform.system() == "Darwin":
        output = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        return output or platform.processor() or "Unknown"

    # Linux: /proc/cpuinfo
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "Unknown"


def detect_system() -> SystemInfo:
    """Detect system hardware: CPU, RAM, GPUs."""
    gpus: list[GPUInfo] = []

    # Detect GPUs in priority order
    gpus.extend(_detect_amd_rocm())
    gpus.extend(_detect_nvidia())
    gpus.extend(_detect_apple())

    # Pick primary GPU (highest VRAM)
    primary = max(gpus, key=lambda g: g.vram_mb) if gpus else None

    return SystemInfo(
        os=platform.system().lower(),
        cpu=_get_cpu_name(),
        ram_gb=_get_ram_gb(),
        gpus=gpus,
        primary_gpu=primary,
    )


def recommend_model(system: SystemInfo) -> ModelRecommendation:
    """Recommend a model tier based on available VRAM (or RAM for CPU)."""
    if system.primary_gpu and system.primary_gpu.vram_gb > 0:
        vram = system.primary_gpu.vram_gb
        backend = system.primary_gpu.backend
        gpu_name = system.primary_gpu.name
    else:
        # CPU-only: use RAM, but cap at 50% (leave room for OS + context)
        vram = system.ram_gb * 0.5
        backend = "cpu"
        gpu_name = "CPU"

    for min_vram, tier, model, size_gb, notes in TIERS:
        if vram >= min_vram:
            if backend == "cpu" and tier in ("heavy", "medium"):
                # Don't recommend 14B+ for CPU — too slow
                return ModelRecommendation(
                    tier="light",
                    model="Llama-3.1-8B IQ4_NL",
                    size_gb=5,
                    backend="cpu",
                    notes=f"CPU-only mode ({system.ram_gb:.0f}GB RAM). "
                          f"8B model is the practical max for CPU inference. "
                          f"Expect 2-5 tokens/sec.",
                )
            return ModelRecommendation(
                tier=tier, model=model, size_gb=size_gb,
                backend=backend,
                notes=f"{gpu_name} ({vram:.1f}GB {'VRAM' if backend != 'cpu' else 'RAM'}). {notes}",
            )

    # Shouldn't reach here (nano tier has min_vram=0), but just in case
    return ModelRecommendation(
        tier="nano", model="Qwen2.5-1.5B IQ4_NL", size_gb=1,
        backend=backend, notes="Minimal hardware. 1.5B model only.",
    )


def generate_config_snippet(rec: ModelRecommendation) -> str:
    """Generate a hori.yaml snippet for the recommended model."""
    # Map model names to llama-server compatible names
    model_map = {
        "Qwen3.6-27B IQ4_NL": "Qwen3.6-27B",
        "Qwen2.5-14B IQ4_NL": "qwen2.5:14b",
        "Llama-3.1-8B IQ4_NL": "llama3.1:8b",
        "Llama-3.2-3B IQ4_NL": "llama3.2:3b",
        "Qwen2.5-1.5B IQ4_NL": "qwen2.5:1.5b",
    }
    model_name = model_map.get(rec.model, rec.model)

    return f"""# hori.yaml — generated by `hori detect`
# Recommended tier: {rec.tier} ({rec.backend})
# {rec.notes}

llm:
  model: "{model_name}"
  # If using llama-server, the model is loaded by the server, not HORI.
  # If using Ollama, this is the model tag.
  # If using a cloud provider, this is the model name (e.g. "gpt-4o").

embedding:
  model: "nomic-embed-text-v1.5.Q8_0"
  dim: 768
"""


def detect() -> DetectionResult:
    """Run full detection and return results."""
    system = detect_system()
    rec = recommend_model(system)
    snippet = generate_config_snippet(rec)
    return DetectionResult(system=system, recommendation=rec, config_snippet=snippet)


def format_report(result: DetectionResult) -> str:
    """Format detection results as a human-readable report."""
    s = result.system
    r = result.recommendation
    lines = []

    lines.append("=== HORI Hardware Detection ===")
    lines.append("")
    lines.append(f"OS:   {s.os}")
    lines.append(f"CPU:  {s.cpu}")
    lines.append(f"RAM:  {s.ram_gb:.1f} GB")
    lines.append("")

    if s.gpus:
        lines.append("GPUs detected:")
        for i, gpu in enumerate(s.gpus):
            marker = " (primary)" if gpu == s.primary_gpu else ""
            lines.append(f"  [{i}] {gpu.name}")
            lines.append(f"      VRAM: {gpu.vram_gb:.1f} GB ({gpu.vram_mb} MB){marker}")
            lines.append(f"      Backend: {gpu.backend}")
    else:
        lines.append("No GPU detected. CPU-only mode.")
    lines.append("")

    lines.append("=== Recommendation ===")
    lines.append(f"Tier:     {r.tier}")
    lines.append(f"Model:    {r.model}")
    lines.append(f"Size:     ~{r.size_gb} GB (GGUF IQ4_NL)")
    lines.append(f"Backend:  {r.backend}")
    lines.append(f"Notes:    {r.notes}")
    lines.append("")

    # Download hint
    if r.backend in ("rocm", "cuda"):
        lines.append("To download the model (llama.cpp / GGUF):")
        lines.append(f"  hf download bartowski/<model>-GGUF --include '*IQ4_NL*' --local-dir ~/ai-models")
    elif r.backend == "metal":
        lines.append("To download the model (Ollama is easiest on macOS):")
        lines.append(f"  ollama pull {r.model.split()[0].lower()}")
    else:
        lines.append("To download the model (Ollama):")
        lines.append(f"  ollama pull {r.model.split()[0].lower()}")
    lines.append("")

    lines.append("Add this to ~/.config/hori/hori.yaml:")
    lines.append("")
    for line in result.config_snippet.strip().splitlines():
        lines.append(f"  {line}")
    lines.append("")

    return "\n".join(lines)


def main():
    """CLI entry point: `python -m hori.detect` or `hori-detect`."""
    result = detect()
    print(format_report(result))


if __name__ == "__main__":
    main()
