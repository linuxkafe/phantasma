"""Tests for audio pipeline components."""

import queue
from unittest.mock import MagicMock, patch

import numpy as np

from src.pipeline.audio import (
    AudioCapture,
    AudioFrame,
    AudioPlayback,
    HotwordDetector,
    VADProcessor,
)


class TestAudioFrame:
    """Test AudioFrame dataclass."""

    def test_audio_frame_creation(self):
        """Test creating AudioFrame with required fields."""
        data = np.zeros(1600, dtype=np.int16)
        frame = AudioFrame(data=data, timestamp=123.456)
        assert frame.data is data
        assert frame.timestamp == 123.456
        assert frame.is_speech is False

    def test_audio_frame_with_speech(self):
        """Test AudioFrame with is_speech=True."""
        data = np.zeros(1600, dtype=np.int16)
        frame = AudioFrame(data=data, timestamp=123.456, is_speech=True)
        assert frame.is_speech is True


class TestVADProcessor:
    """Test VADProcessor WebRTC VAD wrapper."""

    def test_init_default_params(self):
        """Test VADProcessor initialization with defaults."""
        vad = VADProcessor()
        assert vad.sample_rate == 16000
        assert vad.frame_duration_ms == 30
        assert vad.frame_size == 480  # 16000 * 30 / 1000
        assert vad._bytes_per_frame == 960  # 480 * 2

    def test_init_custom_params(self):
        """Test VADProcessor with custom parameters."""
        vad = VADProcessor(aggressiveness=3, sample_rate=8000, frame_duration_ms=10)
        assert vad.sample_rate == 8000
        assert vad.frame_duration_ms == 10
        assert vad.frame_size == 80  # 8000 * 10 / 1000

    def test_is_speech_exact_frame_size(self):
        """Test is_speech with exact frame size."""
        vad = VADProcessor(sample_rate=16000, frame_duration_ms=30)
        frame = np.zeros(480, dtype=np.int16)
        # Should not raise, returns bool
        result = vad.is_speech(frame)
        assert isinstance(result, bool)

    def test_is_speech_pads_short_frame(self):
        """Test is_speech pads frame smaller than frame_size."""
        vad = VADProcessor(sample_rate=16000, frame_duration_ms=30)
        frame = np.zeros(100, dtype=np.int16)  # Smaller than 480
        result = vad.is_speech(frame)
        assert isinstance(result, bool)

    def test_is_speech_truncates_long_frame(self):
        """Test is_speech truncates frame larger than frame_size."""
        vad = VADProcessor(sample_rate=16000, frame_duration_ms=30)
        frame = np.zeros(1000, dtype=np.int16)  # Larger than 480
        result = vad.is_speech(frame)
        assert isinstance(result, bool)

    def test_process_chunk_splits_into_frames(self):
        """Test process_chunk splits audio into correct number of frames."""
        vad = VADProcessor(sample_rate=16000, frame_duration_ms=30)
        # 2 frames worth of audio
        chunk = np.zeros(960, dtype=np.int16)
        frames = vad.process_chunk(chunk)
        assert len(frames) == 2
        assert all(isinstance(f, AudioFrame) for f in frames)
        assert all(len(f.data) == 480 for f in frames)

    def test_process_chunk_discards_partial_frame(self):
        """Test process_chunk discards incomplete final frame."""
        vad = VADProcessor(sample_rate=16000, frame_duration_ms=30)
        # 2.5 frames worth
        chunk = np.zeros(1200, dtype=np.int16)
        frames = vad.process_chunk(chunk)
        assert len(frames) == 2  # Only complete frames


class TestHotwordDetector:
    """Test HotwordDetector openWakeWord wrapper."""

    @patch("src.pipeline.audio.openwakeword.get_pretrained_model_paths")
    @patch("src.pipeline.audio.openwakeword.model.Model")
    def test_init_loads_models(self, mock_model_class, mock_get_paths):
        """Test HotwordDetector loads requested models."""
        mock_get_paths.return_value = [
            "/models/hey_jarvis.onnx",
            "/models/alexa.onnx",
            "/models/hey_mycroft.onnx",
        ]
        mock_model_instance = MagicMock()
        mock_model_class.return_value = mock_model_instance

        HotwordDetector(models=["hey_jarvis", "alexa"])

        # Should filter to only requested models
        call_args = mock_model_class.call_args
        assert call_args is not None
        model_paths = call_args.kwargs["wakeword_model_paths"]
        assert len(model_paths) == 2
        assert any("hey_jarvis" in p for p in model_paths)
        assert any("alexa" in p for p in model_paths)

    @patch("src.pipeline.audio.openwakeword.get_pretrained_model_paths")
    @patch("src.pipeline.audio.openwakeword.model.Model")
    @patch("src.pipeline.audio.os.path.isfile")
    def test_init_loads_custom_model_paths(
        self, mock_isfile, mock_model_class, mock_get_paths
    ):
        """Test HotwordDetector loads custom .onnx paths directly."""
        custom = ["/app/models/hey_fantasma.onnx", "/app/models/ola_fantasma.onnx"]
        mock_isfile.return_value = True
        mock_model_instance = MagicMock()
        mock_model_class.return_value = mock_model_instance

        HotwordDetector(models=custom)

        # Custom paths resolved as files -> passed straight to openWakeWord,
        # the pretrained fallback is NOT consulted.
        call_args = mock_model_class.call_args
        assert call_args is not None
        model_paths = call_args.kwargs["wakeword_model_paths"]
        assert model_paths == custom
        mock_get_paths.assert_not_called()

    @patch("src.pipeline.audio.openwakeword.get_pretrained_model_paths")
    @patch("src.pipeline.audio.openwakeword.model.Model")
    @patch("src.pipeline.audio.os.path.isfile")
    def test_init_falls_back_to_pretrained_when_paths_missing(
        self, mock_isfile, mock_model_class, mock_get_paths
    ):
        """Test HotwordDetector falls back to pretrained when custom paths absent."""
        mock_isfile.return_value = False
        mock_get_paths.return_value = ["/models/hey_jarvis.onnx"]
        mock_model_instance = MagicMock()
        mock_model_class.return_value = mock_model_instance

        HotwordDetector(models=["/app/models/missing.onnx"])

        call_args = mock_model_class.call_args
        model_paths = call_args.kwargs["wakeword_model_paths"]
        assert model_paths == ["/models/hey_jarvis.onnx"]

    @patch("src.pipeline.audio.openwakeword.get_pretrained_model_paths")
    @patch("src.pipeline.audio.openwakeword.model.Model")
    def test_process_returns_detection(self, mock_model_class, mock_get_paths):
        """Test process returns (True, model_name) on detection."""
        mock_get_paths.return_value = ["/models/hey_jarvis.onnx"]
        mock_model_instance = MagicMock()
        mock_model_instance.predict.return_value = {"hey_jarvis": 0.8, "alexa": 0.1}
        mock_model_class.return_value = mock_model_instance

        detector = HotwordDetector(models=["hey_jarvis"], threshold=0.5)
        audio_chunk = np.zeros(1600, dtype=np.int16)

        detected, model = detector.process(audio_chunk)

        assert detected is True
        assert model == "hey_jarvis"

    @patch("src.pipeline.audio.openwakeword.get_pretrained_model_paths")
    @patch("src.pipeline.audio.openwakeword.model.Model")
    def test_process_no_detection_below_threshold(
        self, mock_model_class, mock_get_paths
    ):
        """Test process returns (False, None) when below threshold."""
        mock_get_paths.return_value = ["/models/hey_jarvis.onnx"]
        mock_model_instance = MagicMock()
        mock_model_instance.predict.return_value = {"hey_jarvis": 0.3}
        mock_model_class.return_value = mock_model_instance

        detector = HotwordDetector(models=["hey_jarvis"], threshold=0.5)
        audio_chunk = np.zeros(1600, dtype=np.int16)

        detected, model = detector.process(audio_chunk)

        assert detected is False
        assert model is None

    @patch("src.pipeline.audio.openwakeword.get_pretrained_model_paths")
    @patch("src.pipeline.audio.openwakeword.model.Model")
    def test_process_respects_cooldown(self, mock_model_class, mock_get_paths):
        """Test process respects cooldown period."""
        mock_get_paths.return_value = ["/models/hey_jarvis.onnx"]
        mock_model_instance = MagicMock()
        mock_model_instance.predict.return_value = {"hey_jarvis": 0.8}
        mock_model_class.return_value = mock_model_instance

        detector = HotwordDetector(models=["hey_jarvis"], threshold=0.5)
        audio_chunk = np.zeros(1600, dtype=np.int16)

        # First detection
        detected1, _ = detector.process(audio_chunk)
        assert detected1 is True

        # Immediate second call should be in cooldown
        detected2, _ = detector.process(audio_chunk)
        assert detected2 is False

    def test_reset_clears_cooldown(self):
        """Test reset clears cooldown timer."""
        with (
            patch(
                "src.pipeline.audio.openwakeword.get_pretrained_model_paths"
            ) as mock_paths,
            patch("src.pipeline.audio.openwakeword.model.Model") as mock_model,
        ):
            mock_paths.return_value = ["/models/hey_jarvis.onnx"]
            mock_model_instance = MagicMock()
            mock_model_instance.predict.return_value = {"hey_jarvis": 0.8}
            mock_model.return_value = mock_model_instance

            detector = HotwordDetector(models=["hey_jarvis"])
            audio_chunk = np.zeros(1600, dtype=np.int16)

            detector.process(audio_chunk)  # First detection
            detector.reset()  # Reset cooldown
            detected, _ = detector.process(audio_chunk)  # Should detect again
            assert detected is True


class TestAudioCapture:
    """Test AudioCapture sounddevice input stream."""

    @patch("src.pipeline.audio.sd.query_devices")
    @patch("src.pipeline.audio.sd.InputStream")
    def test_start_resolves_device_name(self, mock_stream_class, mock_query_devices):
        """Test start() resolves device name to index."""
        mock_query_devices.return_value = [
            {"name": "Built-in Microphone"},
            {"name": "Jabra SPEAK 410"},
        ]
        mock_stream = MagicMock()
        mock_stream_class.return_value = mock_stream

        capture = AudioCapture(device="Jabra", sample_rate=16000)
        result = capture.start()

        assert result.success is True
        # Should find device index 1
        mock_stream_class.assert_called_once()
        call_kwargs = mock_stream_class.call_args.kwargs
        assert call_kwargs["device"] == 1

    @patch("src.pipeline.audio.sd.query_devices")
    @patch("src.pipeline.audio.sd.InputStream")
    def test_start_fails_gracefully(self, mock_stream_class, mock_query_devices):
        """Test start() returns Result.fail on exception."""
        mock_query_devices.return_value = []
        mock_stream_class.side_effect = Exception("Device not found")

        capture = AudioCapture(device="Nonexistent")
        result = capture.start()

        assert result.success is False
        assert "Device not found" in result.error

    def test_stop_when_not_running(self):
        """Test stop() returns ok when not running."""
        capture = AudioCapture()
        result = capture.stop()
        assert result.success is True

    @patch("src.pipeline.audio.queue.Queue.get")
    def test_get_frame_timeout(self, mock_queue_get):
        """Test get_frame returns timeout error."""
        mock_queue_get.side_effect = queue.Empty

        capture = AudioCapture()
        result = capture.get_frame(timeout=0.1)

        assert result.success is False
        assert result.error == "timeout"

    @patch("src.pipeline.audio.queue.Queue.get")
    def test_get_frame_success(self, mock_queue_get):
        """Test get_frame returns audio frame on success."""
        mock_queue_get.return_value = np.zeros(1600, dtype=np.int16)

        capture = AudioCapture()
        result = capture.get_frame(timeout=1.0)

        assert result.success is True
        assert isinstance(result.data, np.ndarray)


class TestAudioPlayback:
    """Test AudioPlayback sounddevice output stream."""

    @patch("src.pipeline.audio.sd.OutputStream")
    def test_play_converts_float32_to_int16(self, mock_stream_class):
        """Test play() converts float32 [-1,1] to int16."""
        mock_stream = MagicMock()
        mock_stream_class.return_value.__enter__.return_value = mock_stream

        playback = AudioPlayback(sample_rate=22050)
        # Float32 input
        audio_data = np.ones(22050, dtype=np.float32) * 0.5
        result = playback.play(audio_data)

        assert result.success is True
        # Check that int16 data was written
        written_data = mock_stream.write.call_args[0][0]
        assert written_data.dtype == np.int16
        assert np.max(written_data) > 0

    @patch("src.pipeline.audio.sd.OutputStream")
    def test_play_resamples_when_needed(self, mock_stream_class):
        """Test play() resamples when sample_rate != 22050."""
        mock_stream = MagicMock()
        mock_stream_class.return_value.__enter__.return_value = mock_stream

        playback = AudioPlayback(sample_rate=16000)  # Different from Piper's 22050
        audio_data = np.zeros(22050, dtype=np.int16)
        result = playback.play(audio_data)

        assert result.success is True
        # Should have resampled to 16000
        written_data = mock_stream.write.call_args[0][0]
        assert len(written_data) == 16000  # Resampled length

    @patch("src.pipeline.audio.sd.OutputStream")
    def test_play_handles_exception(self, mock_stream_class):
        """Test play() returns Result.fail on exception."""
        mock_stream_class.side_effect = Exception("Audio device busy")

        playback = AudioPlayback()
        result = playback.play(np.zeros(1000, dtype=np.int16))

        assert result.success is False
        assert "Audio device busy" in result.error
