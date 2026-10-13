"""T071: the wizard's HTTP surface.

Four things happen here and none of them is training:

    record -> check the clip and keep it if it is usable
    export -> bundle everything recorded into something the Colab accepts
    import -> take a trained `.onnx` back, validate it, install it
    users  -> who is enrolled and how far along they are

Every route is behind the admin gate. This page writes a file that will be
loaded by the detector that decides when the whole assistant starts listening,
and the recordings are biometric data.

A note on what enrolment is: it is NOT access control. A wake-word model
answers "someone said the phrase". The microphone hears whoever is in the room.
The owner was told this on 2026-10-13 and chose enrolment over speaker-ID.
Nothing in this module should ever be described to the owner as identifying
them.
"""

import base64
import logging
import os

import numpy as np
from flask import jsonify, send_file

from src.wakeword.dataset import Sample, build_bundle, decode_clip
from src.wakeword.importer import import_model
from src.wakeword.registry import WakeWordRegistry, new_entry, slugify

logger = logging.getLogger(__name__)

# Clips arrive one at a time and this caps a single upload. A MediaRecorder blob
# for a two-second clip is a few tens of kB; 4 MB is generous and stops a
# runaway client from filling the staging directory with megabytes per request.
MAX_CLIP_BYTES = 4 * 1024 * 1024


def _registry() -> WakeWordRegistry:
    import config as config_module

    return WakeWordRegistry(config_module.WAKEWORD_REGISTRY_PATH)


def _staging_root() -> str:
    import config as config_module

    return config_module.WAKEWORD_STAGING_DIR


def _models_dir() -> str:
    import config as config_module

    return config_module.WAKEWORD_MODELS_DIR


def _admin_only():
    """The gate, or None. Kept as one function so it cannot drift per route."""
    from src.api import ui_auth

    if ui_auth.is_admin():
        return None
    return jsonify({"error": "so admin"}), 403


def _staging_dir(slug: str) -> str:
    return os.path.join(_staging_root(), slug)


def _staged(slug: str, kind: str) -> list:
    """Every clip already kept for this person and kind, oldest first."""
    directory = os.path.join(_staging_dir(slug), kind)
    if not os.path.isdir(directory):
        return []
    names = sorted(n for n in os.listdir(directory) if n.endswith(".wav"))
    return [Sample(label=kind, audio=_read_wav(os.path.join(directory, n)),
                   index=i, origin=n)
            for i, n in enumerate(names)]


def _read_wav(path: str) -> np.ndarray:
    import wave

    with wave.open(path) as handle:
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _write_wav(path: str, audio: np.ndarray) -> None:
    import wave

    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm.tobytes())


def register(app) -> None:
    """Attach the wizard routes. Called from skill_ui.register_routes."""

    # --- who is enrolled ------------------------------------------------

    def users_api():
        denied = _admin_only()
        if denied:
            return denied

        registry = _registry()
        rows = []
        for entry in registry.all():
            staged_pos = len(_staged(entry.slug, "positive"))
            staged_neg = len(_staged(entry.slug, "negative"))
            rows.append({
                "slug": entry.slug,
                "label": entry.label,
                "phrase": entry.phrase,
                "trained": bool(entry.model_path
                                and os.path.isfile(entry.model_path)),
                "model_path": entry.model_path if entry.model_path else None,
                "trained_at": entry.trained_at,
                "sample_count": entry.sample_count,
                # Always present and always possibly null. The UI prints
                # "unknown" for null; it must never print 0.
                "false_positive_rate": entry.false_positive_rate,
                "staged_positives": staged_pos,
                "staged_negatives": staged_neg,
            })
        return jsonify({"users": rows,
                        "loaded_models": registry.model_paths()})

    app.add_url_rule("/api/wakeword/users", "wakeword.users", users_api,
                     methods=["GET"])

    # --- record one clip -----------------------------------------------

    def record_api():
        denied = _admin_only()
        if denied:
            return denied

        body = request_json()
        slug = slugify(str(body.get("label") or ""))
        kind = str(body.get("kind") or "positive")
        if kind not in ("positive", "negative"):
            return jsonify({"error": "kind tem de ser positive ou negative"}), 400

        # Enrol first, check second. A refused clip must still leave the owner
        # visible on the page, or the wizard looks broken rather than honest.
        # Inert until a model exists: `enrolled()` skips entries with no
        # `model_path`, and config.py only appends models that are on disk.
        registry = _registry()
        if registry.get(slug) is None:
            registry.upsert(new_entry(str(body.get("label") or slug),
                                       str(body.get("phrase") or "")))

        raw = body.get("audio") or ""
        try:
            data = base64.b64decode(raw)
        except Exception:  # noqa: BLE001
            return jsonify({"error": "audio invalido"}), 400
        if not data:
            return jsonify({"error": "audio vazio"}), 400
        if len(data) > MAX_CLIP_BYTES:
            return jsonify({"error": "clipe demasiado grande"}), 413

        try:
            audio = decode_clip(data)
        except Exception as exc:  # noqa: BLE001 - a bad container, not a crash
            logger.warning("Nao consegui descodificar o clipe: %s", exc)
            return jsonify({"error": f"nao consegui ler o audio: {exc}"}), 400

        sample = Sample(label=kind, audio=audio, index=0)
        check = validate_one(sample)
        if not check["ok"]:
            # Rejected clips are reported per clip and NOT stored, so the
            # count on the page only ever means "usable clips".
            return jsonify({"ok": False, "check": check})

        directory = os.path.join(_staging_dir(slug), kind)
        os.makedirs(directory, exist_ok=True)
        existing = len([n for n in os.listdir(directory) if n.endswith(".wav")])
        _write_wav(os.path.join(directory, f"{kind}_{existing:03d}.wav"), audio)

        return jsonify({"ok": True, "check": check,
                        "staged_positives": len(_staged(slug, "positive")),
                        "staged_negatives": len(_staged(slug, "negative"))})

    app.add_url_rule("/api/wakeword/record", "wakeword.record", record_api,
                     methods=["POST"])

    # --- export a training bundle --------------------------------------

    def export_api():
        denied = _admin_only()
        if denied:
            return denied

        body = request_json()
        registry = _registry()
        slug = slugify(str(body.get("label") or body.get("slug") or ""))
        entry = registry.get(slug)
        phrase = str(body.get("phrase") or (entry.phrase if entry else ""))

        result = build_bundle(
            (entry.label if entry else slug),
            _staged(slug, "positive"), _staged(slug, "negative"),
            phrase=phrase, out_dir=_staging_root())

        if not result["ok"]:
            # 422, not 500. "You did not record enough" is an ordinary outcome.
            return jsonify(result), 422
        return jsonify(result)

    app.add_url_rule("/api/wakeword/export", "wakeword.export", export_api,
                     methods=["POST"])

    def bundle_download(slug: str):
        denied = _admin_only()
        if denied:
            return denied
        path = os.path.join(_staging_root(), f"{slug}-wakeword.zip")
        if not os.path.isfile(path):
            return jsonify({"error": "bundle inexistente"}), 404
        return send_file(path, as_attachment=True,
                         download_name=f"{slug}-wakeword.zip")

    app.add_url_rule("/api/wakeword/bundle/<slug>", "wakeword.bundle",
                     bundle_download, methods=["GET"])

    # --- take a trained model back -------------------------------------

    def import_api():
        denied = _admin_only()
        if denied:
            return denied

        body = request_json()
        slug = slugify(str(body.get("label") or body.get("slug") or ""))
        raw = body.get("model") or ""
        try:
            data = base64.b64decode(raw)
        except Exception:  # noqa: BLE001
            return jsonify({"error": "modelo invalido"}), 400
        if len(data) > 100 * 1024 * 1024:
            return jsonify({"error": "modelo demasiado grande"}), 413

        registry = _registry()
        entry = registry.get(slug)
        # Sample count comes from what was actually recorded, so the entry
        # records what the model was trained from rather than nothing.
        positives = len(_staged(slug, "positive"))
        result = import_model(registry, slug, data, models_dir=_models_dir(),
                              label=entry.label if entry else slug)
        if not result.ok:
            return jsonify(result.to_dict()), 422

        entry = registry.get(slug)
        if entry is not None:
            entry.sample_count = positives
            registry.upsert(entry)
        result_dict = result.to_dict()
        result_dict["sample_count"] = positives
        result_dict["restart_required"] = True
        return jsonify(result_dict)

    app.add_url_rule("/api/wakeword/import", "wakeword.import", import_api,
                     methods=["POST"])

    def record_rate_api(slug: str):
        denied = _admin_only()
        if denied:
            return denied
        from flask import request

        try:
            value = float(request.args.get("rate", ""))
        except ValueError:
            return jsonify({"error": "rate tem de ser um numero"}), 400
        if not _registry().set_false_positive_rate(slug, value):
            return jsonify({"error": "utilizador desconhecido"}), 404
        return jsonify({"ok": True})

    app.add_url_rule("/api/wakeword/<slug>/fp", "wakeword.fp", record_rate_api,
                     methods=["POST"])

    # --- the page -------------------------------------------------------

    def wizard_page():
        from skills.skill_ui import wakeword_page

        return wakeword_page()

    app.add_url_rule("/treino", "wakeword.page", wizard_page, methods=["GET"])


def request_json() -> dict:
    from flask import request

    return request.get_json(silent=True) or {}


def validate_one(sample: Sample) -> dict:
    """One clip, one verdict, as a dict the page can render directly."""
    from src.wakeword.dataset import validate_samples

    return validate_samples([sample], [])[0].to_dict()
