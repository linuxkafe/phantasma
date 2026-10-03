"""The configured model must be the one that is actually installed.

CLAUDE.md records the rule this test enforces, because the failure is silent:

    "a host value belongs in `.env`, never in a dataclass default. Before
     changing any audio setting, check that the name in `.env` matches the name
     in `config.py`; a mismatch fails open to the default and looks like the
     setting not working."

Model names are host values. On 2026-10-03 the models were swapped to
`gemma3:4b` -- measured as the only candidate that wrote European Portuguese and
answered instead of refusing -- and four places name a model:

    .env                        OLLAMA_MODEL_PRIMARY / _FALLBACK / OLLAMA_VISION_MODEL
    config.py LLMConfig         model, model_fallback
    config.py Config            ollama_vision_model
    assistant.py                getattr fallbacks

They are redundant on purpose: the `.env` wins at runtime, but if it is missing
or a name is misspelled, every one of those fallbacks becomes the thing that
decides. A fallback naming a model that has been deleted does not raise -- it
fails open, and Ollama answers 404 per request while `/api/health` still says
healthy, because the health check asks the host whether it is up and not whether
the model it names exists.

So: the configured name and the installed name are asserted to be the same
thing. This does not talk to Ollama -- it is a configuration test and runs
anywhere. `scripts/deploy.sh` is where the installed side gets checked.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config as C  # noqa: E402

# Measured 2026-10-03 on http://10.0.0.128:11434, six prompts each through the
# real production prompt, search noise attached and detached. European Portuguese
# was the disqualifier: llama3.1:8b wrote "Sua obra", "Seus poemas", five
# Brazilian markers, score 0.0.
CHOSEN = "gemma3:4b"


def test_the_shipped_default_is_the_model_that_was_chosen():
    """The dataclass default, which is what a missing .env falls back to."""
    assert C.LLMConfig.model == CHOSEN, (
        f"LLMConfig.model é {C.LLMConfig.model!r} e o modelo medido é {CHOSEN!r}. "
        f"Um .env em falta passaria a pedir um modelo que não está instalado."
    )
    assert C.LLMConfig.model_fallback == CHOSEN, (
        f"LLMConfig.model_fallback é {C.LLMConfig.model_fallback!r}. O fallback "
        f"deve ser o mesmo modelo: o primário e o secundário correm o mesmo "
        f"código com a mesma persona, e um fallback diferente responderia com "
        f"outra voz sem que nada o anuncie."
    )


def test_the_vision_model_is_the_same_one_and_is_a_vision_model():
    """llava:7b was deleted, so a default naming it is a dead reference.

    Verified before the deletion, not after: gemma3:4b was handed a 64x64 red
    PNG and a green one and answered "Vermelho" and "Verde." respectively. A
    model that cannot see would have left the assistant able to hear and unable
    to look, and the deletion is what would have caused that.
    """
    assert C.Config.ollama_vision_model == CHOSEN, (
        f"ollama_vision_model é {C.Config.ollama_vision_model!r}. llava:7b foi "
        f"removido e o gemma3 foi verificado com imagens."
    )


def test_no_module_fallback_names_a_model_that_no_longer_exists():
    """assistant.py falls back to a literal when config has no value.

    Two of them, and they were the last references to the old model outside a
    comment. A stale literal there means the 404 comes from assistant.py rather
    than from the configuration, which points the investigation at the wrong
    file.
    """
    removed = ("llama3.1:8b", "qwen3:8b", "qwen2.5:7b", "llava:7b", "llama3.2:3b")
    src = open(os.path.join(ROOT, "assistant.py"), encoding="utf-8").read()
    # Strip comments: the docstrings deliberately quote the old names to explain
    # what was measured and what broke.
    code = re.sub(r"#.*", "", src)
    for name in removed:
        assert name not in code, (
            f"assistant.py ainda faz fallback para {name!r}, que foi removido. "
            f"O .env ganha em runtime, mas um .env em falta usaria este literal."
        )


def test_the_deploy_script_refuses_a_model_that_is_not_installed():
    """The installed side of the same claim.

    `.env` can be right while the host disagrees, which is how this would be
    discovered in production otherwise: 404 per request, health still green.
    """
    deploy = open(
        os.path.join(ROOT, "scripts", "deploy.sh"), encoding="utf-8"
    ).read()
    assert "OLLAMA_MODEL_PRIMARY" in deploy and "client.list()" in deploy, (
        "deploy.sh não verifica se o modelo que o .env pede existe no host. É a "
        "forma de esta troca ser errada só se descobrir em conversa: o Ollama "
        "responde 404 por pedido, o serviço arranca, e o /api/health continua a "
        "dizer healthy porque pergunta se o HOST está de pé e não se o modelo "
        "que nomeou existe."
    )
    assert "OLLAMA_VISION_MODEL" in deploy, (
        "a verificação não cobre o modelo de visão, que é um terceiro nome e o "
        "único cuja ausência não se nota logo: o assistente continua a ouvir."
    )

