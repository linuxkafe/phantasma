"""The noise calibration must be reachable from .env.

On 2026-10-04 the house stopped answering its own name. The cause was not the
wake model: a genuine "ola fantasma" scores 0.7677, and the bar in force was
0.856, because the noise floor of this room settles at -40.5 dBFS and the
calibration charged +0.156 for it.

The calibration lived in a dataclass default with no environment override, so
the room's floor could not be expressed anywhere except code -- and nobody had
ever measured it there. These tests pin the fix from the operator's side: set
the value in .env, and the bar actually moves.
"""

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

PROBE = """
import sys
sys.path.insert(0, {root!r})
import warnings
warnings.simplefilter("ignore")
import config
h = config.config.hotword
print(h.noise_quiet_db, h.noise_loud_db, h.noise_max_bump, h.noise_adaptive)
"""


# Same probe WITHOUT the warning filter. The inverted-calibration case is
# precisely about a warning, and `simplefilter("ignore")` -- added to keep the
# other probes quiet -- swallowed the very thing being asserted.
PROBE_LOUD = """
import sys
sys.path.insert(0, {root!r})
import config
h = config.config.hotword
print(h.noise_quiet_db, h.noise_loud_db)
"""


def _probe(env_extra):
    env = dict(os.environ)
    env.update({k: str(v) for k, v in env_extra.items()})
    out = subprocess.run(
        [sys.executable, "-c", PROBE.format(root=str(ROOT))],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120,
    )
    assert out.returncode == 0, out.stderr[-800:]
    return out.stdout.strip().splitlines()[-1]


def test_defaults_carry_the_measured_room():
    quiet, loud, bump, adaptive = _probe({}).split()
    assert (float(quiet), float(loud)) == (-38.0, -20.0)
    assert float(bump) == 0.20
    assert adaptive == "True"


def test_env_overrides_the_calibration():
    """The operator's only lever, and the one that was missing."""
    quiet, loud, bump, adaptive = _probe({
        "NOISE_QUIET_DB": -50, "NOISE_LOUD_DB": -30,
        "NOISE_MAX_BUMP": 0.35, "NOISE_ADAPTIVE": "false",
    }).split()
    assert (float(quiet), float(loud), float(bump)) == (-50.0, -30.0, 0.35)
    assert adaptive == "False", "NOISE_ADAPTIVE=false did not disable adaptation"


def test_inverted_calibration_is_reported_not_silently_accepted():
    """quiet_db at or above loud_db makes the span zero or negative.

    The penalty then stops meaning anything, and the failure mode is silence:
    the wake word simply never fires, with nothing in the log to explain it.
    That is the AUDIO_AUTO_DETECT class of bug -- a mistyped variable name that
    fails open and looks like the feature not working -- so it is reported.
    """
    env = dict(os.environ)
    env.update({"NOISE_QUIET_DB": "-20", "NOISE_LOUD_DB": "-40"})
    out = subprocess.run(
        [sys.executable, "-c", PROBE_LOUD.format(root=str(ROOT))],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120,
    )
    combined = out.stdout + out.stderr
    assert "NOISE_QUIET_DB" in combined and "not below" in combined, (
        "a calibracao invertida nao foi reportada. Notar que esta asercao "
        "exige a MENSAGEM: uma versao anterior aceitava qualquer erro, e por isso "
        "passava mesmo quando o aviso estourava um NameError por falta do "
        "import -- o teste verde a mentir."
    )
    assert "NameError" not in combined, (
        "o caminho do aviso esta a rebentar em vez de avisar"
    )
