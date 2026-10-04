"""The model page must be the truth, and it was a dead mirror.

Measured 2026-10-03. `/admin/config` showed, for the three model keys:

    OLLAMA_MODEL_PRIMARY    llama3.1:8b
    OLLAMA_MODEL_FALLBACK   qwen3:8b
    OLLAMA_VISION_MODEL     llava:7b

All three had been deleted from the hosts. `gemma3:4b` was answering.

Nothing applied the config table. No `os.environ[...]` write, no `putenv`, no
import of `admin` from `assistant.py`, `tools.py`, `config.py` or
`src/pipeline/`. The comment above `CONFIG_CONTROLS` claimed a boot overlay
honoured it "so secret service tokens stay in .env" -- that overlay was never
built. So for these keys the page was a mirror of a table nobody read: the owner
could save a value, see "guardado", and have no effect at all.

That is the worst of the three failures this file already documents, and it is
the one an owner cannot detect from the page. A wrong value in `.env` fails
loudly once the host cannot serve it; a value saved on a page that does nothing
looks exactly like success.

Precedence is store > `.env` > dataclass, matching the guest settings, where the
page is authoritative and the code says so. These three keys were the exception
and the exception was written nowhere an owner would read.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.brain import model_config as mc  # noqa: E402

ENV_PATH = "/tmp/opencode/model_config_test.env"


def _write_env(text: str) -> str:
    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write(text)
    return ENV_PATH


# --- precedence ------------------------------------------------------------

def test_the_store_wins_over_env(monkeypatch):
    """The owner's most recent explicit choice is the last word.

    Reversed from the measured behaviour, where the page had no say at all and
    `.env` decided no matter what was saved.
    """
    _write_env("OLLAMA_MODEL_PRIMARY=do-env:4b\n")
    monkeypatch.setattr(mc, "_from_env", lambda k: "do-env:4b")
    monkeypatch.setattr(mc, "_from_settings", lambda k: "da-pagina:4b")

    got = mc.resolve(mc.MODEL_PRIMARY)
    assert got.value == "da-pagina:4b", got
    assert got.source == "settings", got


def test_env_wins_when_the_store_is_empty(monkeypatch):
    """.env is host truth and survives a lost database."""
    monkeypatch.setattr(mc, "_from_settings", lambda k: None)
    monkeypatch.setattr(mc, "_from_env", lambda k: "do-env:4b")

    got = mc.resolve(mc.MODEL_PRIMARY)
    assert got.value == "do-env:4b"
    assert got.source == "env", got


def test_the_dataclass_default_is_a_last_resort_not_a_silent_winner(monkeypatch):
    """With neither source, the measured model answers.

    A default that quietly wins is how a host value fails open -- the
    CLAUDE.md rule, written for audio and true here too. What matters is that
    `source` says "default", so the page can show it instead of pretending the
    value came from somewhere the owner can see.
    """
    monkeypatch.setattr(mc, "_from_settings", lambda k: None)
    monkeypatch.setattr(mc, "_from_env", lambda k: None)

    got = mc.resolve(mc.MODEL_PRIMARY)
    assert got.value == "gemma3:4b", got
    assert got.source == "default", got


def test_an_empty_store_value_is_treated_as_unset(monkeypatch):
    """A blank row is not a model name.

    `set_setting` can store "", or a form can submit whitespace, and neither may
    become the model: Ollama 404s every request. Empty means "the owner never set
    one", so the next source down answers.

    Whitespace and not just "" -- the real store returns whatever the form sent,
    and a text input with a trailing space is a normal thing to submit.
    """
    monkeypatch.setattr(mc, "_from_settings", lambda k: "   ")
    monkeypatch.setattr(mc, "_from_env", lambda k: "do-env:4b")

    got = mc.resolve(mc.MODEL_PRIMARY)
    assert got.value == "do-env:4b", got
    assert got.source == "env", got


def test_an_unreadable_store_does_not_silence_the_assistant(monkeypatch):
    """A database error falls through to `.env` and is logged.

    Returning None is right -- the assistant must keep answering -- and being
    silent about it is not. The settings_store already logs this shape; the model
    name is load-bearing, so it logs too.
    """
    def boom(key):
        raise RuntimeError("database is locked")

    logged: list[str] = []
    monkeypatch.setattr(mc, "_from_settings", boom)
    monkeypatch.setattr(mc, "_from_env", lambda k: "do-env:4b")

    class _Log:
        def warning(self, *args):
            logged.append(args[0] % args[1:])

    monkeypatch.setattr(mc, "log", _Log())

    assert mc.resolve(mc.MODEL_PRIMARY).value == "do-env:4b"
    assert logged, "a falha foi engoleita em silencio"


def test_provenance_covers_every_key_it_resolves():
    """The page needs to say where each value came from."""
    prov = mc.provenance()
    for key in mc.DEFAULTS:
        assert key in prov, key
        assert prov[key] in ("settings", "env", "default"), (key, prov[key])


# --- the .env file write ---------------------------------------------------

def test_writing_the_env_preserves_comments_and_other_keys():
    """It is a file the owner maintains by hand, so it is edited, not rewritten.

    Losing a comment that explains a host value is how the next person stops
    knowing why a setting is the way it is.
    """
    path = _write_env(
        "# o host primario e a maquina do dono\n"
        "OLLAMA_HOST_PRIMARY=http://10.0.0.128:11434\n"
        "OLLAMA_MODEL_PRIMARY=antigo:4b\n"
        "\n"
        "OUTRA_COISA=manter\n"
    )
    assert mc.write_env_file("OLLAMA_MODEL_PRIMARY", "novo:4b", path) is True

    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "# o host primario e a maquina do dono" in text
    assert "OUTRA_COISA=manter" in text
    assert "OLLAMA_MODEL_PRIMARY=novo:4b" in text
    assert "antigo" not in text
    # The primary host must not have been touched by a model write.
    assert "OLLAMA_HOST_PRIMARY=http://10.0.0.128:11434" in text


def test_writing_a_key_that_is_absent_appends_it():
    path = _write_env("OUTRA_COISA=manter\n")
    assert mc.write_env_file("OLLAMA_VISION_MODEL", "gemma3:4b", path) is True
    with open(path, encoding="utf-8") as fh:
        assert "OLLAMA_VISION_MODEL=gemma3:4b" in fh.read()


def test_a_commented_out_key_is_not_the_key():
    """A commented line is a warning, not a value.

    `.env` has a dead `AUDIO_AUTO_DETECT` kept commented as a warning that editing
    it changed nothing. Writing over a commented model line would silently
    resurrect a key the owner had deliberately disabled.
    """
    path = _write_env("# OLLAMA_MODEL_PRIMARY=desligado:8b\nOUTRA=1\n")
    assert mc.write_env_file("OLLAMA_MODEL_PRIMARY", "gemma3:4b", path) is True
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "# OLLAMA_MODEL_PRIMARY=desligado:8b" in text
    assert "\nOLLAMA_MODEL_PRIMARY=gemma3:4b" in text


def test_a_read_only_env_reports_failure_instead_of_pretending():
    """The store already holds the value, so this is not fatal -- but it is not
    silent either.

    A `.env` that has stopped tracking the page is precisely how the two sources
    drift apart again, and only the owner can fix that.
    """
    path = _write_env("OLLAMA_MODEL_PRIMARY=antigo:4b\n")
    os.chmod(path, 0o444)
    try:
        assert mc.write_env_file("OLLAMA_MODEL_PRIMARY", "novo:4b", path) is False
    finally:
        os.chmod(path, 0o644)


# --- the runtime actually uses it -----------------------------------------

def test_the_chat_path_resolves_through_the_module_not_off_config():
    """Otherwise this module is decorative.

    `skills/skill_tapo.py` read `config.OLLAMA_VISION_MODEL` directly, frozen at
    import. So the camera would keep using the old model after the page changed
    -- and the camera is the one path a guest can never take, so nothing else
    would have surfaced it until a visitor was described by a deleted model.
    """
    with open(os.path.join(ROOT, "assistant.py"), encoding="utf-8") as fh:
        chat = fh.read()
    assert "resolve(MODEL_PRIMARY)" in chat, (
        "assistant.py deixou de resolver o modelo pelo modulo"
    )
    assert 'getattr(config, "OLLAMA_MODEL_PRIMARY"' not in chat, (
        "assistant.py voltou a ler o modelo directo do config, que e congelado "
        "no import e ignora a pagina"
    )

    with open(os.path.join(ROOT, "skills", "skill_tapo.py"), encoding="utf-8") as fh:
        tapo = fh.read()
    assert "resolve(MODEL_VISION)" in tapo, (
        "skill_tapo.py le o modelo de visao directo do config: a camara ficaria "
        "com o modelo antigo depois de uma mudanca na pagina"
    )
    # Comments are excluded: the comment above the new call NAMES the old
    # attribute in order to explain what it replaced. A grep over raw source
    # cannot tell that from code, so the first version of this test failed on its
    # own explanation.
    tapo_code = "\n".join(
        line for line in tapo.split("\n") if not line.lstrip().startswith("#")
    )
    assert "config.OLLAMA_VISION_MODEL" not in tapo_code, (
        "skill_tapo.py ainda le o modelo de visao de config"
    )


def test_no_removed_model_is_named_as_a_live_fallback():
    """The leftovers were found by reading, not by a test.

    `assistant.py` still had `or "llama3"` on the fallback line after the model
    had been removed from both hosts -- a name that has not existed in any
    registry line since 2024. A stale fallback is a 404 waiting for the one path
    that reaches it, which is the path that only runs when the primary is down.
    """
    removed = ("llama3", "llava", "qwen2.5", "aya-expanse")
    for name in ("assistant.py", "skills/skill_tapo.py"):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            code = "\n".join(
                line for line in fh if not line.lstrip().startswith("#")
            )
        for gone in removed:
            assert f'"{gone}' not in code, (
                f"{name} ainda tem um fallback vivo para {gone!r}"
            )
