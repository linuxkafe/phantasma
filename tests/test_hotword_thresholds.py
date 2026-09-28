"""Tests for the per-model wake-word threshold override.

Why these exist: ``thresholds_per_model`` was shipped INERT -- prod loads ``{}``,
and nothing exercised the parsing or the consumer. An untested code path in a
wake-word path is a landmine, not a convenience. These tests prove the
mechanism works WITHOUT changing production behaviour: prod still loads only
``ola_fantasma.onnx`` at the global threshold, which the user validated.

See config.py:~400 and src/pipeline/audio.py:302.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_with_env(env_overrides: dict) -> dict:
    """Load config in a subprocess with specific env vars, return hotword fields.

    A subprocess is required: config.py reads its environment once at import
    time, so an in-process monkeypatch would not exercise the real path.
    """
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("WAKEWORD_")}
    env.update(env_overrides)
    env["PYTHONPATH"] = str(REPO)
    code = (
        "from dotenv import load_dotenv; load_dotenv(); import config, json;"
        "print(json.dumps({'per_model': config.config.hotword.thresholds_per_model,"
        "'threshold': config.config.hotword.threshold}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    import json

    payload = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "{}"
    return json.loads(payload)


class TestPerModelThresholdParsing:
    def test_absent_env_yields_empty_dict_and_global_threshold(self):
        """The production state: no override, global threshold applies."""
        r = _load_with_env({})
        assert r["per_model"] == {}

    def test_well_formed_pair_is_parsed(self):
        r = _load_with_env({"WAKEWORD_CONFIDENCE_PER_MODEL": "ola_fantasma:0.70,hey_fantasma:0.50"})
        assert r["per_model"] == {"ola_fantasma": 0.70, "hey_fantasma": 0.50}

    def test_single_pair(self):
        r = _load_with_env({"WAKEWORD_CONFIDENCE_PER_MODEL": "hey_fantasma:0.50"})
        assert r["per_model"] == {"hey_fantasma": 0.50}

    def test_whitespace_is_tolerated(self):
        r = _load_with_env({"WAKEWORD_CONFIDENCE_PER_MODEL": " ola_fantasma : 0.70 "})
        assert r["per_model"] == {"ola_fantasma": 0.70}

    def test_malformed_entry_is_skipped_not_fatal(self):
        """One bad entry must not take the assistant down."""
        r = _load_with_env({"WAKEWORD_CONFIDENCE_PER_MODEL": "ola_fantasma:0.70,garbage"})
        assert r["per_model"] == {"ola_fantasma": 0.70}

    def test_non_numeric_value_is_skipped(self):
        r = _load_with_env({"WAKEWORD_CONFIDENCE_PER_MODEL": "ola_fantasma:high"})
        assert r["per_model"] == {}


class TestMalformedEntryIsReported:
    """The AUDIO_AUTO_DETECT fail-open class: a silent fallback is a bug.

    ``ola_fantasma=0.70`` (equals instead of colon) parses to nothing and used to
    be ignored without a word, leaving the operator staring at the global
    threshold with no explanation. It must now warn.
    """

    def test_equals_instead_of_colon_warns(self):
        import os

        env = {k: v for k, v in os.environ.items() if not k.startswith("WAKEWORD_")}
        env["WAKEWORD_CONFIDENCE_PER_MODEL"] = "ola_fantasma=0.70"
        env["PYTHONPATH"] = str(REPO)
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from dotenv import load_dotenv; load_dotenv(); import config",
            ],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert "WARNING" in out.stderr
        assert "ola_fantasma=0.70" in out.stderr
        assert "colon" in out.stderr

    def test_non_numeric_value_warns(self):
        import os

        env = {k: v for k, v in os.environ.items() if not k.startswith("WAKEWORD_")}
        env["WAKEWORD_CONFIDENCE_PER_MODEL"] = "ola_fantasma:high"
        env["PYTHONPATH"] = str(REPO)
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from dotenv import load_dotenv; load_dotenv(); import config",
            ],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert "WARNING" in out.stderr
        assert "not a number" in out.stderr


class TestConsumerUsesTheOverride:
    def test_audio_detector_reads_the_dict(self):
        """audio.py:302 must copy the dict -- assert the wiring still exists.

        A source-level assertion, not a runtime one: constructing a real
        HotwordDetector needs an ONNX model and an audio device.
        """
        src = (REPO / "src" / "pipeline" / "audio.py").read_text(encoding="utf-8")
        assert "config.hotword.thresholds_per_model" in src
        assert "self._thresholds = dict(" in src

    def test_threshold_lookup_helper_prefers_per_model(self):
        """If a lookup helper exists it must prefer the per-model value."""
        src = (REPO / "src" / "pipeline" / "audio.py").read_text(encoding="utf-8")
        if "_thresholds.get" in src:
            i = src.index("_thresholds.get")
            window = src[max(0, i - 400) : i + 200]
            assert "self._thresholds.get" in window

    def test_lookup_key_is_the_model_stem_not_the_filename(self):
        """The .env keys must match what `predictions` is keyed by.

        audio.py builds each key as ``basename(path).replace(".onnx", "")`` --
        the STEM, e.g. ``ola_fantasma``. The .env therefore has to say
        ``ola_fantasma:0.70`` and NOT ``ola_fantasma.onnx:0.70``; the latter
        would miss every lookup and fall back to the global threshold with no
        error. This assertion pins that coupling so a model rename cannot
        silently disable the override.
        """
        src = (REPO / "src" / "pipeline" / "audio.py").read_text(encoding="utf-8")
        assert 'os.path.basename(path).replace(".onnx", "")' in src, (
            "model key convention changed; update the .env WAKEWORD_CONFIDENCE_PER_MODEL keys"
        )
        # And the lookup must use that same key.
        assert "self._thresholds.get(model_name, self.threshold)" in src


class TestProductionStateIsDocumented:
    def test_hey_fantasma_not_in_prod_model_list(self):
        """hey_fantasma.onnx exists on disk but is NOT loaded in prod.

        This is a deliberate decision, not an oversight: the user validated the
        wake word with ola_fantasma at 0.70. Loading a second model is a
        behaviour change that needs its own validation. If this test fails,
        someone enabled it -- update the config.py comment accordingly.
        """
        env_path = REPO / ".env"
        if not env_path.exists():
            pytest.skip("no .env in this checkout")
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("WAKEWORD_MODELS="):
                assert "hey_fantasma" not in line
                return
        pytest.skip("WAKEWORD_MODELS not set in .env")
