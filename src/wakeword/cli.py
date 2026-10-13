"""The enrolment wizard, from a terminal.

Built because the browser one does not work where this assistant lives.

`navigator.mediaDevices.getUserMedia` only exists in a secure context: https,
localhost, or 127.0.0.1. pHantasma is reached from a phone over the LAN, so
reached by IP it is plain http, `navigator.mediaDevices` is `undefined`, and
the Gravar button throws on a property of undefined and appears to do nothing.
The browser wizard is still there and still correct when the page is served
over https, but it cannot be the only way to enrol a voice.

This runs on the machine the microphone is already plugged into, records
through the same device the assistant listens to, and needs no browser, no
https and no second microphone. It is also faster: no base64 round trip and no
MediaRecorder codec in the way of the audio.

    python3 -m src.wakeword.cli list
    python3 -m src.wakeword.cli record "Ana"          # 30 wake word, 20 others
    python3 -m src.wakeword.cli export "Ana"          # bundle for the Colab
    python3 -m src.wakeword.cli import "Ana" ~/ola.onnx

The service holds the capture device, so `record` has to run with it stopped.
That is said out loud at the point it matters rather than discovered as an
ALSA error 15 seconds into a prompt.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from src.wakeword.dataset import Sample, build_bundle, validate_samples  # noqa: E402
from src.wakeword.importer import import_model  # noqa: E402
from src.wakeword.registry import WakeWordRegistry, new_entry, slugify  # noqa: E402

SAMPLE_RATE = 16000

DEFAULT_POSITIVES = 30
DEFAULT_NEGATIVES = 20
TARGET_POSITIVES = 100


def _paths():
    import config as config_module

    return (config_module.WAKEWORD_REGISTRY_PATH,
            config_module.WAKEWORD_MODELS_DIR,
            config_module.WAKEWORD_STAGING_DIR)


def _staging_dir(staging_root: str, slug: str) -> str:
    return os.path.join(staging_root, slug)


def _staged(staging_root: str, slug: str, kind: str) -> list[Sample]:
    import wave

    directory = os.path.join(_staging_dir(staging_root, slug), kind)
    if not os.path.isdir(directory):
        return []
    out = []
    for index, name in enumerate(sorted(n for n in os.listdir(directory)
                                       if n.endswith(".wav"))):
        with wave.open(os.path.join(directory, name)) as handle:
            frames = handle.readframes(handle.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        out.append(Sample(label=kind, audio=audio, index=index, origin=name))
    return out


def _write_wav(path: str, audio: np.ndarray) -> None:
    import wave

    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def _device_is_busy() -> bool:
    """Is the assistant likely to be holding the microphone right now?

    Not a reliable probe -- ALSA will happily open a stream and then fail on
    read. It exists to turn the common case into a clear sentence instead of an
    opaque error, not to promise an answer.
    """
    try:
        import subprocess

        out = subprocess.run(["pgrep", "-f", "assistant.py"],
                             capture_output=True, text=True, timeout=5)
        return out.returncode == 0
    except Exception:  # noqa: BLE001 - a probe must never be the failure
        return False


def _capture(seconds: float, device: Optional[str]) -> np.ndarray:
    """Record one clip from the microphone, already trimmed to 16 kHz mono."""
    import sounddevice as sd

    frames = int(seconds * SAMPLE_RATE)
    recorded = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                      dtype="float32", device=device)
    sd.wait()
    audio = np.asarray(recorded, dtype=np.float32).reshape(-1)
    if device is not None and getattr(sd.query_devices(device), "default_samplerate",
                                     SAMPLE_RATE) != SAMPLE_RATE:
        # sounddevice resamples when the device refuses the rate; if it did not,
        # the length check below catches it rather than trusting the count.
        pass
    return audio


def _prompt_clip(kind: str, index: int, total: int, seconds: float,
                 device: Optional[str]) -> Optional[np.ndarray]:
    """Prompt for one clip. Returns None to skip, or raises KeyboardInterrupt."""
    label = ("a palavra de acordar" if kind == "positive"
             else "fala normal, musica, ruido -- QUALQUER coisa que NAO seja a palavra")
    print(f"\n[{index}/{total}] {label}")
    print(f"        {seconds:.1f}s. Enter to record, 's' to skip, 'q' to stop.")
    try:
        answer = input("        > ").strip().lower()
    except EOFError:
        return None
    if answer in ("q", "quit"):
        raise KeyboardInterrupt
    if answer == "s":
        return None

    try:
        audio = _capture(seconds, device)
    except Exception as exc:  # noqa: BLE001 - the common one is ALSA busy
        print(f"        NAO CONSEGUI GRAVAR: {type(exc).__name__}: {exc}")
        if _device_is_busy():
            print("        O servico do pHantasma esta a segurar o microfone.")
            print("        Corre:  sudo -n service phantasma stop")
            print("        grava tudo, e no fim: sudo -n service phantasma start")
        return None

    sample = Sample(label=kind, audio=audio, index=index)
    check = validate_samples([sample], [])[0]
    if not check.ok:
        # Rejected, explained, not stored. The counts only ever mean "usable".
        print(f"        REJEITADO: {check.reason}")
        return None
    print(f"        ok ({check.duration_s}s, pico {check.peak})")
    return audio


def cmd_record(args) -> int:
    registry_path, _models_dir, staging_root = _paths()
    registry = WakeWordRegistry(registry_path)
    slug = slugify(args.label)
    label = registry.get(slug).label if registry.get(slug) else args.label

    if _device_is_busy() and not args.force:
        print("O servico do pHantasma parece estar a correr e segura o microfone.")
        print("Gravar ao mesmo tempo vai falhar. Se preferes:")
        print("    sudo -n service phantasma stop")
        print("Depois volta a arrancar com: sudo -n service phantasma start")
        print("Ou usa --force para tentar mesmo assim.")
        return 1

    if registry.get(slug) is None:
        registry.upsert(new_entry(label, args.phrase or ""))

    for kind, count in (("positive", args.positives),
                        ("negative", args.negatives)):
        directory = os.path.join(_staging_dir(staging_root, slug), kind)
        os.makedirs(directory, exist_ok=True)
        already = len([n for n in os.listdir(directory) if n.endswith(".wav")])
        print(f"\n=== {kind.upper()} === ja tens {already} gravado(s), "
              f"faltam {max(0, count - already)}")
        if count <= 0:
            continue
        seconds = args.seconds if kind == "positive" else args.negative_seconds
        made = 0
        attempts = 0
        while already + made < count and attempts < count * 3:
            attempts += 1
            index = already + made
            audio = _prompt_clip(kind, index + 1, count, seconds, args.device)
            if audio is None:
                continue
            _write_wav(os.path.join(directory, f"{kind}_{index:03d}.wav"), audio)
            made += 1

    pos = len(_staged(staging_root, slug, "positive"))
    neg = len(_staged(staging_root, slug, "negative"))
    print(f"\n{label}: {pos} positivas, {neg} negativos.")
    if pos < args.positives:
        print(f"Faltam positivas: o minimo e {DEFAULT_POSITIVES}. "
              f"Correste 'record' outra vez para continuar.")
    if neg < args.negatives:
        print(f"Faltam negativos: o minimo e {DEFAULT_NEGATIVES}. Sem negativos "
              f"o modelo acorda com fala normal.")
    return 0


def cmd_export(args) -> int:
    registry_path, _models_dir, staging_root = _paths()
    registry = WakeWordRegistry(registry_path)
    slug = slugify(args.label)
    entry = registry.get(slug)
    label = entry.label if entry else args.label

    result = build_bundle(label, _staged(staging_root, slug, "positive"),
                          _staged(staging_root, slug, "negative"),
                          phrase=entry.phrase if entry else (args.phrase or ""),
                          out_dir=staging_root)
    if not result["ok"]:
        print(f"NAO EXPORTEI: {result['reason']}")
        for bad in result.get("bad", [])[:10]:
            print(f"  amostra {bad['index']} ({bad['label']}): {bad['reason']}")
        return 1
    print(f"Pronto: {result['zip_path']}")
    print("Carrega no Colab do openWakeWord e segue o LEIA-ME.txt dentro.")
    print("A taxa de falsos positivos NAO e medida aqui: conta os acordares")
    print("numa sala real depois de instalar.")
    return 0


def cmd_import(args) -> int:
    registry_path, models_dir, _staging = _paths()
    registry = WakeWordRegistry(registry_path)
    slug = slugify(args.label)
    if not os.path.isfile(args.model):
        print(f"Ficheiro nao encontrado: {args.model}")
        return 1
    with open(args.model, "rb") as handle:
        data = handle.read()

    entry = registry.get(slug)
    if entry is None:
        entry = new_entry(args.label, args.phrase or "")
    result = import_model(registry, slug, data, models_dir=models_dir,
                          label=entry.label)
    if not result.ok:
        print(f"NAO INSTALEI: {result.reason}")
        return 1
    entry = registry.get(slug)
    if entry is not None:
        entry.sample_count = len(_staged(_paths()[2], slug, "positive"))
        registry.upsert(entry)
    print(f"Instalado: {result.path}")
    print(f"Carregou e respondeu: {', '.join(result.loaded)}")
    if result.replaced:
        print(f"Substituiu o anterior: {result.replaced}")
    print(f"FP medido: {entry.false_positive_rate} "
          f"(None = ainda nao mediste; conta os acordares numa sala real)")
    print("Reinicia o servico para o usar:  sudo -n service phantasma restart")
    return 0


def cmd_list(args) -> int:
    registry_path, _models_dir, staging_root = _paths()
    registry = WakeWordRegistry(registry_path)
    entries = registry.all()
    if not entries:
        print("Ninguem inscrito ainda.")
        print("    python3 -m src.wakeword.cli record \"O teu nome\"")
        return 0
    for entry in entries:
        pos = len(_staged(staging_root, entry.slug, "positive"))
        neg = len(_staged(staging_root, entry.slug, "negative"))
        installed = bool(entry.model_path
                         and os.path.isfile(entry.model_path))
        fp = ("desconhecido" if entry.false_positive_rate is None
              else str(entry.false_positive_rate))
        state = ("modelo instalado" if installed else "POR TREINAR")
        print(f"{entry.label} ({entry.slug})")
        print(f"    {state}: {pos} positivas, {neg} negativos")
        if entry.phrase:
            print(f"    palavra: {entry.phrase}")
        print(f"    FP por hora: {fp}")
        if entry.model_path:
            print(f"    {entry.model_path}")
    print("\nA carregar no arranque:")
    for path in registry.model_paths():
        print(f"    {path}")
    return 0


def cmd_fp(args) -> int:
    registry_path, _models_dir, _staging = _paths()
    registry = WakeWordRegistry(registry_path)
    if not registry.set_false_positive_rate(slugify(args.label), args.rate):
        print("Utilizador desconhecido. Vê 'list'.")
        return 1
    print(f"Registado: {args.rate} falsos positivos por hora para {args.label}.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m src.wakeword.cli",
        description="Inscrever uma palavra de acordar na tua voz.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="quem esta inscrito")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("record", help="grava amostras")
    p.add_argument("label", help="nome de quem e a voz")
    p.add_argument("--phrase", default="", help="a palavra de acordar")
    p.add_argument("--positives", type=int, default=DEFAULT_POSITIVES)
    p.add_argument("--negatives", type=int, default=DEFAULT_NEGATIVES)
    p.add_argument("--seconds", type=float, default=1.8,
                   help="duracao de cada positiva")
    p.add_argument("--negative-seconds", type=float, default=3.0)
    p.add_argument("--device", default=None,
                   help="indice do dispositivo de entrada (default: do sistema)")
    p.add_argument("--force", action="store_true",
                   help="tentar gravar mesmo com o servico a correr")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("export", help="monta o pacote para o Colab")
    p.add_argument("label")
    p.add_argument("--phrase", default="")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="instala um .onnx vindo do Colab")
    p.add_argument("label")
    p.add_argument("model", help="caminho para o .onnx")
    p.add_argument("--phrase", default="")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("fp", help="registar os falsos positivos que mediste")
    p.add_argument("label")
    p.add_argument("rate", type=float, help="acordares falsos por hora")
    p.set_defaults(func=cmd_fp)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nParado. O que ja estava gravado fica.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
