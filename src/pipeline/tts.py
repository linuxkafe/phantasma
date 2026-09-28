"""
Text-to-Speech using Piper with optional sox effects.

Provides PiperTTS class for speech synthesis and a convenience synthesize() function.
Uses subprocess to call piper binary (no stable Python API available).
Applies sox post-processing effects for robotic voice character.
"""

import os
import subprocess
import tempfile
import time
from typing import Optional

from config import config
from src.pipeline.utils import Result, logger


class PiperTTS:
    """Piper TTS with sox post-processing.

    Synthesizes speech by calling piper binary via subprocess.
    Optionally applies sox effects for voice character.
    Loads audio output via soundfile.

    Attributes:
        voice_model_path: Path to Piper .onnx voice model.
        voice_config_path: Path to Piper voice config .json.
        _piper_bin: Piper binary name/path (from config.tts.piper_bin).
        _sox_bin: Sox binary name/path or None if disabled.
    """

    def __init__(
        self,
        voice_model_path: Optional[str] = None,
        voice_config_path: Optional[str] = None,
    ):
        """Initialize Piper TTS.

        Args:
            voice_model_path: Path to .onnx voice model.
                Defaults to config.tts.voice_model_path.
            voice_config_path: Path to .json voice config.
                Defaults to config.tts.voice_config_path.
        """
        self.voice_model_path = voice_model_path or config.tts.voice_model_path
        self.voice_config_path = voice_config_path or config.tts.voice_config_path
        self._piper_bin = config.tts.piper_bin
        self._sox_bin = config.tts.sox_bin if config.tts.use_sox_effects else None

    def check_dependencies(self) -> Result:
        """Verify piper and sox are available in PATH.

        Returns:
            Result.ok(None) if both found, Result.fail(error) if missing.
        """
        try:
            subprocess.run([self._piper_bin, "--help"], capture_output=True, check=False)
        except FileNotFoundError:
            return Result.fail(f"piper not found in PATH (tried: {self._piper_bin})")

        if self._sox_bin:
            try:
                subprocess.run([self._sox_bin, "--help"], capture_output=True, check=False)
            except FileNotFoundError:
                logger.warning("sox not found, disabling effects")
                self._sox_bin = None

        if not os.path.exists(self.voice_model_path):
            return Result.fail(f"Voice model not found: {self.voice_model_path}")

        if not os.path.exists(self.voice_config_path):
            return Result.fail(f"Voice config not found: {self.voice_config_path}")

        return Result.ok(None)

    def synthesize(self, text: str) -> Result:
        """Synthesize text to audio using Piper.

        Creates temporary WAV file, runs piper, optionally applies sox effects,
        loads result via soundfile, cleans up temp files.

        Args:
            text: Text to synthesize. Non-empty string.

        Returns:
            Result.ok((audio_data, sample_rate)) on success,
            Result.fail(error) on failure.
            audio_data: numpy array int16, shape (samples,)
            sample_rate: int (typically 22050 for Piper)
            Result.duration_ms contains total synthesis time.
        """
        start = time.perf_counter()

        check = self.check_dependencies()
        if not check.success:
            return check

        try:
            # Create temp file for piper output
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_out:
                output_path = tmp_out.name

            try:
                # Run Piper
                piper_cmd = [
                    self._piper_bin,
                    "--model",
                    self.voice_model_path,
                    "--config",
                    self.voice_config_path,
                    "--output_file",
                    output_path,
                ]

                piper_proc = subprocess.run(
                    piper_cmd,
                    input=text.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                )

                if piper_proc.returncode != 0:
                    return Result.fail(f"Piper failed: {piper_proc.stderr.decode()}")

                # Apply sox effects if enabled
                if self._sox_bin and config.tts.sox_effects:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_effected:
                        effected_path = tmp_effected.name

                    try:
                        sox_cmd = [self._sox_bin, output_path, effected_path]
                        for effect in config.tts.sox_effects:
                            sox_cmd.extend(effect)

                        sox_proc = subprocess.run(sox_cmd, capture_output=True, timeout=10)

                        if sox_proc.returncode == 0:
                            output_path = effected_path
                        else:
                            err = sox_proc.stderr.decode()
                            logger.warning(f"sox failed, using raw output: {err}")

                    finally:
                        if effected_path != output_path and os.path.exists(effected_path):
                            os.unlink(effected_path)

                # Load audio
                import soundfile as sf

                audio_data, sample_rate = sf.read(output_path, dtype="int16")

                duration_ms = (time.perf_counter() - start) * 1000
                logger.info(
                    f"TTS synthesized: {len(audio_data)} samples "
                    f"@ {sample_rate}Hz (duration_ms={duration_ms:.1f})"
                )

                return Result.ok((audio_data, sample_rate), duration_ms=duration_ms)

            finally:
                if os.path.exists(output_path):
                    os.unlink(output_path)

        except subprocess.TimeoutExpired:
            return Result.fail("TTS synthesis timeout")
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(f"TTS synthesis error: {e}")
            return Result.fail(str(e), duration_ms=duration_ms)


def synthesize(text: str) -> Result:
    """Convenience function for direct synthesis.

    Creates PiperTTS instance and calls synthesize(). Use for one-off synthesis.
    For repeated use, instantiate PiperTTS once and reuse.

    Args:
        text: Text to synthesize.

    Returns:
        Result with (audio_data, sample_rate) in data field.
    """
    tts = PiperTTS()
    return tts.synthesize(text)
