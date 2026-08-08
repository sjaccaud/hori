"""Tests for the TTS module.

Tests adapt to the active backend (kokoro or piper) via the config.
Backend-specific tests are gated so they only run when that backend is active.
Tests that need actual model files are skipped when models are not present.
"""
import io
import os
import wave
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from services.aios_core.tts import (
    synthesize_speech,
    list_voices,
    get_backend,
    TTS_BACKEND,
    KOKORO_MODEL_PATH,
)


# --- Model availability checks ---

_TTS_MODELS_AVAILABLE = False
if TTS_BACKEND == "kokoro":
    _TTS_MODELS_AVAILABLE = os.path.exists(KOKORO_MODEL_PATH)
elif TTS_BACKEND == "piper":
    from services.aios_core.tts import PIPER_VOICE_DIR
    _TTS_MODELS_AVAILABLE = any(PIPER_VOICE_DIR.glob("*.onnx")) if PIPER_VOICE_DIR.exists() else False

_skip_no_models = pytest.mark.skipif(
    not _TTS_MODELS_AVAILABLE,
    reason="TTS model files not found (expected at config paths)"
)


# --- Backend-agnostic tests (run for all backends) ---


@_skip_no_models
def test_synthesize_speech_returns_wav():
    """synthesize_speech should return valid WAV bytes."""
    audio = synthesize_speech("Hello world test")
    assert len(audio) > 100, "Audio too short"
    # Check it's a valid WAV file
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getnchannels() == 1, "Should be mono"
        assert wav.getsampwidth() == 2, "Should be 16-bit"
        # Sample rate depends on backend: Kokoro=24000, Piper=22050
        assert wav.getframerate() in (22050, 24000, 44100), (
            f"Unexpected sample rate: {wav.getframerate()}"
        )


def test_synthesize_speech_empty_text():
    """Empty text should return empty bytes."""
    assert synthesize_speech("") == b""
    assert synthesize_speech("   ") == b""


@_skip_no_models
def test_synthesize_speech_truncates_long_text():
    """Very long text should be truncated to prevent timeouts."""
    long_text = "A" * 5000
    audio = synthesize_speech(long_text)
    # Should still produce audio (truncated to 2000 chars)
    assert len(audio) > 100


def test_list_voices():
    """list_voices should return available voice models."""
    voices = list_voices()
    assert isinstance(voices, list)
    # Should find at least one voice
    if voices:
        assert "name" in voices[0]
        assert "model_path" in voices[0]


def test_get_backend():
    """get_backend should return the active backend name."""
    backend = get_backend()
    assert backend in ("kokoro", "piper"), f"Unknown backend: {backend}"


# --- Kokoro-specific tests (only run when kokoro is active) ---


pytestmark_kokoro = pytest.mark.skipif(
    TTS_BACKEND != "kokoro" or not _TTS_MODELS_AVAILABLE,
    reason="Kokoro backend not active or model files not found"
)


@pytest.mark.skipif(TTS_BACKEND != "kokoro" or not _TTS_MODELS_AVAILABLE, reason="Kokoro backend not active or model files not found")
def test_kokoro_synthesize_with_default_voice():
    """Kokoro should produce 24kHz audio with the default voice."""
    audio = synthesize_speech("Hello, this is a test.")
    assert len(audio) > 100
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getframerate() == 24000, "Kokoro should output 24kHz"
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2


@pytest.mark.skipif(TTS_BACKEND != "kokoro" or not _TTS_MODELS_AVAILABLE, reason="Kokoro backend not active or model files not found")
def test_kokoro_list_voices_includes_british_female():
    """Kokoro should list British female voices."""
    voices = list_voices()
    voice_names = [v["name"] for v in voices]
    assert "bf_emma" in voice_names, "bf_emma should be available"
    assert "bf_isabella" in voice_names, "bf_isabella should be available"


@pytest.mark.skipif(TTS_BACKEND != "kokoro" or not _TTS_MODELS_AVAILABLE, reason="Kokoro backend not active or model files not found")
def test_kokoro_synthesize_different_voices():
    """Multiple Kokoro voices should produce audio."""
    for voice_name in ["bf_emma", "bf_isabella"]:
        audio = synthesize_speech("Test sentence.", voice_name=voice_name)
        assert len(audio) > 100, f"Voice {voice_name} produced no audio"


@pytest.mark.skipif(TTS_BACKEND != "kokoro" or not _TTS_MODELS_AVAILABLE, reason="Kokoro backend not active or model files not found")
def test_kokoro_speed_control():
    """Speed control via length_scale should work."""
    # length_scale < 1.0 = faster, so speed > 1.0
    audio_fast = synthesize_speech("Test sentence.", length_scale=0.5)
    audio_normal = synthesize_speech("Test sentence.", length_scale=1.0)
    assert len(audio_fast) > 100
    assert len(audio_normal) > 100
    # Faster speech should produce shorter audio (fewer samples)
    assert len(audio_fast) < len(audio_normal), (
        "Faster speech (length_scale=0.5) should produce shorter audio"
    )


# --- Piper-specific tests (only run when piper is active) ---


@pytest.mark.skipif(TTS_BACKEND != "piper" or not _TTS_MODELS_AVAILABLE, reason="Piper backend not active or model files not found")
def test_piper_voice_caching():
    """Loading the same Piper voice twice should use the cache."""
    from services.aios_core import tts

    tts._piper_voice_cache.clear()
    voice1 = tts._get_piper_voice("en_US-amy-medium")
    voice2 = tts._get_piper_voice("en_US-amy-medium")
    assert voice1 is voice2, "Voice should be cached"


@pytest.mark.skipif(TTS_BACKEND != "piper" or not _TTS_MODELS_AVAILABLE, reason="Piper backend not active or model files not found")
def test_piper_synthesize_different_voices():
    """Both Piper voices should produce audio."""
    for voice_name in ["en_US-amy-medium", "en_US-danny-low"]:
        audio = synthesize_speech("Test", voice_name=voice_name)
        assert len(audio) > 100, f"Voice {voice_name} produced no audio"
