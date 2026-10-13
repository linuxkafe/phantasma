"""Turning recorded samples into a dataset somebody else can train from.

This module prepares and validates; it never trains. The owner's decision on
2026-10-13 was that training happens in the openWakeWord Google Colab
notebook, and `CLAUDE.md` records that as a deliberate exception to the
project's offline-first non-goal. So the job here is narrower and has one
honest shape: produce a bundle that is either good enough to train from or
refused.

The refusal is the interesting half. A wake-word model trained on thirty
clipped recordings of a laptop fan teaches the assistant to wake on a laptop
fan, and the failure arrives in the house weeks later as "it keeps waking up
on its own". Every check below exists to make the wizard say that now, while
the owner is still holding the microphone, rather than then.

What is NOT checked here: the false-positive rate. That is measured by running
the trained model against real room audio, which needs a trained model, and it
is left to the owner to run and read -- see `README.md` in the bundle. Promising
a number this module cannot produce would be the dishonest kind of helpful.
"""

import json
import os
import tempfile
import wave
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterable, Optional

import numpy as np

from src.wakeword.registry import slugify

__all__ = ["Sample", "SampleCheck", "validate_samples", "build_bundle",
           "MIN_POSITIVES", "TARGET_POSITIVES", "MIN_NEGATIVES"]

# openWakeWord clips around 2s. Below ~0.5s the wake word is cut off; above
# ~5s the notebook's mixing window has more padding than word.
MIN_DURATION_S = 0.5
MAX_DURATION_S = 5.0

# Fewer than this and the model will overfit to one delivery and stop
# recognising the owner on a normal day.
MIN_POSITIVES = 30
TARGET_POSITIVES = 100
MIN_NEGATIVES = 20

# RMS floor, full scale. Below this the recording is a click or a breath and
# carries no voice to learn from.
MIN_RMS = 0.01

# Fraction of samples at or beyond this level that counts as clipping. A
# clipped recording has its consonants flattened, and the flattened consonants
# are most of what distinguishes the phrase from ordinary speech.
CLIP_LEVEL = 0.98
MAX_CLIPPED_FRACTION = 0.02


@dataclass
class Sample:
    """One recorded clip, already at 16 kHz mono float32."""

    label: str          # "positive" (the wake word) or "negative" (anything else)
    audio: np.ndarray
    index: int = 0
    origin: str = ""    # the filename or client hint it arrived with

    @property
    def duration_s(self) -> float:
        return len(self.audio) / 16000.0

    @property
    def peak(self) -> float:
        return float(np.max(np.abs(self.audio))) if len(self.audio) else 0.0

    @property
    def rms(self) -> float:
        return float(np.sqrt(np.mean(np.square(self.audio)))) if len(self.audio) else 0.0

    @property
    def clipped_fraction(self) -> float:
        if not len(self.audio):
            return 0.0
        return float(np.mean(np.abs(self.audio) >= CLIP_LEVEL))


@dataclass
class SampleCheck:
    """The verdict on one clip, and the sentence to show the owner about it."""

    index: int
    label: str
    ok: bool
    reason: str = ""
    duration_s: float = 0.0
    peak: float = 0.0
    rms: float = 0.0

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


def _check_one(sample: Sample) -> SampleCheck:
    """Decide whether one clip can be trained on, and say why not."""
    def verdict(ok: bool, reason: str = "") -> SampleCheck:
        return SampleCheck(index=sample.index, label=sample.label, ok=ok,
                           reason=reason, duration_s=round(sample.duration_s, 3),
                           peak=round(sample.peak, 4),
                           rms=round(sample.rms, 5))

    if sample.audio is None or len(sample.audio) == 0:
        return verdict(False, "Clipe vazio.")

    if sample.duration_s < MIN_DURATION_S:
        return verdict(False,
                       f"Curto demais ({sample.duration_s:.1f}s). Fala a "
                       f"palavra inteira; o minimo e {MIN_DURATION_S}s.")

    if sample.duration_s > MAX_DURATION_S:
        return verdict(False,
                       f"Longo demais ({sample.duration_s:.1f}s). Corta em "
                       f"{MAX_DURATION_S}s para ficar uma palavra e silencio.")

    if sample.peak >= 1.0:
        return verdict(False,
                       "Saturado. O microfone esta too perto ou o ganho too "
                       "alto; afasta-te uns 30 cm.")

    if sample.clipped_fraction > MAX_CLIPPED_FRACTION:
        return verdict(False,
                       f"{sample.clipped_fraction:.1%} do clipe esta saturado. "
                       "Baixa o ganho ou afasta-te do microfone.")

    if sample.rms < MIN_RMS:
        return verdict(False,
                       f"Quase silencio (RMS {sample.rms:.4f}). Fala mais perto "
                       "do microfone.")

    return verdict(True)


def validate_samples(positives: Iterable[Sample],
                     negatives: Iterable[Sample]) -> list[SampleCheck]:
    """Check every clip and report on each one individually.

    Not a single pass/fail: the owner records thirty clips, one of them is bad,
    and the useful answer is which one and why. A verdict that only says
    "insufficient samples" leaves them re-recording everything.
    """
    return [_check_one(s) for s in list(positives) + list(negatives)]


def _write_wav(path: str, audio: np.ndarray) -> None:
    """Write one 16-bit mono 16 kHz WAV.

    16-bit PCM is what the openWakeWord loader and the notebook both expect.
    Writing float WAV here would produce a bundle that looks fine and needs
    conversion on the other side.
    """
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm.tobytes())


def _colab_instructions(user_label: str, positives: int, negatives: int) -> str:
    return f"""# Treinar a palavra de acordar de {user_label}

Isto e o pacote de treino. O treino corre FORA desta maquina, no Google Colab
do openWakeWord. O que sai daqui sao {positives} gravacoes da tua voz e
{negatives} negativos -- ver a excepcao registada no CLAUDE.md.

## O que ha aqui

    positives/   {positives} clips de 16 kHz mono, a palavra de acordar
    negative/    {negatives} clips de 16 kHz mono, tudo o que NAO e a palavra
    metadata.json

## Passos

1. Abre o notebook "Automatic Model Customization and Training" do
   openWakeWord no Google Colab.
2. Corre as celulas de instalacao. O notebook descarrega os modelos base e o
   conjunto de negativos -- isso e hundreds of MB, e precisa de espaco em
   disco. Se a celula falhar por espaco, e isso.
3. Carrega ESTA pasta como dataset. O notebook espera:
      - uma pasta com os clips POSITIVOS, e
      - o conjunto de negativos que ele proprio descarrega.
   Os ficheiros ja estao em 16 kHz mono, que e o que ele quer.
4. Ajusta os steps. Com {positives} positivos, 2000-4000 chega. Mais do que
   isso com poucos clipes leva a overfitting: o modelo passa a reconhecer
   a gravacao e nao a tua voz.
5. Corre o treino e descarrega o `.onnx` gerado.

## Depois

Volta ao pHantasma, abre o wizard de palavra de acordar e importa o `.onnx`.
O wizard valida-o ANTES de o instalar: carrega-o com o openwakeword e corre uma
inferencia. Se nao carregar, o modelo anterior fica no sitio.

## O que este pacote NAO pode garantir

**A taxa de falsos positivos.** O notebook trata dos negativos com o corpus que
descarrega, e isso ajuda. Mas o unico teste que vale e o teu: depois de
instalado, deixa o assistente a ouvir numa sala real durante um dia e conta
quantas vezes acordou sem ninguem dizer a palavra. Se acordar sozinho, o
problema e esse numero, e a solucao sao mais negativos -- grava mais fala
normal, musica, o som do Exterior -- e treina de novo.
"""


def build_bundle(user_label: str, positives: Iterable[Sample],
                 negatives: Iterable[Sample], phrase: str = "",
                 out_dir: Optional[str] = None) -> dict:
    """Validate, then write a Colab-ready bundle. Refuses on weak evidence.

    This refuses to produce a bundle when the dataset cannot support a usable
    model, and says which of the two reasons applies. Two refusals, because
    they are different problems with different fixes:

      * too few positives -- the model will not generalise from five clips.
      * no negatives at all -- this is the one that bites. Trained without
        negatives, a wake-word model fires on ordinary speech, and the symptom
        ("acorda sozinho") is indistinguishable from a broken microphone.

    Args:
        user_label: Who this is for. Recorded in metadata, not trusted.
        positives: Clips of the wake word.
        negatives: Clips of everything else.
        phrase: The wake word, if the owner is happy to write it down. It goes
            in metadata only; the notebook does not need it.
        out_dir: Where to write. Defaults to a temporary directory.

    Returns:
        A dict with `ok`, `reason`, `zip_path`, `dir_path` and the checks.

    Raises:
        Nothing. A refusal is a return value, because "you did not record
        enough" is an ordinary outcome of this workflow, not a crash.
    """
    positives = list(positives)
    negatives = list(negatives)
    checks = validate_samples(positives, negatives)

    bad = [c for c in checks if not c.ok]
    good_pos = [s for s, c in zip(positives, checks[:len(positives)]) if c.ok]

    if bad:
        return {"ok": False, "reason": "amostras rejeitadas",
                "checks": [c.to_dict() for c in checks],
                "bad": [c.to_dict() for c in bad]}

    if len(positives) < MIN_POSITIVES:
        return {"ok": False,
                "reason": (f"Sao {len(positives)} amostras da palavra e o minimo "
                           f"e {MIN_POSITIVES}. Com poucas, o modelo aprende a "
                           f"gravacao e nao a tua voz."),
                "checks": [c.to_dict() for c in checks]}

    if len(negatives) < MIN_NEGATIVES:
        return {"ok": False,
                "reason": (f"Sao {len(negatives)} negativos e o minimo e "
                           f"{MIN_NEGATIVES}. Sem negativos o modelo dispara "
                           f"com fala normal -- o sintoma e acordar sozinho."),
                "checks": [c.to_dict() for c in checks]}

    target = out_dir or tempfile.mkdtemp(prefix="pHantasma-treino-")
    # Filesystem handles come from `slugify`, never from the display label. The
    # label is "Ana Silva" and the slug is "ana-silva", so naming the zip after
    # the label meant the download route looked for "ana-silva-wakeword.zip"
    # and only ever found a 404.
    name = slugify(user_label)
    user_dir = os.path.join(target, name)
    pos_dir = os.path.join(user_dir, "positive")
    neg_dir = os.path.join(user_dir, "negative")
    os.makedirs(pos_dir, exist_ok=True)
    os.makedirs(neg_dir, exist_ok=True)

    for sample in positives:
        _write_wav(os.path.join(pos_dir, f"pos_{sample.index:03d}.wav"),
                   sample.audio)
    for sample in negatives:
        _write_wav(os.path.join(neg_dir, f"neg_{sample.index:03d}.wav"),
                   sample.audio)

    metadata = {
        "user": user_label,
        "slug": name,
        "phrase": phrase,
        "sample_rate": 16000,
        "channels": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "positives": len(positives),
        "negatives": len(negatives),
        "checks": [c.to_dict() for c in checks],
        "false_positive_rate": None,
        "false_positive_note": (
            "Not measured. Train, install, then count the wake-ups in a real "
            "room over a day. This wizard will not guess it."),
    }
    with open(os.path.join(user_dir, "metadata.json"), "w",
              encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)

    with open(os.path.join(user_dir, "LEIA-ME.txt"), "w",
              encoding="utf-8") as handle:
        handle.write(_colab_instructions(user_label, len(positives),
                                        len(negatives)))

    zip_path = os.path.join(target, f"{name}-wakeword.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _dirs, files in os.walk(user_dir):
            for name in files:
                full = os.path.join(root, name)
                archive.write(full, os.path.relpath(full, target))

    return {"ok": True, "dir_path": user_dir, "zip_path": zip_path,
            "metadata": metadata,
            "checks": [c.to_dict() for c in checks],
            "positives_used": len(good_pos)}


def decode_clip(data: bytes) -> np.ndarray:
    """Decode whatever the browser recorded into 16 kHz mono float32.

    Routed through the same `faster_whisper.audio.decode_audio` the voice route
    uses, because that is the one already proven against live MediaRecorder
    output: webm/opus, ogg, mp4/aac, wav, resampled and downmixed. A second
    decoder here would be a second thing to keep in step, and soundfile --
    which is what `routes._decode_base64_audio` tries first -- cannot read
    webm at all, so the fallback would produce noise at the right sample rate.
    """
    from src.pipeline.stt import decode_bytes
    return decode_bytes(data)
