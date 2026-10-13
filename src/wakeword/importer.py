"""Accepting a trained model back from the Colab, without bricking the house.

The `.onnx` arrives as a binary from a notebook run somewhere else. It has no
business being trusted because it exists, so it is loaded and executed before
it is allowed anywhere near `WAKEWORD_MODELS`:

    load with openwakeword  -> run one inference over a second of silence
    -> install, recording the previous file

and if any of that fails, the previous model is still installed and still in
the registry. A wake word that does not load takes the whole assistant down at
startup -- `HotwordDetector.__init__` raises `RuntimeError` on a bad model, and
that is the difference between "the new voice is not recognised yet" and "the
house does not answer at all".

Replacing rather than adding is deliberate too. `HotwordDetector` scores every
configured model and fires on any of them, so leaving the previous per-user
model installed alongside the new one would leave the old voice able to wake the
assistant as well, and the owner would conclude the retrain did nothing.
"""

import logging
import os
from datetime import datetime
from typing import Optional

import numpy as np

from src.wakeword.registry import WakeWordRegistry, new_entry

logger = logging.getLogger(__name__)

__all__ = ["ImportResult", "validate_onnx", "import_model"]

# openWakeWord's own tuned models are a little over 200 kB. A trained custom one
# lands in the same order. A file three orders of magnitude smaller is not a
# wake-word model, it is a truncated download, and refusing it early gives a
# better message than an onnxruntime shape error.
MIN_MODEL_BYTES = 10_000
MAX_MODEL_BYTES = 50_000_000


class ImportResult:
    """What happened, in enough detail to be shown to the owner verbatim.

    Not an exception, because "the notebook gave me a file this cannot use" is
    an ordinary outcome that the wizard renders as a page, not a 500.
    """

    def __init__(self, ok: bool, reason: str = "", path: Optional[str] = None,
                 loaded: Optional[list] = None,
                 replaced: Optional[str] = None):
        self.ok = ok
        self.reason = reason
        self.path = path
        self.loaded = loaded or []
        # The file this one took the place of, so the wizard can say what was
        # replaced instead of leaving the owner wondering.
        self.replaced = replaced

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "path": self.path,
                "loaded": self.loaded, "replaced": self.replaced}


def validate_onnx(path: str) -> ImportResult:
    """Does this file actually load and run? No filesystem side effects.

    Runs a second of digital silence through it. Silence is deliberate: it
    gives a real score to look at, and a model that raises on silence is a
    model that will raise on a quiet room at 3am.
    """
    if not os.path.isfile(path):
        return ImportResult(False, f"Ficheiro nao encontrado: {path}")

    size = os.path.getsize(path)
    if size < MIN_MODEL_BYTES:
        return ImportResult(
            False,
            f"O ficheiro tem {size} bytes e um modelo treinado costuma ter "
            f"mais de {MIN_MODEL_BYTES}. Costuma ser um download interrompido "
            f"-- volta a exportar do Colab.")
    if size > MAX_MODEL_BYTES:
        return ImportResult(
            False,
            f"O ficheiro tem {size / 1e6:.1f} MB, o que nao parece um modelo "
            f"de palavra de acordar.")

    try:
        from openwakeword.model import Model
    except ImportError as exc:  # pragma: no cover - openwakeword is required
        return ImportResult(False, f"openwakeword indisponivel: {exc}")

    try:
        # `wakeword_models`, not `wakeword_model_paths`: the latter is
        # deprecated and warns on every call. Same shape `src/pipeline/audio.py`
        # uses when it loads the project's own `.onnx` files.
        model = Model(wakeword_models=[path], inference_framework="onnx")
        silence = np.zeros(16000, dtype="float32")
        scores = model.predict(silence)
    except Exception as exc:  # noqa: BLE001 - any failure means "do not install"
        return ImportResult(
            False,
            f"O modelo nao carregou ({type(exc).__name__}: {exc}). O anterior "
            f"continua instalado.")

    loaded = sorted(scores)
    if not loaded:
        return ImportResult(
            False,
            "O modelo carregou mas nao produz nenhuma saida. O Colab pode ter "
            "exportado um ficheiro vazio.")

    logger.info("Modelo de palavra de acordar validado: %s -> %s", path, loaded)
    return ImportResult(True, path=path, loaded=loaded)


def import_model(registry: WakeWordRegistry, slug: str, data: bytes,
                 models_dir: Optional[str] = None,
                 label: Optional[str] = None) -> ImportResult:
    """Validate, install and record a model for one enrolled person.

    The install is a rename, not a copy into place: the new file is written
    beside the target under a temporary name, and only after it validates is
    it moved over the old one. So there is no window in which the path in the
    registry points at a half-written or invalid file.

    Args:
        registry: Where the enrolment lives.
        slug: Whose model this is.
        data: The `.onnx` bytes, exactly as returned by the notebook.
        models_dir: Where to install. Defaults to the registry's directory.
        label: Display name, only used when creating a missing entry.

    Returns:
        An ImportResult. On failure nothing is changed.
    """
    if not data:
        return ImportResult(False, "O ficheiro veio vazio.")

    target_dir = models_dir or os.path.join(os.path.dirname(registry.path),
                                            "models")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, f"{slug}.onnx")
    staged = os.path.join(target_dir, f".{slug}.onnx.incoming")

    try:
        with open(staged, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        return ImportResult(False, f"Nao consegui escrever o modelo: {exc}")

    try:
        result = validate_onnx(staged)
        if not result.ok:
            return result

        entry = registry.get(slug)
        replaced = entry.model_path if entry and entry.model_path else None

        # Only now does the validated file become the real one.
        os.replace(staged, target)

        if entry is None:
            entry = new_entry(label or slug)
        entry.model_path = target
        entry.trained_at = datetime.now().isoformat(timespec="seconds")
        registry.upsert(entry)

        logger.info("Modelo de %s instalado em %s", slug, target)
        return ImportResult(True, path=target, loaded=result.loaded,
                            replaced=replaced)
    finally:
        # Whatever happened, the staged copy does not stay behind. On success
        # it was already renamed away; on failure this is what removes it.
        try:
            if os.path.exists(staged):
                os.unlink(staged)
        except OSError:  # pragma: no cover - best effort
            logger.warning("Ficou um ficheiro temporario por limpar: %s", staged)


def result_reason_now() -> str:
    """Timestamp for `trained_at`. Named so it cannot be confused with a verdict."""
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def remove_model(registry: WakeWordRegistry, slug: str,
                 delete_file: bool = False) -> ImportResult:
    """Forget a person, and optionally their `.onnx`.

    `delete_file` is False by default and the flag is not exposed to the web
    route: removing somebody's trained voice is irreversible, and a button on a
    page is not consent to that.
    """
    entry = registry.get(slug)
    if entry is None:
        return ImportResult(False, "Esse utilizador nao esta inscrito.")

    path = entry.model_path
    registry.remove(slug)
    if delete_file and path and os.path.isfile(path):
        try:
            os.unlink(path)
        except OSError as exc:
            return ImportResult(False, f"Sai do registo, mas o ficheiro ficou: {exc}")
    return ImportResult(True, path=path)
