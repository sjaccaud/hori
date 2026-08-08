"""Tests for hori.detect — hardware detection and model tier recommendations."""
import json
import os
import platform
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest

from hori.detect import (
    GPUInfo, SystemInfo, ModelRecommendation, DetectionResult,
    detect_system, recommend_model, generate_config_snippet,
    detect, format_report, TIERS,
)


# --- GPUInfo / SystemInfo construction tests ---

class TestGPUInfo:
    def test_amd_gpu(self):
        gpu = GPUInfo(vendor="amd", name="Radeon AI PRO R9700", vram_mb=32768, vram_gb=32.0, backend="rocm")
        assert gpu.vendor == "amd"
        assert gpu.vram_gb == 32.0
        assert gpu.backend == "rocm"

    def test_nvidia_gpu(self):
        gpu = GPUInfo(vendor="nvidia", name="RTX 4090", vram_mb=24576, vram_gb=24.0, backend="cuda")
        assert gpu.vendor == "nvidia"
        assert gpu.vram_gb == 24.0

    def test_apple_gpu(self):
        gpu = GPUInfo(vendor="apple", name="Apple M1", vram_mb=5734, vram_gb=5.6, backend="metal")
        assert gpu.vendor == "apple"
        assert gpu.backend == "metal"

    def test_cpu_fallback(self):
        gpu = GPUInfo(vendor="cpu", name="CPU", vram_mb=0, vram_gb=0.0, backend="cpu")
        assert gpu.vram_mb == 0


# --- Model recommendation tests ---

class TestRecommendModel:
    def _make_system(self, vram_gb, backend="rocm", ram_gb=64.0):
        if vram_gb > 0:
            gpu = GPUInfo(vendor="amd", name="Test GPU", vram_mb=int(vram_gb * 1024),
                          vram_gb=vram_gb, backend=backend)
            return SystemInfo(os="linux", cpu="Test CPU", ram_gb=ram_gb, gpus=[gpu], primary_gpu=gpu)
        return SystemInfo(os="linux", cpu="Test CPU", ram_gb=ram_gb, gpus=[], primary_gpu=None)

    def test_heavy_tier_32gb(self):
        sys = self._make_system(32.0)
        rec = recommend_model(sys)
        assert rec.tier == "heavy"
        assert "27B" in rec.model
        assert rec.backend == "rocm"

    def test_medium_tier_16gb(self):
        sys = self._make_system(16.0)
        rec = recommend_model(sys)
        assert rec.tier == "medium"
        assert "14B" in rec.model

    def test_light_tier_8gb(self):
        sys = self._make_system(8.0)
        rec = recommend_model(sys)
        assert rec.tier == "light"
        assert "8B" in rec.model

    def test_micro_tier_4gb(self):
        sys = self._make_system(4.0)
        rec = recommend_model(sys)
        assert rec.tier == "micro"
        assert "3B" in rec.model

    def test_nano_tier_2gb(self):
        sys = self._make_system(2.0)
        rec = recommend_model(sys)
        assert rec.tier == "nano"
        assert "1.5B" in rec.model

    def test_boundary_24gb(self):
        """Exactly 24GB should get heavy tier."""
        sys = self._make_system(24.0)
        rec = recommend_model(sys)
        assert rec.tier == "heavy"

    def test_boundary_23_9gb(self):
        """Just under 24GB should get medium tier."""
        sys = self._make_system(23.9)
        rec = recommend_model(sys)
        assert rec.tier == "medium"

    def test_cpu_only_caps_at_light(self):
        """CPU-only should never recommend heavy/medium even with lots of RAM."""
        sys = self._make_system(0, ram_gb=128.0)
        rec = recommend_model(sys)
        assert rec.tier == "light"
        assert rec.backend == "cpu"
        assert "8B" in rec.model

    def test_cpu_only_low_ram(self):
        """CPU-only with low RAM should get nano tier."""
        sys = self._make_system(0, ram_gb=4.0)
        rec = recommend_model(sys)
        # 4GB * 0.5 = 2GB effective → nano tier
        assert rec.tier == "nano"
        assert rec.backend == "cpu"

    def test_nvidia_backend(self):
        sys = self._make_system(24.0, backend="cuda")
        rec = recommend_model(sys)
        assert rec.backend == "cuda"
        assert rec.tier == "heavy"

    def test_apple_backend(self):
        sys = self._make_system(16.0, backend="metal")
        rec = recommend_model(sys)
        assert rec.backend == "metal"
        assert rec.tier == "medium"


# --- Config snippet tests ---

class TestConfigSnippet:
    def test_heavy_snippet(self):
        rec = ModelRecommendation(
            tier="heavy", model="Qwen3.6-27B IQ4_NL", size_gb=15,
            backend="rocm", notes="Test"
        )
        snippet = generate_config_snippet(rec)
        assert "Qwen3.6-27B" in snippet
        assert "llm:" in snippet
        assert "embedding:" in snippet

    def test_light_snippet(self):
        rec = ModelRecommendation(
            tier="light", model="Llama-3.1-8B IQ4_NL", size_gb=5,
            backend="cpu", notes="Test"
        )
        snippet = generate_config_snippet(rec)
        assert "llama3.1:8b" in snippet
        assert "light" in snippet
        assert "llm:" in snippet

    def test_all_tiers_have_valid_yaml(self):
        for min_vram, tier, model, size_gb, notes in TIERS:
            rec = ModelRecommendation(
                tier=tier, model=model, size_gb=size_gb,
                backend="rocm", notes=notes
            )
            snippet = generate_config_snippet(rec)
            assert "llm:" in snippet
            assert "model:" in snippet
            assert "embedding:" in snippet


# --- Integration: detect() ---

class TestDetect:
    def test_detect_returns_result(self):
        result = detect()
        assert isinstance(result, DetectionResult)
        assert isinstance(result.system, SystemInfo)
        assert isinstance(result.recommendation, ModelRecommendation)
        assert isinstance(result.config_snippet, str)

    def test_detect_system_has_os(self):
        sys = detect_system()
        assert sys.os in ("linux", "darwin", "windows")
        assert sys.ram_gb > 0
        assert len(sys.cpu) > 0

    def test_format_report_is_readable(self):
        result = detect()
        report = format_report(result)
        assert "HORI Hardware Detection" in report
        assert "Recommendation" in report
        assert "Tier:" in report
        assert "Model:" in report
        assert "hori.yaml" in report


# --- Mocked detection tests (don't depend on actual hardware) ---

class TestMockedDetection:
    @patch("hori.detect._detect_amd_rocm")
    @patch("hori.detect._detect_nvidia")
    @patch("hori.detect._detect_apple")
    @patch("hori.detect._get_ram_gb")
    @patch("hori.detect._get_cpu_name")
    def test_amd_detected_first(self, mock_cpu, mock_ram, mock_apple, mock_nvidia, mock_amd):
        mock_amd.return_value = [GPUInfo("amd", "Radeon R9700", 32768, 32.0, "rocm")]
        mock_nvidia.return_value = []
        mock_apple.return_value = []
        mock_ram.return_value = 64.0
        mock_cpu.return_value = "Ryzen 7800X3D"

        sys = detect_system()
        assert sys.primary_gpu.vendor == "amd"
        assert sys.primary_gpu.vram_gb == 32.0
        rec = recommend_model(sys)
        assert rec.tier == "heavy"

    @patch("hori.detect._detect_amd_rocm")
    @patch("hori.detect._detect_nvidia")
    @patch("hori.detect._detect_apple")
    @patch("hori.detect._get_ram_gb")
    @patch("hori.detect._get_cpu_name")
    def test_nvidia_fallback(self, mock_cpu, mock_ram, mock_apple, mock_nvidia, mock_amd):
        mock_amd.return_value = []
        mock_nvidia.return_value = [GPUInfo("nvidia", "RTX 4090", 24576, 24.0, "cuda")]
        mock_apple.return_value = []
        mock_ram.return_value = 32.0
        mock_cpu.return_value = "Ryzen 7800X3D"

        sys = detect_system()
        assert sys.primary_gpu.vendor == "nvidia"
        rec = recommend_model(sys)
        assert rec.tier == "heavy"
        assert rec.backend == "cuda"

    @patch("hori.detect._detect_amd_rocm")
    @patch("hori.detect._detect_nvidia")
    @patch("hori.detect._detect_apple")
    @patch("hori.detect._get_ram_gb")
    @patch("hori.detect._get_cpu_name")
    def test_cpu_only_no_gpus(self, mock_cpu, mock_ram, mock_apple, mock_nvidia, mock_amd):
        mock_amd.return_value = []
        mock_nvidia.return_value = []
        mock_apple.return_value = []
        mock_ram.return_value = 16.0
        mock_cpu.return_value = "Intel i7"

        sys = detect_system()
        assert sys.primary_gpu is None
        assert sys.gpus == []
        rec = recommend_model(sys)
        assert rec.backend == "cpu"
        assert rec.tier == "light"  # 16GB * 0.5 = 8GB → light

    @patch("hori.detect._detect_amd_rocm")
    @patch("hori.detect._detect_nvidia")
    @patch("hori.detect._detect_apple")
    @patch("hori.detect._get_ram_gb")
    @patch("hori.detect._get_cpu_name")
    def test_multiple_gpus_picks_highest_vram(self, mock_cpu, mock_ram, mock_apple, mock_nvidia, mock_amd):
        mock_amd.return_value = [GPUInfo("amd", "Radeon R9700", 32768, 32.0, "rocm")]
        mock_nvidia.return_value = [GPUInfo("nvidia", "RTX 3060", 12288, 12.0, "cuda")]
        mock_apple.return_value = []
        mock_ram.return_value = 64.0
        mock_cpu.return_value = "Ryzen 7800X3D"

        sys = detect_system()
        assert len(sys.gpus) == 2
        assert sys.primary_gpu.vram_gb == 32.0  # AMD has more VRAM
        assert sys.primary_gpu.vendor == "amd"
