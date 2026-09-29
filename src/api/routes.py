"""
pHantasma REST API Routes

Flask endpoints for Android app and CLI integration.

Endpoints:
- GET /health, /api/health: Health check
- GET /api/info: Server info and capabilities
- POST /api/command: Execute voice/text command
- POST /api/stt: Speech-to-text
- POST /api/tts: Text-to-speech
- GET /api/devices: List configured devices
- POST /api/devices/<name>/control: Control device
- GET/POST /api/memory: List/add memory entries
- GET/DELETE /api/memory/<key>: Get/delete memory entry
"""

import base64
import io
import logging
import os
import time
from datetime import datetime, timedelta

import numpy as np
import soundfile as sf
from flask import Flask, Response, jsonify, request

from config import config
from src.api.admin import admin_bp
from src.api.models import (
    CommandRequest,
    CommandResponse,
    CommandType,
    DeviceControlRequest,
    DeviceControlResponse,
    DeviceInfo,
    HealthStatus,
    MemoryEntry,
    MemoryRequest,
    MemoryResponse,
    ServerInfo,
    STTRequest,
    STTResponse,
    TTSRequest,
    TTSResponse,
)
from src.pipeline.llm import chat as llm_chat
from src.pipeline.stt import transcribe as stt_transcribe
from src.pipeline.tts import synthesize as tts_synthesize

logger = logging.getLogger("phantasma.api")


def _audio_to_base64_wav(audio_data: np.ndarray, sample_rate: int) -> str:
    """Convert audio numpy array to base64-encoded WAV.

    Args:
        audio_data: Audio samples as int16 numpy array.
        sample_rate: Sample rate in Hz.

    Returns:
        Base64-encoded WAV data as UTF-8 string.
    """
    buf = io.BytesIO()
    sf.write(buf, audio_data, sample_rate, format="WAV")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _decode_base64_audio(audio_b64: str) -> tuple[np.ndarray, int]:
    """Decode base64 audio to float32 numpy array at 16kHz.

    Tries to read as WAV/MP3/OGG via soundfile. Falls back to raw PCM.

    Args:
        audio_b64: Base64-encoded audio data.

    Returns:
        Tuple of (audio_data: float32 array, sample_rate: int).
    """
    audio_bytes = base64.b64decode(audio_b64)

    # Try to read as WAV/OGG/MP3 via soundfile
    try:
        bio = io.BytesIO(audio_bytes)
        audio_data, sample_rate = sf.read(bio, dtype="float32")
        return audio_data, sample_rate
    except Exception:
        pass

    # Fallback: assume raw PCM int16 16kHz mono
    audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    return audio_data, 16000


def _resample_to_16khz(audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
    """Resample audio to 16kHz using linear interpolation.

    Args:
        audio_data: Audio samples as float32 array.
        sample_rate: Current sample rate in Hz.

    Returns:
        Resampled audio as float32 array at 16kHz.
    """
    if sample_rate == 16000:
        return audio_data

    ratio = 16000 / sample_rate
    new_length = int(len(audio_data) * ratio)
    indices = np.linspace(0, len(audio_data) - 1, new_length)
    return np.interp(indices, np.arange(len(audio_data)), audio_data).astype(np.float32)


def _execute_llm_tts(text: str, start_time: float) -> tuple[str, str | None, float]:
    """Execute LLM query and TTS synthesis.

    Args:
        text: User input text.
        start_time: Start time from time.perf_counter().

    Returns:
        Tuple of (response_text, audio_base64_or_none, processing_time_ms).
    """
    # LLM
    llm_result = llm_chat(text)
    if not llm_result.success:
        elapsed = (time.perf_counter() - start_time) * 1000
        return "", None, elapsed

    response_text = llm_result.data

    # TTS
    tts_result = tts_synthesize(response_text)
    audio_b64 = None
    if tts_result.success:
        audio_data, sample_rate = tts_result.data
        audio_b64 = _audio_to_base64_wav(audio_data, sample_rate)

    elapsed = (time.perf_counter() - start_time) * 1000
    return response_text, audio_b64, elapsed


def _handle_memory_command(text: str) -> Response:
    """Handle memory-related commands (memoriza, lembra, guarda).

    Args:
        text: Command text.

    Returns:
        Flask JSON response with CommandResponse.
    """
    # Simplified - would parse "memoriza que..." and store in SQLite
    return jsonify(
        CommandResponse(
            success=True, text="Memorizado com sucesso.", processing_time_ms=10.0
        ).model_dump()
    )


def _handle_device_command(text: str) -> Response:
    """Handle device control commands (liga, desliga, etc.).

    Args:
        text: Command text.

    Returns:
        Flask JSON response with CommandResponse.
    """
    # Simplified - would parse device name/action and call skill
    return jsonify(
        CommandResponse(
            success=True,
            text="Comando de dispositivo executado.",
            processing_time_ms=50.0,
        ).model_dump()
    )


def _is_memory_command(text: str) -> bool:
    """Check if text is a memory command."""
    return text.lower().strip().startswith(("memoriza", "lembra", "guarda"))


def _is_device_command(text: str) -> bool:
    """Check if text is a device control command."""
    device_keywords = [
        "liga",
        "desliga",
        "liga o",
        "desliga o",
        "abre",
        "fecha",
    ]
    text_lower = text.lower().strip()
    return any(kw in text_lower for kw in device_keywords)


def create_app(pipeline=None) -> Flask:
    """Create Flask application with all routes.

    Args:
        pipeline: Optional PhantasmaPipeline instance for health check.

    Returns:
        Configured Flask application.
    """
    app = Flask(__name__)
    # (actor, emoji, message_id) -> monotonic timestamps, for replay
    # suppression on the reaction endpoint.
    _REACTION_SEEN: dict = {}
    # Access logging is attached to the WSGI layer, not by replacing `app`, so
    # the Flask object (and therefore test_client()/config) survives intact.
    from src.api.access_log import install as _install_access_log

    app = _install_access_log(app)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max upload
    # Secret key for sessions (set via env var SECRET_KEY in production)
    app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
    # Session lifetime 30 days
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

    # Store pipeline reference for route handlers
    app.pipeline = pipeline

    # Register admin blueprint
    app.register_blueprint(admin_bp)

    # CORS for Android app
    @app.after_request
    def after_request(response):
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "GET,PUT,POST,DELETE,OPTIONS")
        return response

    # ============ Legacy Discord/UI contract ============

    @app.route("/comando", methods=["POST"])
    def comando():
        """Legacy endpoint used by the vendored Discord skill and UI.

        Contract: POST {"prompt": "<text>"} -> {"status": "ok", "response": "..."}.
        Routes through the same skill->LLM logic as the voice path, so a
        Discord/UI command behaves identically to a spoken command.

        Returns:
            JSON with "status" and "response" keys, mirroring the remote
            assistant.py contract.
        """
        try:
            data = request.get_json(silent=True) or {}
            prompt = (data.get("prompt") or "").strip()
            if not prompt:
                return jsonify({"status": "error", "message": "Prompt vazio"}), 400

            response = pipeline.respond_to_text(prompt) if pipeline else None
            if response is None:
                return jsonify({"status": "error", "message": "Sem resposta"}), 502

            return jsonify({"status": "ok", "response": response})
        except Exception as e:
            logger.error(f"/comando error: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    # Wire skill register_routes (UI, devices, etc.)
    if pipeline and hasattr(pipeline, "_skill_loader"):
        for skill in pipeline._skill_loader.skills:
            module = getattr(skill, "_module", None)
            if module and hasattr(module, "register_routes"):
                try:
                    module.register_routes(app)
                    logger.info(f"Registered routes for skill: {skill.NAME}")
                except Exception as e:
                    logger.warning(f"Failed to register routes for {skill.NAME}: {e}")

    # ============ Legacy device/skill endpoints (UI) ============

    @app.route("/get_devices", methods=["GET"])
    def get_devices():
        """List all configured devices from config for UI toggles/status."""
        try:
            toggles, status = [], []

            def keys(attr: str):
                return list(getattr(config, attr).keys()) if hasattr(config, attr) else []

            # Config instance uses lowercase attribute names
            for n in keys("tuya_devices"):
                if any(x in n.lower() for x in ["sensor", "temp"]):
                    status.append(n)
                else:
                    toggles.append(n)
            for n in keys("miio_devices") + keys("ewelink_devices"):
                toggles.append(n)
            for n in keys("cloogy_devices"):
                if "casa" in n.lower():
                    status.append(n)
                else:
                    toggles.append(n)
            if hasattr(config, "shelly_gas_url") and config.shelly_gas_url:
                status.append("Sensor de Gás")
            # Chacon plug is not a cloud device: it has no entry in any *_devices
            # dict, but it IS controllable, so it must get a tile like the rest.
            # Gated on the IP being configured, not on the plug being online --
            # reachability is what /device_status reports, not what a listing means.
            plug_ip = getattr(config, "chacon_plug_ip", "")
            if plug_ip:
                plug_name = getattr(config, "chacon_plug_name", "") or "luz do balcão"
                if plug_name not in toggles:
                    toggles.append(plug_name)

            return jsonify(
                {
                    "status": "ok",
                    "devices": {
                        "toggles": toggles,
                        "status": status,
                    },
                }
            )
        except Exception as e:
            logger.error(f"/get_devices error: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/device_status", methods=["GET"])
    def device_status():
        """Get status of a specific device by nickname."""
        try:
            nickname = request.args.get("nickname", "")
            if not nickname:
                return jsonify({"state": "unreachable"}), 400

            # Check skills that have get_status function
            for skill in pipeline._skill_loader.skills:
                module = getattr(skill, "_module", None)
                candidates = []
                if module and hasattr(module, "get_status_for_device"):
                    candidates.append(module)
                if hasattr(skill, "get_status_for_device"):
                    candidates.append(skill)
                for impl in candidates:
                    try:
                        res = impl.get_status_for_device(nickname)
                        if res and res.get("state") != "unreachable":
                            return jsonify(res)
                    except Exception:
                        continue

            return jsonify({"state": "unreachable"})
        except Exception as e:
            logger.error(f"/device_status error: {e}")
            return jsonify({"state": "unreachable"}), 500

    @app.route("/device_action", methods=["POST"])
    def device_action():
        """Execute action on a device via route_and_respond equivalent."""
        try:
            data = request.get_json(silent=True) or {}
            device = data.get("device", "")
            action = data.get("action", "")
            if not device or not action:
                return jsonify({"status": "error", "message": "device e action obrigatórios"}), 400

            prompt = f"{action} o {device}"
            response = pipeline.respond_to_text(prompt) if pipeline else None
            if response is None:
                return jsonify({"status": "error", "message": "Sem resposta"}), 502

            return jsonify({"status": "ok", "response": response})
        except Exception as e:
            logger.error(f"/device_action error: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/help", methods=["GET"])
    def get_help():
        """List all skills and their triggers for UI help."""
        try:
            cmds = {"diz": "TTS"}
            if pipeline and hasattr(pipeline, "_skill_loader"):
                for skill in pipeline._skill_loader.skills:
                    triggers = getattr(skill, "TRIGGERS", [])
                    cmds[skill.NAME] = ", ".join(triggers[:3]) + "..." if triggers else "Ativo"
            return jsonify({"status": "ok", "commands": cmds})
        except Exception as e:
            logger.error(f"/help error: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    # ============ Health & Info ============

    @app.route("/health", methods=["GET"])
    @app.route("/api/health", methods=["GET"])
    def health():
        """Health check endpoint.

        Returns component health status and uptime.
        """
        import os

        import psutil

        uptime = time.time() - psutil.Process(os.getpid()).create_time()

        components = {
            "pipeline": "healthy" if pipeline and pipeline._running else "stopped",
            "stt": "healthy",
            "llm": "healthy",
            "tts": "healthy",
            "audio": "healthy",
        }

        # Check Ollama connectivity — try primary then fallback, mirroring the
        # runtime LLM chain in assistant.py (which probes reachability and
        # fails over to the fallback host). Uses list() for reachability only,
        # not check_connection(), which assumes a dict-based models API.
        try:
            import ollama

            llm_cfg = config.llm
            hosts = [llm_cfg.host, llm_cfg.host_fallback]
            if hosts[1] == hosts[0]:
                hosts = hosts[:1]
            alive = False
            for host in hosts:
                try:
                    ollama.Client(host=host, timeout=2).list()
                    alive = True
                    break
                except Exception:
                    continue
            components["ollama"] = "healthy" if alive else "unhealthy"
        except Exception:
            components["ollama"] = "unhealthy"

        all_healthy = all(v == "healthy" for v in components.values())
        status = "healthy" if all_healthy else "degraded"

        return jsonify(
            HealthStatus(
                status=status,
                version="0.1.0",
                uptime_seconds=uptime,
                components=components,
                timestamp=datetime.now(),
            ).model_dump()
        )

    @app.route("/api/info", methods=["GET"])
    def info():
        """Server information for discovery."""
        return jsonify(
            ServerInfo(
                version="0.1.0",
                endpoints=[
                    "/health",
                    "/api/command",
                    "/api/devices",
                    "/api/devices/<name>/control",
                    "/api/memory",
                    "/api/memory/<key>",
                    "/api/stt",
                    "/api/tts",
                ],
                capabilities=["voice", "devices", "memory", "tts", "stt"],
            ).model_dump()
        )

    # ============ Voice Commands ============

    # ------------------------------------------------------------------
    # Graph editing. Mutating a brain, so gated like the admin surface: an
    # admin session OR the loopback bypass. Read routes above stay open.
    # ------------------------------------------------------------------
    def _may_edit():
        """Is this requester allowed to mutate the graph?

        Same contract as the admin pages: a resolved admin identity, or the
        loopback bypass. Returns None when permitted, else the refusal.
        """
        from src.api import admin as _admin

        user = _admin._current_user_data()
        if user is None:
            user = _admin._bypass_or_none()
        if user is not None and user.get("role") == "admin":
            return None
        return {"ok": False, "error": "admin required"}, (403 if user is not None else 401)

    def _db():
        import config as _c

        return _c.BRAIN_DB_PATH

    @app.route("/api/graph/edge", methods=["POST"])
    def graph_edge_edit():
        """Update, create, relink or delete an edge.

        One route with an explicit ``op`` rather than four URLs: the audit
        record, the validation and the permission check are identical for all
        four, and four routes would be four places to keep them in step.
        """
        refused = _may_edit()
        if refused:
            body, status = refused
            return jsonify(body), status
        from src.brain import graph_edit as _ge

        data = request.get_json(silent=True) or {}
        op = str(data.get("op", "update"))
        key = str(data.get("node_key", "") or "")
        actor = str(data.get("actor", "local"))[:64]
        try:
            if op == "update":
                changes = {f: data[f] for f in ("weight", "affinity", "label") if f in data}
                out = _ge.update_edge(_db(), key, changes, actor=actor)
            elif op == "create":
                out = _ge.create_edge(
                    _db(),
                    str(data.get("source", "")),
                    str(data.get("target", "")),
                    weight=data.get("weight", 1.0),
                    affinity=data.get("affinity", 0.0),
                    label=str(data.get("label", "")),
                    actor=actor,
                )
            elif op == "delete":
                out = _ge.delete_edge(_db(), key, actor=actor)
            elif op == "node":
                # The editable set comes from graph_edit.NODE_COLUMNS, not from a
                # list repeated here. Repeating it is what made node weight
                # unsaveable: `weight` was added to NODE_COLUMNS and to the UI,
                # but this hardcoded tuple was never updated, so the field was
                # silently dropped and the POST still answered 200. update_node
                # already rejects anything outside NODE_COLUMNS, so passing
                # through needs no second allowlist.
                changes = {f: data[f] for f in _ge.NODE_COLUMNS if f in data}
                out = _ge.update_node(_db(), key, changes, actor=actor)
            elif op == "node.delete":
                out = _ge.delete_node(_db(), key, actor=actor)
            else:
                return jsonify(
                    {
                        "ok": False,
                        "error": f"unknown op {op!r}",
                        "supported": ["update", "create", "delete", "node"],
                    }
                ), 400
        except _ge.EditError as exc:
            # 422: well-formed, semantically wrong. Distinct from 400 so the
            # caller can tell "you asked for nonsense" from "that is not an edge".
            return jsonify({"ok": False, "error": str(exc)}), 422
        return jsonify({"ok": True, "op": op, "result": out})

    @app.route("/api/graph/resolve", methods=["POST"])
    def graph_resolve_dangling():
        """Resolve one dangling edge endpoint. Human-initiated (T046).

        Deliberately NOT wired into the sleep cycle. Relinking and promoting are
        opposite judgements about the same observation -- same concept under
        another name, or a genuine new concept -- and picking one automatically
        either loses an edge or duplicates a concept. The automatic path is
        T047, and it goes through this same endpoint with a recorded rationale.

        `side` is mandatory: an edge can dangle on both ends and resolving the
        wrong one is a silent no-op on the number the operator is watching.
        """
        refused = _may_edit()
        if refused:
            body, status = refused
            return jsonify(body), status
        from src.brain import graph_edit as _ge

        data = request.get_json(silent=True) or {}
        edge_key = str(data.get("edge_key", "") or "")
        side = str(data.get("side", "") or "")
        action = str(data.get("action", "") or "")
        target_label = data.get("target_label")
        actor = str(data.get("actor", "local"))[:64]
        rationale = data.get("rationale")
        if not edge_key or not side or not action:
            return jsonify(
                {
                    "ok": False,
                    "error": "edge_key, side and action are required",
                }
            ), 400
        try:
            out = _ge.resolve_dangling(
                _db(),
                edge_key,
                side,
                action,
                target_label=str(target_label) if target_label else None,
                actor=actor,
                rationale=str(rationale)[:500] if rationale else None,
            )
        except _ge.EditError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        return jsonify({"ok": True, "op": "resolve", "result": out})

    @app.route("/api/graph/node", methods=["POST"])
    def graph_node_from_text():
        """Create a node from a piece of text the operator chose to keep.

        Not reachable automatically. A reaction whose reply names no node leaves
        the graph alone and the UI offers this as a deliberate press, so the
        graph gains a concept because someone decided it should, not because
        something was said.
        """
        refused = _may_edit()
        if refused:
            body, status = refused
            return jsonify(body), status
        from src.brain import graph_edit as _ge

        data = request.get_json(silent=True) or {}
        label = str(data.get("label", "") or "")
        text = str(data.get("text", "") or "")
        if not label.strip():
            return jsonify({"ok": False, "error": "label is required"}), 400
        try:
            out = _ge.create_node_from_text(
                _db(),
                label,
                text,
                actor=str(data.get("actor", "ui"))[:64],
                rationale=str(data.get("rationale", ""))[:500] or None,
            )
        except _ge.EditError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        return jsonify({"ok": True, "op": "node.from_text", "result": out})

    @app.route("/api/graph/rag", methods=["POST"])
    def graph_rag_edit():
        """Correct a RAG entry stored on a memory row."""
        refused = _may_edit()
        if refused:
            body, status = refused
            return jsonify(body), status
        from src.brain import graph_edit as _ge

        data = request.get_json(silent=True) or {}
        try:
            out = _ge.update_rag(
                _db(),
                str(data.get("key", "")),
                {f: data[f] for f in ("summary", "tags", "facts", "mermaid") if f in data},
                actor=str(data.get("actor", "local"))[:64],
            )
        except _ge.EditError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        return jsonify({"ok": True, "result": out})

    @app.route("/api/graph/audit", methods=["GET"])
    def graph_audit():
        """Recent graph edits. Readable, because an audit nobody can read is
        not an audit."""
        from src.brain import graph_edit as _ge

        return jsonify(
            {
                "edits": _ge.list_audit(_db(), int(request.args.get("limit", 50))),
            }
        )

    @app.route("/api/graph/flybrain", methods=["GET"])
    def graph_flybrain_state():
        """The live ring state, for the explorer's inspector.

        Read-only on purpose. Training stays on the reaction path; exposing a
        write here would let a slider drag become a learning event, which the
        owner explicitly ruled out.
        """
        from src.brain import graph_edit as _ge

        return jsonify({"state": _ge.flybrain_state(_db())})

    @app.route("/api/reaction", methods=["POST"])
    def reaction():
        """Record a FlyBrain reward from a chat reaction.

        Exposes the reward path that already worked over Discord, so the web
        chat and Discord train the same brain through one implementation
        (src/brain/reactions.py).

        Guarded because a reaction is a TRAINING SIGNAL, not a like. Unbounded,
        a loop of POSTs would drive the ring state anywhere, and the browser is
        an untrusted client. The limits are deliberately generous for a human
        tapping a message and deliberately tight for a script.
        """
        from src.brain import reactions as _reactions

        try:
            data = request.get_json(silent=True) or {}
        except Exception:
            data = {}
        emoji = str(data.get("emoji", "")).strip()
        source = str(data.get("source", "web"))[:24]
        actor = str(data.get("actor", "local"))[:64]

        if not emoji:
            return jsonify({"ok": False, "error": "missing emoji"}), 400
        # Pick up weights the owner changed in /admin before validating, so an
        # emoji the owner just re-weighted is accepted this very next click.
        _reactions.reload_reaction_weights()
        if emoji not in _reactions.REACTION_REWARD:
            # 422, not 400: the request was well-formed but the value is not
            # supported. Distinct codes because they need distinct fixes.
            return jsonify(
                {
                    "ok": False,
                    "error": "unsupported emoji",
                    "supported": list(_reactions.SUPPORTED_EMOJI),
                }
            ), 422

        # Replay guard: one reward per (message, emoji) per client, for a
        # window. Without it, holding down a tap is a training signal.
        key = (actor[:32], emoji, str(data.get("message_id", ""))[:64])
        now = time.monotonic()
        bucket = [t for t in _REACTION_SEEN.get(key, ()) if now - t < 30]
        if bucket:
            return jsonify(
                {
                    "ok": False,
                    "error": "already reacted to this message",
                    "retry_after": round(30 - (now - bucket[0]), 1),
                }
            ), 429
        bucket.append(now)
        _REACTION_SEEN[key] = bucket[-4:]
        if len(_REACTION_SEEN) > 500:  # bound memory under key spraying
            for k in list(_REACTION_SEEN):
                if not [t for t in _REACTION_SEEN[k] if now - t < 30]:
                    _REACTION_SEEN.pop(k, None)

        # The message text travels with the reaction so the graph reward can
        # resolve the node THAT reply is about, instead of the ambient topic.
        message_text = data.get("message_text")
        report = _reactions.record(
            emoji,
            source=source,
            actor=actor,
            message_text=str(message_text)[:4000] if message_text else None,
        )
        status = 200 if report.get("applied") else 202
        return jsonify({"ok": report.get("applied", False), **report}), status

    @app.route("/api/reactions", methods=["GET"])
    def reaction_map():
        """Which emojis carry a reward, and how much.

        The UI renders its buttons from this, so the UI and the training
        behaviour cannot drift apart: adding an emoji server-side makes it
        appear, and removing one removes it.
        """
        from src.brain import reactions as _reactions

        return jsonify(_reactions.describe())

    @app.route("/api/command", methods=["POST"])
    def command():
        """Execute a voice/text command.

        Request body: CommandRequest
        Response: CommandResponse
        """
        start = time.perf_counter()

        try:
            data = request.get_json()
            if not data:
                return jsonify(
                    CommandResponse(success=False, error="No JSON body provided").model_dump()
                ), 400

            cmd = CommandRequest(**data)
            logger.info(f"Command received: type={cmd.type}, text={cmd.text[:100]}")

            if not pipeline:
                return jsonify(
                    CommandResponse(success=False, error="Pipeline not available").model_dump()
                ), 503

            # For text/voice commands, use LLM directly
            if cmd.type in (CommandType.TEXT, CommandType.VOICE):
                text_lower = cmd.text.lower().strip()

                # Memory commands
                if _is_memory_command(text_lower):
                    return _handle_memory_command(cmd.text)

                # Device control
                if _is_device_command(text_lower):
                    return _handle_device_command(cmd.text)

                # General LLM query
                response_text, audio_b64, elapsed = _execute_llm_tts(cmd.text, start)

                return jsonify(
                    CommandResponse(
                        success=True,
                        text=response_text,
                        audio_base64=audio_b64,
                        audio_format="wav" if audio_b64 else None,
                        processing_time_ms=elapsed,
                    ).model_dump()
                )

            return jsonify(
                CommandResponse(
                    success=False, error=f"Unsupported command type: {cmd.type}"
                ).model_dump()
            ), 400

        except Exception as e:
            logger.error(f"Command error: {e}")
            return jsonify(
                CommandResponse(
                    success=False,
                    error=str(e),
                    processing_time_ms=(time.perf_counter() - start) * 1000,
                ).model_dump()
            ), 500

    # ============ STT ============

    @app.route("/api/stt", methods=["POST"])
    def stt():
        """Speech-to-text endpoint.

        Request body: STTRequest (audio_base64, language?, format?)
        Response: STTResponse
        """
        start = time.perf_counter()

        try:
            data = request.get_json()
            if not data:
                return jsonify(
                    STTResponse(success=False, error="No JSON body provided").model_dump()
                ), 400

            stt_req = STTRequest(**data)

            # Decode and resample audio
            audio_data, sample_rate = _decode_base64_audio(stt_req.audio_base64)
            audio_data = _resample_to_16khz(audio_data, sample_rate)

            # Transcribe
            result = stt_transcribe(audio_data, language=stt_req.language)

            return jsonify(
                STTResponse(
                    success=result.success,
                    text=result.data if result.success else None,
                    language=None,
                    duration_ms=(time.perf_counter() - start) * 1000,
                    error=result.error,
                ).model_dump()
            )

        except Exception as e:
            logger.error(f"STT error: {e}")
            return jsonify(
                STTResponse(
                    success=False,
                    error=str(e),
                    duration_ms=(time.perf_counter() - start) * 1000,
                ).model_dump()
            ), 500

    # ============ TTS ============

    @app.route("/api/tts", methods=["POST"])
    def tts():
        """Text-to-speech endpoint.

        Request body: TTSRequest (text, language?, format?)
        Response: TTSResponse (audio_base64, format, duration_ms)
        """
        start = time.perf_counter()

        try:
            data = request.get_json()
            if not data:
                return jsonify(
                    TTSResponse(success=False, error="No JSON body provided").model_dump()
                ), 400

            tts_req = TTSRequest(**data)

            result = tts_synthesize(tts_req.text)

            if not result.success:
                elapsed = (time.perf_counter() - start) * 1000
                return jsonify(
                    TTSResponse(success=False, error=result.error, duration_ms=elapsed).model_dump()
                )

            audio_data, sample_rate = result.data
            audio_b64 = _audio_to_base64_wav(audio_data, sample_rate)

            elapsed = (time.perf_counter() - start) * 1000
            return jsonify(
                TTSResponse(
                    success=True,
                    audio_base64=audio_b64,
                    format="wav",
                    duration_ms=elapsed,
                ).model_dump()
            )

        except Exception as e:
            logger.error(f"TTS error: {e}")
            elapsed = (time.perf_counter() - start) * 1000
            return jsonify(
                TTSResponse(success=False, error=str(e), duration_ms=elapsed).model_dump()
            ), 500

    # ============ Devices ============

    @app.route("/api/devices", methods=["GET"])
    def list_devices():
        """List all configured devices from config.

        Returns JSON with list of DeviceInfo objects.
        """
        try:
            devices = []

            # Tuya devices
            for name, cfg in config.tuya_devices.items():
                dtype = "tuya_light" if "luz" in name.lower() else "tuya_switch"
                devices.append(
                    DeviceInfo(name=name, type=dtype, state={"online": True}).model_dump()
                )

            # Xiaomi devices
            for name, cfg in config.miio_devices.items():
                dtype = "xiaomi_vacuum" if "robot" in name.lower() else "xiaomi_light"
                devices.append(
                    DeviceInfo(name=name, type=dtype, state={"online": True}).model_dump()
                )

            return jsonify({"devices": devices})

        except Exception as e:
            logger.error(f"List devices error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/devices/<name>/control", methods=["POST"])
    def control_device(name: str):
        """Control a specific device by name.

        Request body: DeviceControlRequest
        Response: DeviceControlResponse
        """
        try:
            data = request.get_json()
            if not data:
                return jsonify(
                    DeviceControlResponse(
                        success=False, device_name=name, error="No JSON body provided"
                    ).model_dump()
                ), 400

            ctrl_req = DeviceControlRequest(**data)

            # Find device in config
            if name in config.tuya_devices:
                _ = config.tuya_devices[name]  # device_cfg
                _ = "tuya"  # device_type
            elif name in config.miio_devices:
                _ = config.miio_devices[name]  # device_cfg
                _ = "xiaomi"  # device_type
            else:
                return jsonify(
                    DeviceControlResponse(
                        success=False, device_name=name, error="Device not found"
                    ).model_dump()
                ), 404

            # Execute control (simplified - would use actual skill logic)
            new_state = {"action": ctrl_req.action, "timestamp": time.time()}

            return jsonify(
                DeviceControlResponse(
                    success=True, device_name=name, new_state=new_state
                ).model_dump()
            )

        except Exception as e:
            logger.error(f"Device control error: {e}")
            return jsonify(
                DeviceControlResponse(success=False, device_name=name, error=str(e)).model_dump()
            ), 500

    # ============ Memory ============

    @app.route("/api/memory", methods=["GET"])
    def list_memory():
        """List all memory entries.

        Returns JSON with list of MemoryEntry objects.
        """
        try:
            # Would query SQLite memory DB
            entries = []  # Placeholder
            return jsonify(MemoryResponse(success=True, entries=entries).model_dump())
        except Exception as e:
            return jsonify(MemoryResponse(success=False, error=str(e)).model_dump()), 500

    @app.route("/api/memory", methods=["POST"])
    def add_memory():
        """Add a memory entry.

        Request body: MemoryRequest
        Response: MemoryResponse
        """
        try:
            data = request.get_json()
            if not data:
                return jsonify(
                    MemoryResponse(success=False, error="No JSON body").model_dump()
                ), 400

            mem_req = MemoryRequest(**data)
            # Would store in SQLite
            entry = MemoryEntry(key=mem_req.key, value=mem_req.value, created_at=datetime.now())
            return jsonify(MemoryResponse(success=True, entry=entry).model_dump())
        except Exception as e:
            return jsonify(MemoryResponse(success=False, error=str(e)).model_dump()), 500

    @app.route("/api/memory/<key>", methods=["GET"])
    def get_memory(key: str):
        """Get a specific memory entry by key.

        Response: MemoryResponse
        """
        try:
            # Would query SQLite
            return jsonify(MemoryResponse(success=False, error="Not implemented").model_dump()), 501
        except Exception as e:
            return jsonify(MemoryResponse(success=False, error=str(e)).model_dump()), 500

    @app.route("/api/memory/graph", methods=["GET"])
    def api_memory_graph():
        """Real 3D-explorer payload built from brain.db records.

        Every node/edge traces back to a stored row. Stored references whose
        target does not exist are flagged ``unresolved`` and counted in
        ``stats`` instead of being faked. See src/api/memory_graph.py.
        """
        from src.api.memory_graph import build_graph_from_db

        try:
            return jsonify(build_graph_from_db())
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"memory graph failed: {exc}")
            return jsonify({"nodes": [], "links": [], "stats": {"error": str(exc)}}), 500

    @app.route("/memory/3d", methods=["GET"])
    def view_3d_memory():
        """Serves the HTML5 3D Memory & Connectome Explorer."""
        from flask import send_from_directory

        return send_from_directory(config.public_dir, "memory_3d.html")

    @app.route("/public/<path:filename>")
    def public_files(filename: str):
        """Serve the explorer's vendored libraries and ES modules.

        ``send_from_directory`` refuses path traversal, so requests cannot
        escape the configured public directory.
        """
        from flask import send_from_directory

        return send_from_directory(config.public_dir, filename)

    @app.route("/api/memory/<key>", methods=["DELETE"])
    def delete_memory(key: str):
        """Delete a memory entry by key.

        Response: MemoryResponse
        """
        try:
            # Would delete from SQLite
            return jsonify(MemoryResponse(success=True).model_dump())
        except Exception as e:
            return jsonify(MemoryResponse(success=False, error=str(e)).model_dump()), 500

    return app
