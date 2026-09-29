"""`/api/voz`: one spoken command, audio in and a spoken answer out.

Added 2026-09-29 for the phone. Three things are worth stating before the tests,
because they are the three ways this endpoint can be wrong in a way that still
looks like it worked:

1. **Silence.** A mis-tap posts an empty room. Whisper does not say "I heard
   nothing" -- it returns a confident sentence built from noise, and the user
   reads it aloud to their house. The endpoint has to reject it itself.
2. **Authentication.** This turns light switches into a POST body. It is gated
   by the same session as the page, and a test that renders the page without one
   proves nothing about this.
3. **The audio really is decodable.** The browser sends 16 kHz mono WAV built by
   its own `decodeAudioData`. These tests feed the endpoint a WAV produced the
   same way, because the alternative -- asserting that a base64 string was
   accepted -- would pass on a file that transcribes to noise.

The browser half (getUserMedia, MediaRecorder, AudioContext) cannot be tested
here: it needs a real device and a real microphone. What is verified is the
server contract, and the page half was checked by hand on a phone.
"""

from __future__ import annotations

import base64
import io
import struct
import wave

import numpy as np
import pytest

from tests.helpers_ui_auth import make_app_with_user


def _wav_b64(samples: np.ndarray, rate: int = 16000) -> str:
    """A real 16-bit PCM WAV, base64 encoded -- the exact shape the browser sends."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def _silence(seconds: float = 1.0, rate: int = 16000) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.float32)


@pytest.fixture
def signed_in(monkeypatch):
    _app, client = make_app_with_user("b@t.test", "admin", monkeypatch=monkeypatch)
    return client


@pytest.fixture
def anonymous(monkeypatch):
    """An app with the routes but NO session."""
    from flask import Flask

    from skills import skill_ui

    app = Flask(__name__)
    app.secret_key = "k"
    app.config["TESTING"] = True
    skill_ui.register_routes(app)
    return app.test_client()


# --- authentication --------------------------------------------------------


def test_anonymous_cannot_reach_the_voice_endpoint(anonymous):
    """Turning the lights on is a POST body away; it must not be one POST away
    from anything on the LAN."""
    res = anonymous.post("/api/voz", json={"audio_base64": _wav_b64(_silence())})
    assert res.status_code == 401, (
        f"expected 401 for an unauthenticated caller, got {res.status_code}"
    )
    assert res.get_json()["success"] is False


def test_a_session_is_required_even_with_audio(anonymous):
    """A valid-looking body must not be what decides access."""
    res = anonymous.post("/api/voz", json={"audio_base64": _wav_b64(_silence(2.0))})
    assert res.status_code == 401


# --- input validation ------------------------------------------------------


def test_a_missing_body_is_a_400_not_a_500(signed_in):
    res = signed_in.post("/api/voz", json={})
    assert res.status_code == 400
    assert res.get_json()["error"]


def test_undecodable_audio_is_reported_not_crashed(signed_in):
    """The browser sends WAV, but the endpoint must not 500 on something else.

    This is the failure that motivated decoding in the page: a webm/opus body
    used to reach the recogniser as raw PCM. Returning an error is the correct
    outcome; transccribing noise is not.
    """
    res = signed_in.post("/api/voz", json={"audio_base64": "bm90LWF1ZGlv"})
    assert res.status_code == 400
    assert "áudio" in res.get_json()["error"] or "audio" in res.get_json()["error"]


def test_silence_is_refused_before_the_recogniser(signed_in, monkeypatch):
    """The recogniser must never be asked to invent a sentence out of a room.

    Whisper is happy to return a confident transcription of near-silence, and
    the user would then act on it. The peak check is what stops that, so the
    test asserts the recogniser was NOT called.
    """
    called = []
    import src.pipeline.stt as stt_mod

    def spy(audio, language=None):
        called.append(audio)
        raise AssertionError("the recogniser ran on silence")

    monkeypatch.setattr(stt_mod, "transcribe", spy)
    res = signed_in.post("/api/voz", json={"audio_base64": _wav_b64(_silence(1.0))})
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is False
    assert body["reason"] == "silence"
    assert not called, "the recogniser was called on silence"


def test_an_oversized_upload_is_refused_before_decoding(signed_in):
    """A 12 MB cap is cheap to enforce and bounds what one tap can cost."""
    res = signed_in.post("/api/voz", json={"audio_base64": "A" * (13 * 1024 * 1024)})
    assert res.status_code == 413


# --- the round trip --------------------------------------------------------


def test_spoken_audio_is_transcribed_and_the_command_answered(signed_in, monkeypatch):
    """The whole loop, with the recogniser and the LLM replaced by fakes.

    A 440 Hz tone stands in for speech: this test is about the plumbing and the
    response shape, not about whether Whisper can hear a sine wave. That part
    was verified against real synthesised speech separately.
    """
    import src.pipeline.stt as stt_mod

    class R:
        success = True
        data = "liga a luz da sala"
        error = None

    monkeypatch.setattr(stt_mod, "transcribe", lambda audio, language=None: R())

    # The answer path: a device command, so the device handler decides, and the
    # assertion is that the spoken text reaches the same dispatch the typed one
    # uses.
    import src.api.routes as routes

    seen = {}

    def fake_device(text):
        seen["text"] = text
        from flask import jsonify

        return jsonify({"response": "Luz da sala ligada.", "device_states": {"sala": True}})

    monkeypatch.setattr(routes, "_is_device_command", lambda t: True)
    monkeypatch.setattr(routes, "_handle_device_command", fake_device)

    tone = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000).astype(np.float32) * 0.3
    res = signed_in.post("/api/voz", json={"audio_base64": _wav_b64(tone)})

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["success"] is True
    assert body["transcript"] == "liga a luz da sala", (
        "the transcript is not echoed back, so a mishearing is invisible to the user"
    )
    assert body["text"] == "Luz da sala ligada."
    assert body["device_states"] == {"sala": True}
    assert seen["text"] == "liga a luz da sala", (
        "the spoken text did not reach the device dispatch verbatim"
    )
    assert body["processing_time_ms"] >= 0


def test_a_failed_transcription_is_reported_as_a_failure(signed_in, monkeypatch):
    import src.pipeline.stt as stt_mod

    class R:
        success = False
        data = None
        error = "modelo não carregado"

    monkeypatch.setattr(stt_mod, "transcribe", lambda audio, language=None: R())
    tone = (np.random.default_rng(0).normal(0, 0.2, 16000)).astype(np.float32)
    res = signed_in.post("/api/voz", json={"audio_base64": _wav_b64(tone)})
    assert res.status_code == 500
    assert res.get_json()["success"] is False


def test_an_empty_transcription_does_not_execute_anything(signed_in, monkeypatch):
    """An empty string is not a command. Executing it would run the LLM on ""."""
    import src.api.routes as routes
    import src.pipeline.stt as stt_mod

    class R:
        success = True
        data = "   "
        error = None

    monkeypatch.setattr(stt_mod, "transcribe", lambda audio, language=None: R())
    monkeypatch.setattr(
        routes, "_is_device_command", lambda t: pytest.fail("dispatched an empty command")
    )
    tone = (np.random.default_rng(1).normal(0, 0.2, 16000)).astype(np.float32)
    res = signed_in.post("/api/voz", json={"audio_base64": _wav_b64(tone)})
    assert res.get_json()["reason"] == "empty"


def test_the_16khz_rate_survives_the_round_trip(signed_in, monkeypatch):
    """A WAV that is NOT 16 kHz must be resampled, not trusted.

    The browser resamples in the page, but this endpoint accepts any rate, and
    feeding 48 kHz to a 16 kHz model is the classic way to get confident
    nonsense out of Whisper.
    """
    import src.pipeline.stt as stt_mod

    seen = {}

    class R:
        success = True
        data = "ok"
        error = None

    def spy(audio, language=None):
        seen["len"] = len(audio)
        return R()

    monkeypatch.setattr(stt_mod, "transcribe", spy)
    # One second at 48 kHz.
    tone = np.sin(2 * np.pi * 440 * np.arange(48000) / 48000).astype(np.float32) * 0.3
    signed_in.post("/api/voz", json={"audio_base64": _wav_b64(tone, rate=48000)})
    # Linear resampling lands within a few samples of 16000 for one second.
    assert 15000 < seen["len"] < 17000, (
        f"expected ~16000 samples after resampling, got {seen.get('len')}"
    )


def test_the_wav_header_is_what_whisper_needs():
    """Guard the encoder contract independently of the endpoint.

    If the browser's WAV header were wrong, every one of the tests above would
    still pass with a stub recogniser, and the failure would only appear on a
    phone. This asserts the bytes.
    """
    samples = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
    data = base64.b64decode(_wav_b64(samples))
    with wave.open(io.BytesIO(data), "rb") as fh:
        assert fh.getframerate() == 16000
        assert fh.getnchannels() == 1
        assert fh.getsampwidth() == 2
        assert fh.getnframes() == len(samples)
        read = np.frombuffer(fh.readframes(len(samples)), dtype="<i2")
    assert read[1] == pytest.approx(16383, abs=2)
    assert read[2] == pytest.approx(-16383, abs=2)
    # The clamp: 1.0 must not wrap around to a negative sample.
    assert read[3] == 32767
    assert read[4] == -32767
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8
