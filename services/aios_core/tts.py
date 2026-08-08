"""
TTS (Text-to-Speech) module for HORI.

Provides natural-sounding voice synthesis on CPU with low latency.
Supports pluggable backends selected via the hori.yaml config (tts.backend):

- **kokoro** (default): Kokoro-82M, #1 TTS Arena (Jan 2026). Apache 2.0.
  82M params, StyleTTS 2 + ISTFTNet, 24kHz output, 6x realtime on CPU.
  British female voices: bf_alice, bf_emma, bf_isabella, bf_lily.
  Default: bf_emma.

- **piper** (fallback): Piper neural TTS, VITS-era, 22.05kHz, 45x realtime.
  Kept as a fallback for compatibility and edge deployments.

- **none**: Disables TTS entirely. HORI operates text-only.

Usage:
    from services.aios_core.tts import synthesize_speech
    wav_bytes = synthesize_speech("Hello world")
"""
import io
import logging
import os
import wave
from pathlib import Path
from typing import Optional, Protocol

from hori.config import (
    TTS_BACKEND,
    KOKORO_MODEL_PATH,
    KOKORO_VOICES_PATH,
    KOKORO_DEFAULT_VOICE,
    KOKORO_LANG,
    PIPER_VOICE_DIR,
    PIPER_DEFAULT_VOICE,
)

logger = logging.getLogger(__name__)

# Active default voice depends on backend
DEFAULT_VOICE = KOKORO_DEFAULT_VOICE if TTS_BACKEND == "kokoro" else PIPER_DEFAULT_VOICE


class TTSBackend(Protocol):
    """Protocol for TTS backends. All backends implement this interface."""

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        """Synthesize text to WAV bytes.

        Args:
            text: Text to synthesize
            voice: Voice identifier
            speed: Speed multiplier (1.0 = normal, <1.0 = faster, >1.0 = slower)

        Returns:
            WAV audio bytes (16-bit PCM, mono)
        """
        ...

    def list_voices(self) -> list:
        """List available voice models."""
        ...


# --- Kokoro Backend ---

_kokoro_instance = None


def _get_kokoro():
    """Load and cache the Kokoro model instance."""
    global _kokoro_instance
    if _kokoro_instance is not None:
        return _kokoro_instance

    from kokoro_onnx import Kokoro

    model_path = KOKORO_MODEL_PATH
    voices_path = KOKORO_VOICES_PATH
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Kokoro model not found: {model_path}")
    if not os.path.exists(voices_path):
        raise FileNotFoundError(f"Kokoro voices not found: {voices_path}")

    _kokoro_instance = Kokoro(model_path, voices_path)
    logger.info(f"Loaded Kokoro model: {model_path}")
    return _kokoro_instance


def _synthesize_kokoro(text: str, voice: str, speed: float = 1.0) -> bytes:
    """Synthesize using Kokoro-82M."""
    import numpy as np

    kokoro = _get_kokoro()
    audio, sample_rate = kokoro.create(
        text, voice=voice, speed=speed, lang=KOKORO_LANG
    )

    # Convert float32 [-1, 1] to 16-bit PCM WAV
    audio_int16 = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_int16.tobytes())
    return buf.getvalue()


def _list_kokoro_voices() -> list:
    """List available Kokoro voices."""
    voices = []
    try:
        kokoro = _get_kokoro()
        voice_names = kokoro.get_voices()
        for name in sorted(voice_names):
            voices.append({
                "name": name,
                "backend": "kokoro",
                "model_path": KOKORO_MODEL_PATH,
            })
    except Exception as e:
        logger.warning(f"Failed to list Kokoro voices: {e}")
    return voices


# --- Piper Backend (fallback) ---

_piper_voice_cache = {}


def _get_piper_voice(voice_name: str):
    """Load and cache a Piper voice model."""
    if voice_name in _piper_voice_cache:
        return _piper_voice_cache[voice_name]

    model_path = PIPER_VOICE_DIR / f"{voice_name}.onnx"
    if not model_path.exists():
        raise FileNotFoundError(f"Piper voice not found: {model_path}")

    from piper import PiperVoice
    voice = PiperVoice.load(str(model_path))
    _piper_voice_cache[voice_name] = voice
    logger.info(f"Loaded Piper voice: {voice_name}")
    return voice


def _synthesize_piper(text: str, voice: str, speed: float = 1.0) -> bytes:
    """Synthesize using Piper."""
    from piper.config import SynthesisConfig

    voice_obj = _get_piper_voice(voice)

    # Piper uses length_scale (inverse of speed): <1.0 = faster, >1.0 = slower
    syn_config = SynthesisConfig(length_scale=1.0 / speed) if speed != 1.0 else None

    chunks = list(
        voice_obj.synthesize(text, syn_config) if syn_config else voice_obj.synthesize(text)
    )
    if not chunks:
        return b""

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        chunk = chunks[0]
        wav_file.setnchannels(chunk.sample_channels)
        wav_file.setsampwidth(chunk.sample_width)
        wav_file.setframerate(chunk.sample_rate)
        for c in chunks:
            wav_file.writeframes(c.audio_int16_bytes)
    return buf.getvalue()


def _list_piper_voices() -> list:
    """List available Piper voice models."""
    voices = []
    if PIPER_VOICE_DIR.exists():
        for f in PIPER_VOICE_DIR.glob("*.onnx"):
            name = f.stem
            config_file = f.with_suffix(".onnx.json")
            voices.append({
                "name": name,
                "backend": "piper",
                "model_path": str(f),
                "config_path": str(config_file) if config_file.exists() else "",
            })
    return voices


# --- Public API (unchanged signature for backward compatibility) ---


def synthesize_speech(
    text: str,
    voice_name: str = None,
    length_scale: float = None,
    noise_scale: float = None,
    noise_w_scale: float = None,
) -> bytes:
    """Synthesize text to speech and return WAV audio bytes.

    Args:
        text: The text to synthesize
        voice_name: Optional voice name (defaults to backend default)
        length_scale: Speed control (Piper). <1.0 = faster, >1.0 = slower.
            For Kokoro, this is converted to speed (inverse).
        noise_scale: Piper expressiveness. 0.0 = monotone, 1.0 = very expressive
        noise_w_scale: Piper cadence variation. 0.0 = robotic, 1.0 = natural

    Returns:
        WAV audio bytes (16-bit PCM, mono, 24kHz for Kokoro / 22.05kHz for Piper)
    """
    if not text or not text.strip():
        return b""

    # Limit text length to avoid extremely long synthesis
    text = text[:2000]

    voice = voice_name or DEFAULT_VOICE

    # Convert length_scale to speed for the unified backend interface
    # length_scale < 1.0 = faster, so speed = 1/length_scale
    if length_scale is not None and length_scale > 0:
        speed = 1.0 / length_scale
    else:
        speed = 1.0

    if TTS_BACKEND == "kokoro":
        return _synthesize_kokoro(text, voice, speed)
    else:
        # Piper fallback — pass noise scales through via direct call
        return _synthesize_piper_with_scales(
            text, voice, speed, noise_scale, noise_w_scale
        )


def _synthesize_piper_with_scales(
    text: str, voice: str, speed: float, noise_scale: float, noise_w_scale: float
) -> bytes:
    """Piper synthesis with full config support."""
    from piper.config import SynthesisConfig

    voice_obj = _get_piper_voice(voice)
    syn_config_kwargs = {}
    if speed != 1.0:
        syn_config_kwargs["length_scale"] = 1.0 / speed
    if noise_scale is not None:
        syn_config_kwargs["noise_scale"] = noise_scale
    if noise_w_scale is not None:
        syn_config_kwargs["noise_w_scale"] = noise_w_scale

    syn_config = SynthesisConfig(**syn_config_kwargs) if syn_config_kwargs else None
    chunks = list(
        voice_obj.synthesize(text, syn_config) if syn_config else voice_obj.synthesize(text)
    )
    if not chunks:
        return b""

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        chunk = chunks[0]
        wav_file.setnchannels(chunk.sample_channels)
        wav_file.setsampwidth(chunk.sample_width)
        wav_file.setframerate(chunk.sample_rate)
        for c in chunks:
            wav_file.writeframes(c.audio_int16_bytes)
    return buf.getvalue()


def list_voices() -> list:
    """List available voice models for the active backend."""
    if TTS_BACKEND == "kokoro":
        return _list_kokoro_voices()
    else:
        return _list_piper_voices()


def get_backend() -> str:
    """Return the active TTS backend name."""
    return TTS_BACKEND
