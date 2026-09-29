"""Tests for the STT engine choice.

The engine is a swap, not a detail: the original openai-whisper FP32 on CPU
took 17.6-27.8s for 1.3s of audio on this host, which is 13-21x real time and
made every reply feel broken. faster-whisper (CTranslate2, int8) measured
7.45s for the same audio on the same host -- a 2.4-3.7x win with the same
model and the same transcription.

What matters for correctness is NOT the speed. It is that the swap preserves
the contract the pipeline depends on:

* the same public functions (`WhisperSTT.load_model`, `.transcribe`, and the
  module-level `transcribe`);
* the same `Result` shape, including `duration_ms`;
* the same language handling (explicit argument beats config);
* the same phonetic-fixes post-processing, which is applied to the returned
  text and not to the model;
* int16 input still accepted and normalised, float32 passed through.

No test loads a model: the engine is injected, so these run in milliseconds
and cannot depend on a 275-second model download.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from src.pipeline import stt
from src.pipeline.utils import Result


class _FakeSegment:
    def __init__(self, text):
        self.text = text


class _FakeEngine:
    """Stands in for faster_whisper.WhisperModel."""

    def __init__(self, model_size, device, compute_type, text="Olá  fantasma. ", **_kw):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.text = text
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append({"audio": audio, **kwargs})
        segments = [_FakeSegment(self.text)]
        info = types.SimpleNamespace(language=kwargs.get("language") or "pt")
        return segments, info


@pytest.fixture()
def fake_backend(monkeypatch):
    """Swap the engine factory and reset the model cache around each test."""
    created = []

    def _factory(model_size, device, compute_type, **kwargs):
        engine = _FakeEngine(model_size, device, compute_type, **kwargs)
        created.append(engine)
        return engine

    monkeypatch.setattr(stt, "_make_engine", _factory)
    stt.WhisperSTT._engine = None
    stt.WhisperSTT._engine_key = None
    yield created
    stt.WhisperSTT._engine = None
    stt.WhisperSTT._engine_key = None

def _pcm(dtype=np.int16):
    return np.zeros(16000, dtype=dtype)


def test_transcribe_returns_text_and_timing(fake_backend):
    result = stt.transcribe(_pcm(), language="pt")
    assert isinstance(result, Result)
    assert result.success
    assert "Olá" in result.data
    assert result.duration_ms is not None
    assert result.duration_ms >= 0


def test_int16_is_normalised_to_float(fake_backend):
    """The pipeline feeds int16 PCM; Whisper needs [-1, 1] floats."""
    stt.transcribe(_pcm(np.int16))
    audio = fake_backend[0].calls[0]["audio"]
    assert audio.dtype == np.float32
    assert audio.min() >= -1.0 and audio.max() <= 1.0


def test_explicit_language_beats_config(fake_backend, monkeypatch):
    monkeypatch.setattr(stt.config.stt, "language", "en")
    stt.transcribe(_pcm(), language="pt")
    assert fake_backend[0].calls[0]["language"] == "pt"


def test_config_language_is_used_when_none_given(fake_backend, monkeypatch):
    monkeypatch.setattr(stt.config.stt, "language", "en")
    stt.transcribe(_pcm())
    assert fake_backend[0].calls[0]["language"] == "en"


def test_phonetic_fixes_are_applied_to_the_text(monkeypatch):
    """The fixes exist because Whisper mishears domain phrases like the
    weather question; they must keep running after the engine swap.

    The fake returns the *mishearing*, so the assertion proves the fix is
    applied to the returned text rather than merely configured.
    """
    monkeypatch.setattr(
        stt.config, "phonetic_fixes", {"nao e que ta otimo": "como esta o tempo"}
    )
    created = []

    def _factory(model_size, device, compute_type, **kw):
        kw["text"] = "nao e que ta otimo"
        engine = _FakeEngine(model_size, device, compute_type, **kw)
        created.append(engine)
        return engine

    monkeypatch.setattr(stt, "_make_engine", _factory)
    stt.WhisperSTT._engine = None
    stt.WhisperSTT._engine_key = None
    try:
        result = stt.transcribe(_pcm(), language="pt")
    finally:
        stt.WhisperSTT._engine = None
        stt.WhisperSTT._engine_key = None
    assert result.data == "como esta o tempo"


def test_model_is_loaded_once_and_cached(fake_backend, monkeypatch):
    """A 7s transcription should not also pay a model load every time."""
    for _ in range(3):
        stt.transcribe(_pcm())
    assert len(fake_backend) == 1
    assert fake_backend[0].calls.__len__() == 3


def test_engine_runs_quantised_on_cpu(fake_backend, monkeypatch):
    """The whole gain comes from int8 + CTranslate2; assert it is what runs."""
    monkeypatch.setattr(stt.config.stt, "model_size", "medium")
    stt.transcribe(_pcm())
    engine = fake_backend[0]
    assert engine.model_size == "medium"
    assert engine.device == "cpu"
    assert engine.compute_type == "int8"


def test_generous_segments_are_split_not_cut(fake_backend):
    """VAD must be off: a wake word is the first part of a longer utterance and
    splitting on silence would drop what the user actually asked for."""
    stt.transcribe(_pcm())
    assert fake_backend[0].calls[0]["vad_filter"] is False


def test_load_model_is_still_public(fake_backend):
    result = stt.WhisperSTT.load_model("medium")
    assert result.success
    assert result.data is fake_backend[0]


def test_no_openai_whisper_import_remains():
    """The swap is incomplete if the old dependency is still imported: it
    would keep ~1GB of torch loaded for nothing."""
    assert "whisper" not in stt.__dict__, "stt.py still holds an openai-whisper handle"
    assert not any(m.startswith("openai_whisper") for m in sys.modules)
