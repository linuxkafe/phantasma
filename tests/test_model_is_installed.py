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
# The text model, measured 2026-10-04 on http://10.0.0.128:11434.
#
# The 2026-10-03 eval ranked gemma3:4b first and agreed with it for a day. That
# eval never asked the question the house actually asks, which is an opinion
# ABOUT a live reading, so it ranked models on the wrong axis. On the real path,
# with the weather skill's output in the prompt:
#
#     qwen3:8b    103 palavras, 49.4s
#     gemma3:4b    57 palavras, 13.3s
#
# against the owner's own reference answer: ~90 words of European Portuguese that
# still quote the measured figures. The owner reversed the decision on that
# evidence and named the split -- qwen3:8b answers, gemma3:4b sees.
#
# qwen3:8b's costs, measured the same day and recorded next to the default in
# config.py so they cannot be lost: it writes Brazilian Portuguese when asked
# about Poe ("Sua obra...", "Em suas páginas"), and it paraphrases readings into
# adjectives instead of quoting them. A prompt rule now requires the figures to
# be cited; that took the weather reading from 0/3 quoted to 3/3. It also once
# answered 18 km/h as "dezasseis", which no prompt rule prevents.
CHOSEN_TEXT = "qwen3:8b"

# The vision model. It has to be one that can see, and it is deliberately NOT the
# model that answers: qwen3:8b has no vision weights.
#
# Verified 2026-10-04, after the split, on BOTH hosts: a solid red 400x300 PNG
# and a green one, one word each. gemma3:4b answered "Vermelho" and "Verde." on
# 10.0.0.128 and on localhost. A model that cannot see leaves the house able to
# hear and blind, and hides the failure -- the assistant keeps answering, so
# nothing looks broken until a guest asks what is in the room.
CHOSEN_VISION = "gemma3:4b"


def test_the_shipped_default_is_the_model_that_was_chosen():
    """The dataclass default, which is what a missing .env falls back to."""
    assert C.LLMConfig.model == CHOSEN_TEXT, (
        f"LLMConfig.model é {C.LLMConfig.model!r} e o modelo medido é {CHOSEN_TEXT!r}. "
        f"Um .env em falta passaria a pedir um modelo que não está instalado."
    )
    assert C.LLMConfig.model_fallback == CHOSEN_TEXT, (
        f"LLMConfig.model_fallback é {C.LLMConfig.model_fallback!r}. O fallback "
        f"deve ser o mesmo modelo: o primário e o secundário correm o mesmo "
        f"código com a mesma persona, e um fallback diferente responderia com "
        f"outra voz sem que nada o anuncie."
    )


def test_text_and_vision_are_not_the_same_model():
    """They stopped being the same name on 2026-10-04, and that is the point.

    Asserted rather than assumed. The two roles shared one model for a day, and
    putting qwen3:8b in the vision slot fails SILENTLY: the camera path returns
    whatever text the model emits and nothing raises. It is a capability check
    wearing a configuration test's clothes.
    """
    assert C.Config.ollama_vision_model != C.LLMConfig.model, (
        f"visão e texto são o mesmo modelo ({C.LLMConfig.model!r}). "
        f"O modelo de visão tem de ser um que veja, e {CHOSEN_TEXT!r} não vê."
    )


def test_the_vision_model_is_the_one_verified_with_images():
    """llava:7b was deleted, so a default naming it is a dead reference.

    Verified before the deletion, not after: gemma3:4b was handed a 64x64 red
    PNG and a green one and answered "Vermelho" and "Verde." respectively. A
    model that cannot see would have left the assistant able to hear and unable
    to look, and the deletion is what would have caused that.

    Re-verified 2026-10-04 on both hosts when vision was separated from text,
    because the invariant outlives the change that prompted it.
    """
    assert C.Config.ollama_vision_model == CHOSEN_VISION, (
        f"ollama_vision_model é {C.Config.ollama_vision_model!r}. llava:7b foi "
        f"removido e o {CHOSEN_VISION} foi verificado com imagens nos dois hosts."
    )


def test_no_module_fallback_names_a_model_that_no_longer_exists():
    """assistant.py falls back to a literal when config has no value.

    Two of them, and they were the last references to the old model outside a
    comment. A stale literal there means the 404 comes from assistant.py rather
    than from the configuration, which points the investigation at the wrong
    file.
    """
    # qwen3:8b is NOT here: it was removed on 2026-10-03 and put back on
    # 2026-10-04 as the text model, so naming it is now correct. The rest are
    # still gone, and a stale literal among them is what this test is for.
    removed = ("llama3.1:8b", "qwen2.5:7b", "llava:7b", "llama3.2:3b", "aya:8b")
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
    assert "/api/tags" in deploy, (
        "deploy.sh não pergunta ao host que modelos tem. É a forma de esta troca "
        "ser errada só se descobrir em conversa: o Ollama responde 404 por "
        "pedido, o serviço arranca, e o /api/health continua a dizer healthy "
        "porque pergunta se o HOST está de pé e não se o modelo que nomeou "
        "existe."
    )
    for key in ("OLLAMA_MODEL_PRIMARY", "OLLAMA_MODEL_FALLBACK", "OLLAMA_VISION_MODEL"):
        assert key in deploy, (
            f"{key} não é verificado no deploy. O modelo de visão é o pior caso: "
            f"a sua ausência não se nota logo, porque o assistente continua a "
            f"ouvir e a responder."
        )
    assert 'cd "$PROD"' in deploy, (
        "a verificação tem de correr a partir de $PROD. config.load_dotenv() é "
        "chamado sem caminho e lê o .env do CWD, que durante o deploy é o repo "
        "de dev -- então verificaria o ficheiro errado e reportaria os defaults "
        "de prod com o .env de dev."
    )

