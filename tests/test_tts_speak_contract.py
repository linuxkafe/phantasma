"""The speak path must not hand back audio samples.

_speak reads the cache, plays, and keeps the mic shut. Its Result carries no
samples by design. Both call sites used to go on to unpack .data into
(samples, rate) and play it a second time, which raised TypeError on every
skill response and killed the wake-word thread: the device answered exactly
one command and was deaf from then on, while the rest of the suite stayed
green. Nothing here mocks the TTS, so the regression cannot hide behind a
stub that happens to return audio.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import assistant  # noqa: E402


class _FakeResult:
    def __init__(self, success=True, data=None, error=None):
        self.success = success
        self.data = data
        self.error = error


class _FakeCapture:
    def __init__(self):
        self.stops = 0
        self.starts = 0

    def stop(self):
        self.stops += 1
        return _FakeResult()

    def start(self):
        self.starts += 1
        return _FakeResult()


def _pipeline_with_speak(monkeypatch):
    """A stand-in with just the surface _speak touches."""
    obj = object.__new__(assistant.PhantasmaPipeline)
    obj.audio_capture = _FakeCapture()
    obj._speaking = False
    played = []
    monkeypatch.setattr("audio_utils.play_tts", lambda t, use_cache=True: played.append(t))
    monkeypatch.setattr(assistant.time, "sleep", lambda s: None)
    return obj, played


class TestSpeakReturnsNoSamples:
    def test_result_data_is_none(self, monkeypatch):
        """The contract: no samples, so no caller may unpack them."""
        obj, played = _pipeline_with_speak(monkeypatch)
        result = obj._speak("Liga a luz da sala.")
        assert result.success
        assert result.data is None, "_speak plays the audio itself; it returns no samples"
        assert played == ["Liga a luz da sala."]

    def test_caller_would_have_crashed(self, monkeypatch):
        """Pin the exact failure: unpacking this Result is a TypeError."""
        obj, _ = _pipeline_with_speak(monkeypatch)
        result = obj._speak("ok")
        with pytest.raises(TypeError):
            audio_data, sample_rate = result.data  # noqa: F841

    def test_capture_is_held_shut_while_speaking(self, monkeypatch):
        obj, _ = _pipeline_with_speak(monkeypatch)
        obj._speak("Luz da sala ligada.")
        assert obj.audio_capture.stops == 1, "mic must be closed before talking"
        assert obj.audio_capture.starts == 1, "and reopened after"
        assert obj._speaking is False, "flag must be cleared even on the way out"

    def test_speaking_flag_cleared_on_failure(self, monkeypatch):
        def boom(text, use_cache=True):
            raise RuntimeError("no audio device")

        obj, _ = _pipeline_with_speak(monkeypatch)
        monkeypatch.setattr("audio_utils.play_tts", boom)
        result = obj._speak("oi")
        assert not result.success
        assert obj._speaking is False, "a failure must not leave the mic shut forever"


class TestNoUnpackingInCallers:
    def test_call_sites_do_not_touch_speak_data(self):
        src = (ROOT / "assistant.py").read_text(encoding="utf-8")
        body = src.split("def _speak(self, text: str", 1)[1]
        assert "tts_result.data" not in body, (
            "a caller is unpacking .data from _speak's Result; it is None by design"
        )

    def test_call_sites_do_not_double_play(self):
        src = (ROOT / "assistant.py").read_text(encoding="utf-8")
        after = src.split("def _speak(self, text: str", 1)[1]
        assert after.count("self.audio_playback.play(") == 0, (
            "_speak already plays; a second play would double the audio"
        )


class TestSpeechSeconds:
    def test_short_reply_still_holds_the_mic(self):
        assert assistant._speech_seconds("Sim.") >= 0.6

    def test_scales_with_length(self):
        short = assistant._speech_seconds("Sim.")
        long = assistant._speech_seconds("Luz da sala ligada. " * 10)
        assert long > short * 5
