"""skill_mastodon — Mastodon integration for Phantasma.

Polls the Mastodon instance for mentions of the bot account and replies by
routing the message through the local Phantasma API, exactly like the Discord
skill. Only the configured admin account is answered.

Configuration (via .env):
  MASTODON_INSTANCE_URL  (default: https://linuxkafe.com)
  MASTODON_ACCESS_TOKEN  (access token for the phantasma bot account)
  MASTODON_ADMIN         (default: seyon@s.linuxkafe.com)
  MASTODON_POLL_INTERVAL (default: 30 seconds)

The bot account is expected to be phantasma@s.linuxkafe.com. Create an app in
the Mastodon instance and store its access token in MASTODON_ACCESS_TOKEN.
"""

import asyncio
import html
import json
import logging
import os
import random
import re
import threading
import unicodedata

import httpx

import config

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = []

logger = logging.getLogger("MastodonSkill")

PHANTASMA_API_URL = "http://127.0.0.1:5000/comando"

INSTANCE_URL = getattr(
    config, "MASTODON_INSTANCE_URL", os.getenv("MASTODON_INSTANCE_URL", "https://s.linuxkafe.com")
).rstrip("/")
ACCESS_TOKEN = getattr(
    config, "MASTODON_ACCESS_TOKEN", os.getenv("MASTODON_ACCESS_TOKEN", "")
).strip()
ADMIN_ACCT = getattr(
    config, "MASTODON_ADMIN", os.getenv("MASTODON_ADMIN", "seyon@s.linuxkafe.com")
).strip().lower()
POLL_INTERVAL = int(
    getattr(config, "MASTODON_POLL_INTERVAL", os.getenv("MASTODON_POLL_INTERVAL", "30"))
)
POST_HOUR = int(getattr(config, "MASTODON_POST_HOUR", os.getenv("MASTODON_POST_HOUR", "5")))
_raw = getattr(config, "MASTODON_POST_ENABLED", os.getenv("MASTODON_POST_ENABLED", "true"))
POST_ENABLED = _raw if isinstance(_raw, bool) else str(_raw).lower() in ("1", "true", "yes")
BOT_ACCT = getattr(
    config, "MASTODON_BOT_ACCT", os.getenv("MASTODON_BOT_ACCT", "phantasma@s.linuxkafe.com")
).strip()
_trends_raw = getattr(
    config, "MASTODON_TRENDS_ENABLED", os.getenv("MASTODON_TRENDS_ENABLED", "true")
)
TRENDS_ENABLED = (
    _trends_raw
    if isinstance(_trends_raw, bool)
    else str(_trends_raw).lower() in ("1", "true", "yes")
)
POST_CANDIDATES = max(
    1,
    int(
        getattr(
            config,
            "MASTODON_POST_CANDIDATES",
            os.getenv("MASTODON_POST_CANDIDATES", "2"),
        )
    ),
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECENT_POSTS_PATH = os.getenv(
    "MASTODON_RECENT_POSTS_PATH",
    os.path.join(_ROOT, "data", "mastodon_recent_posts.json"),
)
RECENT_POSTS_KEEP = 20

# The imagery this account has been recycling since it started posting. The
# owner's complaint on 2026-11-15 was not that the posts were bad, it was that
# they were the SAME post: "tempo", "relógio", "sombra", "rio", "silêncio".
# Naming them in the prompt is not enough (the model still drifts back), so the
# generator scores candidates against this list and retries when it lands on it.
CLICHE_WORDS = (
    "tempo",
    "relogio",
    "hora",
    "horas",
    "sombra",
    "sombras",
    "rio",
    "silencio",
)

# Subjects the daily post must not touch. The local timeline is mostly the
# owner's own links, and on 2026-11-15 the first draft was a cynical line about
# a housing protest the owner supports. Topical is the goal; commenting on
# real people, wars or protests is not. Matching is accent-free (see _fold).
SUBJECT_BLOCKLIST = (
    "gaza",
    "guerra",
    "genocidio",
    "holocausto",
    "eleicao",
    "eleicoes",
    "presidente",
    "governo",
    "politica",
    "protesto",
    "manifestacao",
    "acampada",
    "habitacao",
    "morte",
    "mortes",
    "covid",
    "ucrania",
    "israel",
    "palestina",
)


# Memory is only useful as a spark. The old query list literally asked for
# "tempo silencio sombra", so every post was guaranteed to be about time and
# shadows -- the generator was being fed the answer it kept producing.
CONCEPT_QUERIES = (
    "casa",
    "animais",
    "música",
    "viagem",
    "comida",
    "cidade",
    "mar",
    "livros",
    "sonho",
    "trabalho",
    "tecnologia",
    "infância",
    "jardim",
    "chuva",
    "vozes",
    "objectos",
    "gestos",
    "cores",
)

FRAMINGS = (
    "Parte de um objecto comum e descreve-o como se fosse a primeira vez que o vês.",
    "Escreve sobre um animal que ninguém repara, sem o nomear.",
    "Inventa uma cidade pequena e diz uma única coisa que lá acontece.",
    "Parte de uma notícia ou assunto em voga e responde-lhe com uma imagem inesperada.",
    "Escreve como uma instrução para uma máquina que ainda não existe.",
    "Fala de uma memória de infância que nunca aconteceu.",
    "Começa por uma cor e deixa-a transformar-se numa ideia.",
    "Escreve como um aforismo: uma frase que fica, sem explicar a origem.",
    "Descreve um som de uma casa e o que ele faz quando ninguém o ouve.",
    "Parte de um cheiro e conta o que ele traz.",
    "Escreve uma nota de diário de alguém que vive no futuro.",
    "Pega num assunto em voga e olha para ele de lado, sem opinar.",
    "Descreve o que um objecto faz quando ninguém o usa.",
    "Escreve o rótulo de um produto que não existe.",
    "Conta uma coisa que uma criança explicaria mal.",
    "Descreve uma máquina a fazer algo que ninguém lhe pediu.",
)

_COMMAND_TOKEN = os.getenv("PHANTASMA_COMMAND_TOKEN", "").strip()
_COMMAND_HEADERS = {"Authorization": f"Bearer {_COMMAND_TOKEN}"} if _COMMAND_TOKEN else {}

_last_seen_id = None
_last_post_date = None
_processed_ids = set()
_initial_poll_done = False
_running = False


def _headers():
    h = {"Accept": "application/json"}
    if ACCESS_TOKEN:
        h["Authorization"] = f"Bearer {ACCESS_TOKEN}"
    return h


async def _send_to_phantasma(prompt) -> tuple:
    async with httpx.AsyncClient(timeout=600) as http_client:
        try:
            payload = {"prompt": prompt}
            if not _COMMAND_HEADERS:
                return ("Não tenho credencial para falar com a casa.", False)
            response = await http_client.post(
                PHANTASMA_API_URL, json=payload, headers=_COMMAND_HEADERS
            )
            if response.status_code == 401:
                return ("A casa recusou o comando (401).", False)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "..."), True
        except Exception as e:
            return f"Erro de comunicação interna: {e}", False


def _acct_of(status):
    acct = status.get("account", {}) or {}
    return (acct.get("acct") or acct.get("username") or "").lower()


def _strip_mention(text, bot_username):
    # Remove @phantasma mentions from the prompt.
    import re
    text = re.sub(rf"@{re.escape(bot_username)}\b", "", text, flags=re.IGNORECASE)
    return text.strip()


async def _poll_once():
    global _last_seen_id
    if not ACCESS_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            params = {"limit": 20, "types[]": "mention"}
            if _last_seen_id:
                params["since_id"] = _last_seen_id
            resp = await http.get(
                f"{INSTANCE_URL}/api/v1/notifications",
                headers=_headers(),
                params=params,
            )
            if resp.status_code != 200:
                logger.warning("Mastodon notifications HTTP %s", resp.status_code)
                return
            notifs = resp.json()
    except Exception as e:
        logger.warning("Mastodon poll failed: %s", e)
        return

    if not notifs:
        return

    # Update cursor
    ids = [n.get("id") for n in notifs if n.get("id")]
    if ids:
        _last_seen_id = max(ids, key=lambda x: int(x) if str(x).isdigit() else 0)

    # First poll after (re)start: mark everything as seen without replying,
    # so we never re-answer old mentions.
    global _initial_poll_done, _processed_ids
    if not _initial_poll_done:
        _processed_ids.update(ids)
        _initial_poll_done = True
        print(
            f"[Mastodon Skill] initial catch-up: marked {len(ids)} notifications as seen",
            flush=True,
        )
        return

    for n in notifs:
        if n.get("type") != "mention":
            continue
        nid = n.get("id")
        if nid in _processed_ids:
            continue
        status = n.get("status") or {}
        acct = _acct_of(status)
        # Only answer the configured admin account. Mastodon returns the
        # local username ("seyon") for same-instance accounts, so compare on
        # the local part as well.
        admin_local = ADMIN_ACCT.split("@")[0].lower()
        if acct != ADMIN_ACCT and acct != admin_local:
            _processed_ids.add(nid)
            continue
        _processed_ids.add(nid)
        content = status.get("content", "") or ""
        # Strip HTML tags crudely
        import re
        content = re.sub(r"<[^>]+>", " ", content)
        content = " ".join(content.split())
        bot_username = BOT_ACCT.split("@")[0]
        prompt = _strip_mention(content, bot_username)
        if not prompt:
            continue

        status_id = status.get("id")
        in_reply_to = status_id

        response_text, delivered = await _send_to_phantasma(prompt)

        # Truncate for Mastodon (500 char limit per status is safe)
        if len(response_text) > 450:
            response_text = response_text[:440] + "..."

        # Reply visibility follows the original status: public mentions get
        # public replies, direct (DM) mentions get direct replies.
        orig_visibility = status.get("visibility") or "public"
        if orig_visibility not in ("public", "unlisted", "private", "direct"):
            orig_visibility = "public"

        try:
            async with httpx.AsyncClient(timeout=30) as http:
                await http.post(
                    f"{INSTANCE_URL}/api/v1/statuses",
                    headers=_headers(),
                    json={
                        "status": f"@{acct.split('@')[0]} {response_text}",
                        "in_reply_to_id": in_reply_to,
                        "visibility": orig_visibility,
                    },
                )
        except Exception as e:
            logger.warning("Mastodon reply failed: %s", e)


async def _post_status(text: str, visibility: str = "public") -> bool:
    """Post a status to Mastodon. Returns True on success."""
    if not ACCESS_TOKEN or not text:
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{INSTANCE_URL}/api/v1/statuses",
                headers=_headers(),
                json={"status": text, "visibility": visibility},
            )
            if resp.status_code in (200, 201):
                print(f"[Mastodon Skill] posted: {text[:60]}", flush=True)
                _remember_post(text)
                return True
            print(
                f"[Mastodon Skill] post failed HTTP {resp.status_code}: {resp.text[:100]}",
                flush=True,
            )
            return False
    except Exception as e:
        print(f"[Mastodon Skill] post error: {e}", flush=True)
        return False


def _fold(text: str) -> str:
    """Lowercase, accent-free form for word matching (não é para exibir)."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _strip_html(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def _blocked_subject(text: str) -> bool:
    """True for wars, protests, party politics and deaths -- see blocklist.

    Substring, not word-boundary: the instance exposes tags like
    "GazaVerified", and `\bgaza\b` let that one through.
    """
    folded = _fold(text)
    return any(word in folded for word in SUBJECT_BLOCKLIST)


# --- recent posts: the model has to know what it already said --------------

def _read_recent_posts() -> list:
    try:
        with open(RECENT_POSTS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [str(p) for p in data if str(p).strip()]
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("Mastodon recent-posts read failed: %s", e)
    return []


def _write_recent_posts(posts) -> None:
    try:
        os.makedirs(os.path.dirname(RECENT_POSTS_PATH), exist_ok=True)
        tmp = RECENT_POSTS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(posts[:RECENT_POSTS_KEEP], fh, ensure_ascii=False, indent=1)
        os.replace(tmp, RECENT_POSTS_PATH)
    except Exception as e:
        logger.warning("Mastodon recent-posts write failed: %s", e)


def _backfill_recent_posts(limit: int = 12) -> list:
    """Seed the local history from the bot's own statuses on first run.

    Without this the first post after the change has no idea what the last
    thirty said, and repeats them. Best-effort: any failure is just an empty
    history, never a reason not to post.
    """
    if not ACCESS_TOKEN:
        return []
    try:
        bot_local = BOT_ACCT.split("@")[0]
        with httpx.Client(timeout=15, headers=_headers()) as client:
            resp = client.get(
                f"{INSTANCE_URL}/api/v1/accounts/lookup", params={"acct": bot_local}
            )
            if resp.status_code != 200:
                return []
            account_id = resp.json().get("id")
            if not account_id:
                return []
            resp = client.get(
                f"{INSTANCE_URL}/api/v1/accounts/{account_id}/statuses",
                params={
                    "limit": limit,
                    "exclude_replies": "true",
                    "exclude_reblogs": "true",
                },
            )
            if resp.status_code != 200:
                return []
            posts = []
            for status in resp.json():
                text = _strip_html(status.get("content") or "")
                if text:
                    posts.append(text)
            return posts
    except Exception as e:
        logger.warning("Mastodon recent-post backfill failed: %s", e)
        return []


def _recent_posts(limit: int = 8) -> list:
    posts = _read_recent_posts()
    if not posts:
        posts = _backfill_recent_posts()
        if posts:
            _write_recent_posts(posts)
    return posts[:limit]


def _remember_post(text: str) -> None:
    """Record a published post so tomorrow's prompt can refuse to repeat it."""
    if not text:
        return
    posts = [text] + [p for p in _read_recent_posts() if p != text]
    _write_recent_posts(posts)


# --- what the instance is talking about right now ---------------------------

def _fetch_trending_tags(limit: int = 8) -> list:
    """Trending hashtags, without the '#'. Public endpoint, no token needed."""
    if not TRENDS_ENABLED:
        return []
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{INSTANCE_URL}/api/v1/trends/tags", params={"limit": limit}
            )
            if resp.status_code != 200:
                logger.warning("Mastodon trends HTTP %s", resp.status_code)
                return []
            tags = []
            for trend in resp.json():
                name = (trend.get("name") or "").strip()
                if name and not _blocked_subject(name):
                    tags.append(name)
            return tags
    except Exception as e:
        logger.warning("Mastodon trends failed: %s", e)
        return []


def _fetch_local_subjects(limit: int = 5) -> list:
    """A few subjects from the local timeline, bot posts excluded.

    Hashtags say "what" but not "about what"; a small instance's local
    timeline is where the actual subjects live. Truncated hard so the model
    gets a spark, not an article to paraphrase.
    """
    if not TRENDS_ENABLED:
        return []
    try:
        bot_local = BOT_ACCT.split("@")[0].lower()
        with httpx.Client(timeout=10, headers=_headers()) as client:
            resp = client.get(
                f"{INSTANCE_URL}/api/v1/timelines/public",
                params={"local": "true", "limit": limit * 4},
            )
            if resp.status_code != 200:
                return []
            subjects = []
            for status in resp.json():
                if _acct_of(status).split("@")[0] == bot_local:
                    continue
                text = _strip_html(status.get("content") or "")
                text = re.sub(r"https?://\S+", "", text).strip()
                text = re.sub(r"#\s+", "#", text).strip()
                if not text or _blocked_subject(text):
                    continue
                subjects.append(text[:140])
                if len(subjects) >= limit:
                    break
            return subjects
    except Exception as e:
        logger.warning("Mastodon local timeline failed: %s", e)
        return []


# --- prompt + generation ----------------------------------------------------

def _memory_snippet() -> str:
    """Sample concept clusters from memory, minus the account's own clichés.

    The RAG index contains the bot's previous posts, so "memória" kept handing
    the model the very imagery it was being told to stop using (tempo, sombra,
    rio). Filtering those lines is what actually breaks the loop -- the ban in
    the prompt only works if the prompt is not already full of the banned words.
    """
    try:
        import sys

        sys.path.insert(0, _ROOT)
        from data_utils import retrieve_from_rag

        queries = random.sample(CONCEPT_QUERIES, min(4, len(CONCEPT_QUERIES)))
        chunks = []
        for query in queries:
            result = retrieve_from_rag(query, max_results=6)
            if result:
                chunks.append(result)
        rag = "\n".join(chunks)
        lines = list(dict.fromkeys(ln for ln in rag.splitlines() if ln.strip()))
        lines = [ln for ln in lines if _cliche_hits(ln) == 0]
        random.shuffle(lines)
        rag = "\n".join(lines[:10])
    except Exception:
        rag = ""
    return rag[:1200] if rag else ""


def _cliche_hits(text: str) -> int:
    folded = _fold(text)
    return sum(1 for word in CLICHE_WORDS if re.search(rf"\b{re.escape(word)}\b", folded))


def _overlap_with_recent(text: str, recent) -> float:
    """Largest share of this post's words already used in one recent post."""
    words = set(re.findall(r"\w{4,}", _fold(text)))
    if not words:
        return 0.0
    worst = 0.0
    for other in recent:
        other_words = set(re.findall(r"\w{4,}", _fold(other)))
        if other_words:
            worst = max(worst, len(words & other_words) / len(words))
    return worst


def _build_prompt(rag: str, trends, subjects, recent, framing: str) -> str:
    trend_line = ", ".join(f"#{t}" for t in trends) if trends else "(sem tendências)"
    subject_block = "\n".join(f"- {s}" for s in subjects) if subjects else "(nada)"
    recent_block = "\n".join(f"- {p}" for p in recent[:8]) if recent else "(nenhum)"
    memory_block = rag if rag else "(sem memórias relevantes)"

    return (
        "Escreves como o Phantasma: voz própria, imaginativa, em português "
        "europeu. Escreve um post curto (máximo 280 caracteres) para o "
        "Mastodon.\n\n"
        "REGRAS:\n"
        "- NÃO cites nem transcrevas as memórias, as tendências ou os posts "
        "recentes. São faísca, não texto.\n"
        "- PROIBIDO o campo semântico do tempo que passa: tempo, relógio, "
        "horas, sombra, rio, silêncio. Já foram usados até à exaustão.\n"
        "- NÃO repitas temas, imagens ou frases dos teus posts recentes.\n"
        "- Escolhe UM assunto concreto (um objecto, um animal, uma cidade, "
        "uma notícia, uma ideia) e surpreende.\n"
        "- Nunca comentes pessoas reais, protestos, guerras, tragédias ou "
        "política partidária; se um assunto for desse tipo, ignora-o.\n"
        "- Sem hashtags excessivas (máximo 2). Sem markdown. Sem aspas.\n"
        "- Uma só frase ou duas. Sem título, sem listas, sem quebras de "
        "linha.\n"
        "- Português de Portugal, nunca do Brasil: autónomo (não autônomo), "
        "ecrã (não tela), telemóvel (não celular), a superfície (não sua "
        "superfície).\n\n"
        f"Ângulo de hoje: {framing}\n\n"
        f"Assuntos em voga no Mastodon (inspiração opcional): {trend_line}\n\n"
        f"Assuntos na timeline local (inspiração opcional):\n{subject_block}\n\n"
        f"Posts recentes teus (NÃO repetir):\n{recent_block}\n\n"
        f"Memórias (inspiração, não citação):\n{memory_block}\n\n"
        "Post:"
    )


def _generate_once(prompt: str) -> str:
    try:
        import ollama

        host = os.getenv("OLLAMA_HOST_FALLBACK", "http://localhost:11434")
        client = ollama.Client(host=host, timeout=300)
        model = os.getenv(
            "OLLAMA_MODEL_FALLBACK",
            "hf.co/mradermacher/Huihui-Qwen3-8B-abliterated-v2-GGUF:Q4_K_M",
        )
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt + " /no_think"}],
            think=False,
            options={
                "num_predict": 90,
                "num_ctx": 4096,
                "temperature": 0.95,
                "top_p": 0.95,
                "repeat_penalty": 1.15,
            },
        )
        text = (response.message.content or "").strip()
        _close = "<" + "/think" + ">"
        if _close in text:
            text = text.split(_close)[-1].strip()
        text = text.strip('"').strip("'").strip()
        if not text:
            return ""
        text = _tidy_post(text)
        if not text:
            return ""
        if len(text) > 280:
            text = text[:277] + "..."
        return text
    except Exception as e:
        print(f"[Mastodon Skill] LLM error for reflective post: {e}", flush=True)
        return ""


def _tidy_post(text: str) -> str:
    """One paragraph, no markdown leftovers, no stray spaces.

    The model liked to answer the "instructions" framing with a title line and
    a blank line ("Instruções para a máquina invisível:\\n\\n..."). Mastodon
    renders that as a broken post, so collapse it to running text.
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"^\s*[#*\->]+\s*", "", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


def _generate_reflective_post() -> str:
    """Generate a post that is not yesterday's post.

    Inspiration comes from three independent places -- memory, the instance's
    trending tags, and the local timeline -- and every candidate is scored
    against the account's recent posts and against the imagery it has been
    recycling. The first candidate with no clichés wins; otherwise the least
    repetitive one, after up to two extra attempts. Two extra attempts matter:
    measured on 2026-11-15, a single candidate still landed on "sombra/tempo"
    about one time in two.
    """
    rag = _memory_snippet()
    trends = _fetch_trending_tags()
    subjects = _fetch_local_subjects()
    recent = _recent_posts()

    def score(text):
        return (_cliche_hits(text), _overlap_with_recent(text, recent))

    best = ""
    best_score = None
    attempts = 0
    max_attempts = POST_CANDIDATES + 2
    while attempts < max_attempts:
        attempts += 1
        framing = random.choice(FRAMINGS)
        prompt = _build_prompt(rag, trends, subjects, recent, framing)
        text = _generate_once(prompt)
        if not text:
            continue
        candidate_score = score(text)
        if best_score is None or candidate_score < best_score:
            best, best_score = text, candidate_score
        # Clean and unrelated enough: stop paying for more inference.
        if best_score[0] == 0 and best_score[1] <= 0.45:
            break

    if not best:
        return ""

    print(
        f"[Mastodon Skill] post candidate chosen (clichés={best_score[0]}, "
        f"overlap={best_score[1]:.2f}, attempts={attempts})",
        flush=True,
    )
    return best


async def post_reflective_now() -> bool:
    """Generate and post a reflective post immediately (for testing)."""
    print("[Mastodon Skill] Generating reflective post...", flush=True)
    # Generation is a blocking LLM call (slow on CPU); keep the loop free.
    text = await asyncio.to_thread(_generate_reflective_post)
    if not text:
        print("[Mastodon Skill] No post generated.", flush=True)
        return False
    print(f"[Mastodon Skill] Posting: {text[:80]}...", flush=True)
    return await _post_status(text)


async def _loop():
    global _running
    _running = True
    print(
        f"[Mastodon Skill] Ligado como {BOT_ACCT}, polling {INSTANCE_URL} (admin={ADMIN_ACCT})",
        flush=True,
    )
    while _running:
        try:
            await _poll_once()
            print("[Mastodon Skill] polled OK", flush=True)
        except Exception as e:
            print(f"[Mastodon Skill] poll error: {e}", flush=True)

        # Scheduled reflective post
        if POST_ENABLED:
            try:
                import datetime as _dt
                now = _dt.datetime.now()
                global _last_post_date
                if now.hour == POST_HOUR and _last_post_date != now.date():
                    print("[Mastodon Skill] Time for scheduled post", flush=True)
                    # Blocking LLM call off the event loop: polling and mention
                    # replies must keep working while the post is generated.
                    text = await asyncio.to_thread(_generate_reflective_post)
                    if text:
                        ok = await _post_status(text)
                        if ok:
                            _last_post_date = now.date()
            except Exception as e:
                print(f"[Mastodon Skill] scheduled post error: {e}", flush=True)

        await asyncio.sleep(POLL_INTERVAL)


def init_skill_daemon():
    """Start the Mastodon polling daemon in a background thread."""
    if not ACCESS_TOKEN:
        logger.warning("MASTODON_ACCESS_TOKEN not set; Mastodon skill disabled.")
        return
    t = threading.Thread(target=lambda: asyncio.run(_loop()), daemon=True, name="mastodon-daemon")
    t.start()


def handle(user_prompt_lower, user_prompt_full):
    # Not triggered by voice/text locally; the daemon handles everything.
    return None
