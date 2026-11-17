"""Daily Mastodon post: variety, not the same post with different words.

The owner's complaint on 2026-11-15 was concrete: the account had been posting
the same meditation about time, clocks and shadows for weeks. These tests pin
the three mechanisms that replaced that loop: trending subjects from the
instance, a local history of what was already said, and a cliché score that
picks the least repetitive candidate.

No network here: every source is monkeypatched. The real endpoints are public
and were measured on 2026-11-15 (/api/v1/trends/tags and the local timeline).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skills.skill_mastodon as mastodon  # noqa: E402

OLD_POST = (
    "Sob a luz fraca da noite, o relógio parado parece sussurrar segredos "
    "antigos. O tempo, como os animais, carrega consigo um peso de silêncio."
)
NEW_POST = "A máquina de lavar sonha com o mar e acorda com a roupa por dobrar."


def test_the_old_repertoire_scores_as_cliche():
    """tempo + relógio + silêncio: exactly what the owner complained about."""
    assert mastodon._cliche_hits(OLD_POST) >= 3


def test_a_fresh_subject_scores_zero():
    assert mastodon._cliche_hits(NEW_POST) == 0


def test_cliche_matching_ignores_accents_and_case():
    assert mastodon._cliche_hits("O TEMPO e o RELOGIO") == 2


def test_blocklist_keeps_war_and_protests_out():
    assert mastodon._blocked_subject("GazaVerified")
    assert mastodon._blocked_subject("acampada na praça")
    assert not mastodon._blocked_subject("caturday")


def test_tidy_post_flattens_titles_and_blank_lines():
    """A titled, double-spaced draft must become one running paragraph."""
    raw = "Instruções para a máquina invisível:  \n\nCria uma lâmpada  que brilhe.  "
    assert mastodon._tidy_post(raw) == (
        "Instruções para a máquina invisível: Cria uma lâmpada que brilhe."
    )


def test_tidy_post_drops_markdown_markers():
    assert mastodon._tidy_post("## Título\n- um\n- dois") == "Título - um - dois"


def test_tidy_post_leaves_a_clean_post_alone():
    text = "A notícia fala em drones. Eu vejo um gato a seguir uma bola."
    assert mastodon._tidy_post(text) == text


def test_prompt_asks_for_european_portuguese_and_length():
    prompt = mastodon._build_prompt("", [], [], [], "angulo")
    assert "autónomo" in prompt and "autônomo" in prompt
    assert "controlo" in prompt and "controle" in prompt
    assert "quebras de" in prompt
    assert "máximo 500 caracteres" in prompt
    assert "Escolhe UMA das entradas" in prompt
    assert "entrada escolhida" in prompt


def _fake_http(monkeypatch, payloads):
    class _Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *_args, **_kwargs):
            self._payloads = list(payloads)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, *_args, **_kwargs):
            return _Response(self._payloads.pop(0))

    monkeypatch.setattr(mastodon.httpx, "Client", _Client)


def test_trending_tags_drop_blocked_subjects(monkeypatch):
    _fake_http(
        monkeypatch,
        [[{"name": "caturday"}, {"name": "GazaVerified"}, {"name": "vegan"}]],
    )
    assert mastodon._fetch_trending_tags() == ["caturday", "vegan"]


def test_local_subjects_skip_the_bot_and_blocked_text(monkeypatch):
    _fake_http(
        monkeypatch,
        [
            [
                {"account": {"acct": "phantasma"}, "content": "<p>sombra</p>"},
                {"account": {"acct": "seyon"}, "content": "<p>acampada no Porto</p>"},
                {"account": {"acct": "seyon"}, "content": "<p>GTA VI adiado</p>"},
            ]
        ],
    )
    assert mastodon._fetch_local_subjects() == ["GTA VI adiado"]


def test_trend_research_reads_the_tag_timelines(monkeypatch):
    """A tag name is not research: the statuses under it are."""
    _fake_http(
        monkeypatch,
        [
            [
                {
                    "account": {"acct": "ana"},
                    "content": "<p>O meu gato dorme no teclado há três horas</p>",
                },
                {
                    "account": {"acct": "rui"},
                    "content": "<p>Outro gato, outra sesta, o mesmo teclado</p>",
                },
            ],
            [
                {
                    "account": {"acct": "ana"},
                    "content": "<p>Bolo de chocolate sem ovos, testado duas vezes</p>",
                },
            ],
        ],
    )

    research = mastodon._fetch_trend_research(["caturday", "vegan"])

    assert [tag for tag, _ in research] == ["caturday", "vegan"]
    assert "teclado" in research[0][1][0]
    assert "chocolate" in research[1][1][0]


def test_trend_research_skips_the_bot_blocked_and_link_only(monkeypatch):
    _fake_http(
        monkeypatch,
        [
            [
                {
                    "account": {"acct": "phantasma"},
                    "content": "<p>Um gato de vidro atravessa a cidade</p>",
                },
                {"account": {"acct": "seyon"}, "content": "<p>manifestação na praça</p>"},
                {"account": {"acct": "seyon"}, "content": "<p>https://example.com/x</p>"},
                {"account": {"acct": "seyon"}, "content": "<p>bierschinken.net/band/KrashKar</p>"},
                {"account": {"acct": "seyon"}, "content": "<p>curto</p>"},
                {
                    "account": {"acct": "seyon"},
                    "content": "<p>Um gato de bronze dorme sobre o livro de receitas</p>",
                },
            ]
        ],
    )

    research = mastodon._fetch_trend_research(["caturday"])

    assert research == [
        ("caturday", ["Um gato de bronze dorme sobre o livro de receitas"])
    ]


def test_trend_research_survives_a_failing_tag(monkeypatch):
    class _Response:
        status_code = 500

        def json(self):
            return []

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(mastodon.httpx, "Client", _Client)

    assert mastodon._fetch_trend_research(["caturday"]) == []


def test_status_text_drops_links_handles_and_cuts_on_a_word_boundary():
    status = {
        "content": (
            '<p>@ ana@example.social Olha isto: '
            "bierschinken.net/band/KrashKar\u200bma e um gato de bronze</p>"
        )
    }
    text = mastodon._status_text(status, max_chars=40)
    assert "http" not in text and "@" not in text and "bierschinken" not in text
    assert "gato" in text
    assert not text.endswith("bronz")


def test_persona_comes_from_the_admin_setting(monkeypatch):
    import src.settings_store as store

    monkeypatch.setattr(store, "get_persona", lambda: "  VOZ CONFIGURADA  ")
    assert mastodon._persona() == "VOZ CONFIGURADA"


def test_persona_failure_does_not_block_a_post(monkeypatch):
    import src.settings_store as store

    def boom():
        raise RuntimeError("db indisponível")

    monkeypatch.setattr(store, "get_persona", boom)
    assert mastodon._persona() == ""


def test_prompt_carries_research_and_persona():
    prompt = mastodon._build_prompt(
        rag="memoria",
        trends=["caturday"],
        subjects=["GTA VI"],
        recent=[OLD_POST],
        framing="angulo",
        research=[("caturday", ["Um gato de bronze dorme sobre o livro de receitas"])],
        persona="VOZ FRIA E AUSTERA",
    )

    assert "VOZ FRIA E AUSTERA" in prompt
    assert "Um gato de bronze" in prompt
    assert "matéria-prima" in prompt
    assert "não sobre o que se fala" in prompt


def test_prompt_without_research_says_so():
    prompt = mastodon._build_prompt("", [], [], [], "angulo")
    assert "(sem assuntos — parte de uma memória)" in prompt
    assert "(sem persona configurada)" in prompt


def test_overlap_is_high_for_a_reworded_recent_post():
    recent = ["A sombra do gato alonga-se pela parede enquanto o mundo espera."]
    assert mastodon._overlap_with_recent(
        "A sombra do gato alonga-se pela parede enquanto tudo espera.", recent
    ) > 0.5


def test_overlap_is_low_for_an_unrelated_post():
    recent = ["A sombra do gato alonga-se pela parede enquanto o mundo espera."]
    assert mastodon._overlap_with_recent(NEW_POST, recent) < 0.2


def test_prompt_carries_trends_recent_posts_and_the_ban():
    prompt = mastodon._build_prompt(
        rag="memoria",
        trends=["caturday", "gaza"],
        subjects=["GTA VI adiado outra vez"],
        recent=[OLD_POST],
        framing="Parte de um objecto comum.",
    )
    assert "#caturday" in prompt
    assert "GTA VI adiado outra vez" in prompt
    assert OLD_POST in prompt
    assert "Evita as imagens gastas" in prompt and "relógio" in prompt
    assert "ferrugem" in prompt


def test_prompt_without_sources_does_not_emit_empty_headers():
    prompt = mastodon._build_prompt("", [], [], [], "angulo")
    assert "(sem tendências)" in prompt
    assert "(nenhum)" in prompt


def test_recent_posts_roundtrip_uses_the_configured_path(tmp_path, monkeypatch):
    path = tmp_path / "recent.json"
    monkeypatch.setattr(mastodon, "RECENT_POSTS_PATH", str(path))

    mastodon._remember_post(NEW_POST)
    assert mastodon._read_recent_posts() == [NEW_POST]

    mastodon._remember_post(OLD_POST)
    assert mastodon._read_recent_posts()[0] == OLD_POST
    assert len(mastodon._read_recent_posts()) == 2


def test_recent_posts_are_capped(tmp_path, monkeypatch):
    path = tmp_path / "recent.json"
    monkeypatch.setattr(mastodon, "RECENT_POSTS_PATH", str(path))
    for i in range(mastodon.RECENT_POSTS_KEEP + 5):
        mastodon._remember_post(f"post {i}")
    assert len(mastodon._read_recent_posts()) == mastodon.RECENT_POSTS_KEEP


def test_memory_snippet_drops_the_accounts_own_cliches(monkeypatch):
    """The RAG index contains the bot's old posts; they must not prime it."""
    import data_utils

    monkeypatch.setattr(
        data_utils,
        "retrieve_from_rag",
        lambda *_args, **_kwargs: "O tempo e a sombra do relógio\nUm gato dorme na cadeira",
    )

    snippet = mastodon._memory_snippet()
    assert "gato" in snippet
    assert "sombra" not in snippet and "relógio" not in snippet


def _stub_sources(monkeypatch, recent=None):
    monkeypatch.setattr(mastodon, "_memory_snippet", lambda: "memoria")
    monkeypatch.setattr(mastodon, "_fetch_trending_tags", lambda: ["caturday"])
    monkeypatch.setattr(mastodon, "_fetch_local_subjects", lambda: ["GTA VI"])
    monkeypatch.setattr(mastodon, "_fetch_trend_research", lambda _tags: [])
    monkeypatch.setattr(mastodon, "_persona", lambda: "PERSONA FIXA")
    monkeypatch.setattr(mastodon, "_recent_posts", lambda: recent or [])


def test_generation_researches_the_trends_once(monkeypatch):
    """Research is paid for once per post, not once per candidate."""
    _stub_sources(monkeypatch, recent=[OLD_POST])
    monkeypatch.setattr(mastodon, "POST_CANDIDATES", 2)

    researched = []

    def fake_research(tags):
        researched.append(list(tags))
        return [("caturday", ["Um gato de bronze dorme sobre o livro de receitas"])]

    monkeypatch.setattr(mastodon, "_fetch_trend_research", fake_research)

    prompts = []

    def fake_generate(prompt):
        prompts.append(prompt)
        return NEW_POST

    monkeypatch.setattr(mastodon, "_generate_once", fake_generate)

    assert mastodon._generate_reflective_post() == NEW_POST
    assert researched == [["caturday"]]
    assert "gato de bronze" in prompts[0]
    assert "PERSONA FIXA" in prompts[0]


def test_generation_retries_past_a_cliche_candidate(monkeypatch):
    """One cliché candidate must not become the post."""
    _stub_sources(monkeypatch, recent=[OLD_POST])
    monkeypatch.setattr(mastodon, "POST_CANDIDATES", 2)

    texts = iter([OLD_POST, NEW_POST])
    calls = []

    def fake_generate(prompt):
        calls.append(prompt)
        return next(texts)

    monkeypatch.setattr(mastodon, "_generate_once", fake_generate)

    assert mastodon._generate_reflective_post() == NEW_POST
    assert len(calls) == 2


def test_generation_stops_at_the_first_clean_candidate(monkeypatch):
    """A clean first try must not pay for three more LLM calls."""
    _stub_sources(monkeypatch, recent=[OLD_POST])
    monkeypatch.setattr(mastodon, "POST_CANDIDATES", 2)

    calls = []

    def fake_generate(prompt):
        calls.append(prompt)
        return NEW_POST

    monkeypatch.setattr(mastodon, "_generate_once", fake_generate)

    assert mastodon._generate_reflective_post() == NEW_POST
    assert len(calls) == 1


def test_generation_keeps_the_least_cliche_when_all_fail(monkeypatch):
    """Silence is worse than an imperfect post: pick the least bad one."""
    _stub_sources(monkeypatch, recent=[])
    monkeypatch.setattr(mastodon, "POST_CANDIDATES", 1)

    worse = "O tempo e a sombra do relógio. O rio e o silêncio."
    monkeypatch.setattr(mastodon, "_generate_once", lambda _prompt: worse)

    assert mastodon._generate_reflective_post() == worse


def test_generation_refuses_to_repeat_a_recent_post(monkeypatch):
    """An exact repeat (overlap=1.0) must never be published again."""
    duplicate = "A regra da serra é simples: corta-se apenas o que não pode ser regenerado."
    _stub_sources(monkeypatch, recent=[duplicate])
    monkeypatch.setattr(mastodon, "POST_CANDIDATES", 1)

    calls = []

    def fake_generate(_prompt):
        calls.append(1)
        return duplicate

    monkeypatch.setattr(mastodon, "_generate_once", fake_generate)

    assert mastodon._generate_reflective_post() == ""
    # 1 (POST_CANDIDATES) + 2 (base extra) + 2 (duplicate extra) = 5 attempts.
    assert len(calls) == 5


def test_generation_accepts_a_fresh_post_after_duplicates(monkeypatch):
    """A duplicate candidate is discarded when a different one appears."""
    duplicate = "A regra da serra é simples: corta-se apenas o que não pode ser regenerado."
    _stub_sources(monkeypatch, recent=[duplicate])
    monkeypatch.setattr(mastodon, "POST_CANDIDATES", 1)

    texts = iter([duplicate, duplicate, NEW_POST])
    monkeypatch.setattr(mastodon, "_generate_once", lambda _prompt: next(texts))

    assert mastodon._generate_reflective_post() == NEW_POST


def test_generation_returns_empty_when_the_model_gives_nothing(monkeypatch):
    _stub_sources(monkeypatch)
    monkeypatch.setattr(mastodon, "POST_CANDIDATES", 1)
    monkeypatch.setattr(mastodon, "_generate_once", lambda _prompt: "")

    assert mastodon._generate_reflective_post() == ""


def test_publishing_records_the_post(monkeypatch):
    """A post that is not remembered is a post tomorrow will repeat."""
    remembered = []

    class _Response:
        status_code = 200

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(mastodon, "ACCESS_TOKEN", "token")
    monkeypatch.setattr(mastodon.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(mastodon, "_remember_post", remembered.append)

    import asyncio

    assert asyncio.run(mastodon._post_status(NEW_POST)) is True
    assert remembered == [NEW_POST]
