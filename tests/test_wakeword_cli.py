"""The terminal wizard, exercised without a microphone.

The capture itself is stubbed. What is under test is everything around it: the
counting, the refusals, and the two things that are easy to get wrong and
invisible when wrong -- that a rejected clip is NOT counted as recorded, and
that a bad model is refused with the previous one left alone.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.wakeword import cli  # noqa: E402
from src.wakeword.dataset import MIN_NEGATIVES, MIN_POSITIVES  # noqa: E402
from tests.test_wakeword import negatives, positives, speechlike  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Captured at import, before the fixture below stubs it. A test that wants the
# real prompt -- and therefore the real verdict and its explanation -- puts it
# back with this.
REAL_PROMPT = cli._prompt_clip
REAL_MODEL = os.path.join(REPO_ROOT, "models", "ola_fantasma.onnx")


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Point the CLI at temporary paths and pretend nothing is recording."""
    monkeypatch.setattr(cli, "_paths", lambda: (
        str(tmp_path / "wakewords.json"),
        str(tmp_path / "models"),
        str(tmp_path / "staging"),
    ))
    monkeypatch.setattr(cli, "_device_is_busy", lambda: False)
    monkeypatch.setattr(cli, "_prompt_clip", lambda *a, **k: None)
    return tmp_path


def _stage(root, slug, kind, samples):
    # Under `staging`, which is what _paths() handed the CLI.
    directory = os.path.join(str(root), "staging", slug, kind)
    os.makedirs(directory, exist_ok=True)
    for index, sample in enumerate(samples):
        cli._write_wav(os.path.join(directory, f"{kind}_{index:03d}.wav"),
                       sample.audio)


class TestList:
    def test_nobody_enrolled_says_so_and_says_how(self, wired, capsys):
        assert cli.main(["list"]) == 0
        out = capsys.readouterr().out
        assert "Ninguem inscrito" in out
        assert "src.wakeword.cli record" in out


class TestRecord:
    def test_it_refuses_while_the_service_holds_the_microphone(self, monkeypatch,
                                                               wired, capsys):
        monkeypatch.setattr(cli, "_device_is_busy", lambda: True)
        assert cli.main(["record", "Ana"]) == 1
        out = capsys.readouterr().out
        assert "sudo -n service phantasma stop" in out

    def test_a_rejected_clip_is_not_counted(self, monkeypatch, wired, capsys):
        """The count must mean 'usable clips'.

        A rejected clip that still counted would send the owner to the Colab
        with a dataset the wizard had already refused, and the model would
        inherit the defect this whole module exists to prevent.

        The capture is stubbed, not the prompt: the verdict and its explanation
        are printed from inside `_prompt_clip`, so stubbing that would stub out
        the thing under test.
        """
        clips = iter([speechlike(1.4),
                      np.zeros(16000 * 2, dtype=np.float32),   # silence
                      speechlike(1.4, seed=2)])
        monkeypatch.setattr(cli, "_prompt_clip", REAL_PROMPT)
        monkeypatch.setattr(cli, "_capture", lambda *a, **k: next(clips))
        monkeypatch.setattr("builtins.input", lambda *a: "")
        assert cli.main(["record", "Ana", "--positives", "3",
                         "--negatives", "0"]) == 0
        out = capsys.readouterr().out
        assert "REJEITADO" in out
        assert "2 positivas" in out

    def test_force_records_anyway(self, monkeypatch, wired, capsys):
        monkeypatch.setattr(cli, "_device_is_busy", lambda: True)
        monkeypatch.setattr(cli, "_prompt_clip", REAL_PROMPT)
        monkeypatch.setattr(cli, "_capture", lambda *a, **k: speechlike(1.4))
        monkeypatch.setattr("builtins.input", lambda *a: "")
        assert cli.main(["record", "Ana", "--positives", "1", "--negatives",
                         "0", "--force"]) == 0


class TestExport:
    def test_refused_until_there_is_enough(self, wired, capsys):
        _stage(wired, "ana", "positive", positives(5))
        _stage(wired, "ana", "negative", negatives(30))
        assert cli.main(["export", "Ana"]) == 1
        assert str(MIN_POSITIVES) in capsys.readouterr().out

    def test_refused_without_negatives(self, wired, capsys):
        _stage(wired, "ana", "positive", positives(MIN_POSITIVES))
        assert cli.main(["export", "Ana"]) == 1
        out = capsys.readouterr().out
        assert "negativo" in out.lower()

    def test_builds_and_says_the_fp_rate_is_unmeasured(self, wired, capsys):
        _stage(wired, "ana", "positive", positives(MIN_POSITIVES))
        _stage(wired, "ana", "negative", negatives(MIN_NEGATIVES))
        assert cli.main(["export", "Ana"]) == 0
        out = capsys.readouterr().out
        assert "Pronto:" in out
        assert "NAO e medida aqui" in out


class TestImport:
    def test_a_valid_model_is_installed(self, wired, capsys):
        assert cli.main(["import", "Ana", REAL_MODEL]) == 0
        out = capsys.readouterr().out
        assert "Instalado:" in out
        assert "service phantasma restart" in out

    def test_a_bad_model_is_refused(self, wired, capsys, tmp_path):
        junk = tmp_path / "junk.onnx"
        junk.write_bytes(b"x" * 5000)
        assert cli.main(["import", "Ana", str(junk)]) == 1
        assert "NAO INSTALEI" in capsys.readouterr().out

    def test_a_missing_file_is_refused(self, wired, capsys):
        assert cli.main(["import", "Ana", "/tmp/definitely-not-here.onnx"]) == 1

    def test_fp_stays_unknown_until_measured(self, wired, capsys):
        cli.main(["import", "Ana", REAL_MODEL])
        assert "None" in capsys.readouterr().out
        assert cli.main(["fp", "Ana", "0.5"]) == 0
        cli.main(["list"])
        assert "0.5" in capsys.readouterr().out

    def test_fp_for_an_unknown_person_is_refused(self, wired, capsys):
        assert cli.main(["fp", "Ninguem", "1.0"]) == 1
