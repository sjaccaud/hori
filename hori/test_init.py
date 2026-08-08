"""Tests for hori.init — setup wizard."""
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from hori.init import run_init, _build_config, DATA_DIR
from hori.detect import (
    DetectionResult, SystemInfo, GPUInfo, ModelRecommendation,
)


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Point hori.config at temp paths so tests don't touch real config."""
    config_dir = tmp_path / "config" / "hori"
    config_file = config_dir / "hori.yaml"
    data_dir = tmp_path / "data" / "hori"

    monkeypatch.setattr("hori.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("hori.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("hori.init.CONFIG_DIR", config_dir)
    monkeypatch.setattr("hori.init.CONFIG_FILE", config_file)
    monkeypatch.setattr("hori.init.DATA_DIR", data_dir)

    return config_file, data_dir


@pytest.fixture
def mock_detect(monkeypatch):
    """Mock detect() to return a known result without hitting hardware."""
    gpu = GPUInfo(vendor="amd", name="Test GPU", vram_mb=32768,
                  vram_gb=32.0, backend="rocm")
    system = SystemInfo(os="linux", cpu="Test CPU", ram_gb=64.0,
                        gpus=[gpu], primary_gpu=gpu)
    rec = ModelRecommendation(
        tier="heavy", model="Qwen3.6-27B IQ4_NL", size_gb=15,
        backend="rocm", notes="Test notes",
    )
    result = DetectionResult(
        system=system, recommendation=rec,
        config_snippet="# test snippet",
    )
    monkeypatch.setattr("hori.init.detect", lambda: result)
    return result


class TestRunInit:
    def test_creates_config(self, temp_config, mock_detect):
        config_file, data_dir = temp_config
        success = run_init(quiet=True)
        assert success is True
        assert config_file.exists()
        assert data_dir.exists()

    def test_config_has_recommended_model(self, temp_config, mock_detect):
        config_file, _ = temp_config
        run_init(quiet=True)
        with open(config_file) as f:
            cfg = yaml.safe_load(f)
        assert cfg["llm"]["model"] == "Qwen3.6-27B"

    def test_config_defaults_to_sqlite(self, temp_config, mock_detect):
        config_file, _ = temp_config
        run_init(quiet=True)
        with open(config_file) as f:
            cfg = yaml.safe_load(f)
        assert cfg["memory"]["backend"] == "sqlite"

    def test_overwrite_with_force(self, temp_config, mock_detect):
        config_file, _ = temp_config
        # Create an existing config
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("llm:\n  model: old-model\n")
        # Run with force
        success = run_init(force=True, quiet=True)
        assert success is True
        with open(config_file) as f:
            cfg = yaml.safe_load(f)
        assert cfg["llm"]["model"] == "Qwen3.6-27B"

    def test_decline_overwrite_without_force(self, temp_config, mock_detect, monkeypatch):
        config_file, _ = temp_config
        # Create an existing config
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("llm:\n  model: old-model\n")
        # Simulate user saying "n"
        monkeypatch.setattr("sys.stdin.readline", lambda: "n\n")
        success = run_init(quiet=True)
        assert success is False
        # Config should be unchanged
        with open(config_file) as f:
            cfg = yaml.safe_load(f)
        assert cfg["llm"]["model"] == "old-model"


class TestBuildConfig:
    def test_heavy_tier(self, mock_detect):
        config = _build_config(mock_detect)
        assert config["llm"]["model"] == "Qwen3.6-27B"
        assert config["memory"]["backend"] == "sqlite"

    def test_light_tier(self):
        gpu = GPUInfo(vendor="cpu", name="CPU", vram_mb=0,
                      vram_gb=0.0, backend="cpu")
        system = SystemInfo(os="linux", cpu="Test", ram_gb=16.0,
                            gpus=[], primary_gpu=None)
        rec = ModelRecommendation(
            tier="light", model="Llama-3.1-8B IQ4_NL", size_gb=5,
            backend="cpu", notes="CPU mode",
        )
        result = DetectionResult(system=system, recommendation=rec,
                                  config_snippet="# test")
        config = _build_config(result)
        assert config["llm"]["model"] == "llama3.1:8b"

    def test_nano_tier(self):
        system = SystemInfo(os="linux", cpu="Test", ram_gb=4.0,
                            gpus=[], primary_gpu=None)
        rec = ModelRecommendation(
            tier="nano", model="Qwen2.5-1.5B IQ4_NL", size_gb=1,
            backend="cpu", notes="Minimal",
        )
        result = DetectionResult(system=system, recommendation=rec,
                                  config_snippet="# test")
        config = _build_config(result)
        assert config["llm"]["model"] == "qwen2.5:1.5b"

    def test_config_has_all_sections(self, mock_detect):
        config = _build_config(mock_detect)
        assert "llm" in config
        assert "embedding" in config
        assert "memory" in config
        assert "tts" in config
        assert "service" in config
        assert "paths" in config
        assert "admin" in config
