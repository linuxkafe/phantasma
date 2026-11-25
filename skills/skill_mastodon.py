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

# English is the other language on the timeline. Word-boundary matching here
# because "war" as a substring would also hit "software" and "warning".
SUBJECT_BLOCKLIST_EN = (
    "war",
    "wars",
    "ukraine",
    "israel",
    "palestine",
    "genocide",
    "nazi",
    "hitler",
    "election",
    "elections",
    "president",
    "government",
    "protest",
    "missile",
    "missiles",
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

# Angles, not subjects. The previous list was a set of subjects ("descreve um
# som de uma casa", "inventa uma cidade") competing with the research for the
# model's attention -- and winning: measured on 2026-11-16, four candidates in
# a row ignored the researched trends and wrote the angle instead. An angle
# has to be applicable to any subject.
FRAMINGS = (
    "Olha para o assunto como se fosse a primeira vez que o vês.",
    "Descreve o assunto pelo som que ele faz.",
    "Descreve o assunto pelo cheiro que deixa.",
    "Conta o assunto como uma instrução para uma máquina que ainda não existe.",
    "Fala do assunto através de um objecto que o acompanha.",
    "Diz uma única coisa que acontece por causa do assunto.",
    "Olha para o assunto de lado, sem o nomear.",
    "Começa por uma cor e chega ao assunto por ela.",
    "Escreve um aforismo sobre o assunto, sem explicar a origem.",
    "Imagina o assunto daqui a cem anos.",
    "Conta o assunto como uma criança o explicaria mal.",
    "Descreve o assunto através do que ele destrói.",
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


async def _poll_once() -> bool:
    """One notification poll. False means the poll itself failed."""
    global _last_seen_id
    if not ACCESS_TOKEN:
        return False
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
                return False
            notifs = resp.json()
    except Exception as e:
        # %r, not %s: an httpx timeout stringifies to "" and the log line
        # read "Mastodon poll failed:" with no cause on 2026-11-18.
        logger.warning("Mastodon poll failed: %r", e)
        return False

    if not notifs:
        return True

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
        return True

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

    return True


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


_LINK_TOKEN = re.compile(
    r"^(?:https?://\S+|www\.\S+|[\w.-]+\.(?:com|net|org|pt|de|io|ly|be|info|me|co|uk"
    r"|fr|es|it|nl|eu|tv|app|dev)(?:/\S*)?)$",
    re.IGNORECASE,
)


def _strip_html(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def _blocked_subject(text: str) -> bool:
    """True for wars, protests, party politics and deaths -- see blocklist.

    Substring, not word-boundary, for the Portuguese list: the instance
    exposes tags like "GazaVerified", and `\bgaza\b` let that one through.
    English needs boundaries (2026-11-18: "Second world war started after the
    meeting of the two biggest authoritarian figures" was offered to the model
    as a subject because "guerra" does not match "war").
    """
    folded = _fold(text)
    if any(word in folded for word in SUBJECT_BLOCKLIST):
        return True
    return any(
        re.search(rf"\b{re.escape(word)}\b", folded) for word in SUBJECT_BLOCKLIST_EN
    )


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
                if status.get("in_reply_to_id"):
                    # A reply is context for someone else's thread, not a
                    # standalone subject; one of them was a comment on the
                    # bot's own previous post and fed the same theme back in.
                    continue
                text = _status_text(status, max_chars=140)
                if not text or _blocked_subject(text):
                    continue
                if text[:3].upper() == "RE:":
                    # The owner prefixes reactions with "RE:" (one pointed at
                    # the bot's own last post), and a reaction is not a subject.
                    continue
                subjects.append(text)
                if len(subjects) >= limit:
                    break
            return subjects
    except Exception as e:
        logger.warning("Mastodon local timeline failed: %s", e)
        return []


def _status_text(status, max_chars: int = 160) -> str:
    """Readable text of a status: no HTML, no links, no handles.

    Mastodon inserts zero-width spaces inside links, so a link can arrive as
    "bierschinken.net/band/KrashKar\\u200bma" and survive a naive
    ``https?://`` strip. Tokens that look like links are dropped whole.

    Truncation prefers a sentence end. Cutting at the last space instead left
    the model with "…an infinite eternal monolithic thread. :p (Can't remember
    why I had goose and", and it tried to complete the broken clause: the post
    came out with "infinitos filtos", a word that does not exist.
    """
    text = _strip_html(status.get("content") or "")
    text = text.replace("\u200b", " ").replace("\ufeff", " ")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"@\w+(@[\w.-]+)?", " ", text)
    text = text.replace("@", " ")
    tokens = [token for token in text.split() if not _LINK_TOKEN.match(token)]
    text = re.sub(r"#\s+", "#", " ".join(tokens))
    text = " ".join(text.split())
    if len(text) > max_chars:
        head = text[:max_chars]
        end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
        if end >= max_chars // 3:
            return head[: end + 1].strip()
        text = head.rsplit(" ", 1)[0]
    return text.strip()


def _fetch_trend_research(tags, per_tag: int = 2, max_tags: int = 3) -> list:
    """What people are actually saying under the trending tags.

    A tag name is not research. "#caturday" gives the model nothing to write
    from; the statuses under it do -- that is the difference between a post
    that mentions a trend and a post that grew out of one. Returns
    ``[(tag, [snippet, ...]), ...]``, filtered like the local timeline:
    the bot's own posts, the blocklist, one-word statuses and link-only
    statuses never reach the prompt.
    """
    if not TRENDS_ENABLED or not tags:
        return []
    bot_local = BOT_ACCT.split("@")[0].lower()
    research = []
    seen = set()
    try:
        with httpx.Client(timeout=10, headers=_headers()) as client:
            for tag in list(tags)[:max_tags]:
                try:
                    resp = client.get(
                        f"{INSTANCE_URL}/api/v1/timelines/tag/{tag}",
                        params={"limit": 15},
                    )
                except Exception as e:
                    logger.warning("Mastodon tag %s failed: %s", tag, e)
                    continue
                if resp.status_code != 200:
                    logger.warning("Mastodon tag %s HTTP %s", tag, resp.status_code)
                    continue
                snippets = []
                for status in resp.json():
                    if _acct_of(status).split("@")[0] == bot_local:
                        continue
                    text = _status_text(status)
                    if len(text) < 25 or _blocked_subject(text):
                        continue
                    key = text[:60].lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    snippets.append(text)
                    if len(snippets) >= per_tag:
                        break
                if snippets:
                    research.append((tag, snippets))
    except Exception as e:
        logger.warning("Mastodon trend research failed: %s", e)
    return research


# --- prompt + generation ----------------------------------------------------

def _looks_structured(line: str) -> bool:
    """JSON lines from the RAG index are data, not prose to write from.

    On 2026-11-18 the post ended with "#éticaanimal #indústria musical #opinião
    pública": those were the "tags" of a JSON chunk, copied verbatim as
    hashtags. A chunk that is a dict is not a memory the model can use.
    """
    stripped = line.strip()
    return (
        stripped.startswith(("{", "["))
        or '"tags":' in stripped
        or '"facts":' in stripped
    )


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
        lines = [
            ln
            for ln in lines
            if _cliche_hits(ln) == 0 and not _looks_structured(ln)
        ]
        random.shuffle(lines)
        rag = "\n".join(lines[:6])
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


# A shared run of this many words is a copied sentence, not a shared subject.
# On 2026-11-18 the daily post reused "não há rastro, apenas uma transição
# invisível" from the previous day: 13 shared four-word shingles, but the
# word-overlap metric scored it 0.75 -- under the 0.80 duplicate bar, so it
# shipped. Bag-of-words cannot see a copied phrase; shingles can.
DUPLICATE_SHINGLE = 4

# Share of a post's words that may already sit in one recent post. The highest
# overlap between two genuinely different posts in the history is 0.47; the
# 2026-11-18 paraphrase scored 0.75. The bar sits between them.
DUPLICATE_OVERLAP = 0.65

_PHRASE_STOPWORDS = frozenset(
    "para pela pelo como mais menos entre sobre depois antes ainda quando onde "
    "porque assim cada todo toda todos todas outro outra mesmo mesma esta este "
    "estes estas isso isto aquele aquela seu sua seus suas meu minha nao".split()
)


def _shingles(text: str, n: int = DUPLICATE_SHINGLE) -> set:
    """Word n-grams of a post, one sentence at a time.

    Shingles never cross a sentence boundary. Built on the whole text, they do:
    "...a carne que nunca foi. Nenhum eco sobra..." shares the four words
    "que nunca foi nenhum" with an unrelated post and gets flagged as copied.
    A copied phrase is copied inside a sentence.
    """
    grams = set()
    for sentence in re.split(r"[.!?;:…—\n]+", _fold(text)):
        words = re.findall(r"\w+", sentence)
        for i in range(len(words) - n + 1):
            gram = words[i : i + n]
            if all(word in _PHRASE_STOPWORDS for word in gram):
                continue
            grams.add(" ".join(gram))
    return grams


def _copied_phrase(text: str, recent) -> str:
    """A phrase copied verbatim from a recent post ('' when there is none)."""
    mine = _shingles(text)
    if not mine:
        return ""
    for other in recent:
        shared = mine & _shingles(other)
        if shared:
            return max(shared, key=len)
    return ""


def _tag_key(raw: str) -> str:
    """Comparable form of a hashtag: folded, punctuation-free, no #."""
    tag = re.sub(r"[^\w]", "", _fold(raw or ""), flags=re.UNICODE)
    return re.sub(r"_+", "_", tag).strip("_")


def _used_theme_tags(recent) -> set:
    """Hashtags the recent posts already carry: those themes have been used.

    Every post ends with the hashtags of the themes that fed it, so the
    history says which material has already been spent. On 2026-11-18 the
    only trend was #vegan, the same one that fed the previous post, and the
    model wrote the same paragraph again.
    """
    used = set()
    for post in recent:
        used.update(re.findall(r"#([\w]+)", _fold(post)))
    return used


def _is_stale_subject(subject: str, recent) -> bool:
    """A timeline subject that echoes a recent post has no new material in it."""
    words = set(re.findall(r"\w{4,}", _fold(subject)))
    if not words:
        return True
    for post in recent:
        other = set(re.findall(r"\w{4,}", _fold(post)))
        if other and len(words & other) / len(words) >= 0.5:
            return True
    return False


def _has_prose(text: str, min_words: int = 3) -> bool:
    """True when a menu entry has words of its own, not just tags or a link.

    On 2026-11-18 the menu offered "https:// #ParkerHouseRolls #Vegan" -- no
    text at all -- and the model, told to take a concrete image from it,
    invented one: a post about veal ("A vitela está quase acabada"). A tag is
    a label, not a subject. A slug counts as one token, not as prose.
    """
    stripped = re.sub(r"https?://\S*", " ", text or "")
    stripped = re.sub(r"#[\w]+", " ", stripped)
    tokens = [
        token
        for token in stripped.split()
        if re.search(r"[^\W\d_]{2,}", _fold(token), flags=re.UNICODE)
    ]
    if len(tokens) < 2:
        return False
    words = re.findall(r"[^\W\d_]{2,}", _fold(stripped), flags=re.UNICODE)
    return len(words) >= min_words


def _persona() -> str:
    """The persona configured in /admin, so the posts follow the owner's voice.

    The skill used to hardcode "voz própria, imaginativa", which is how the
    account ended up with a voice the owner never chose. Read per post, like
    the assistant does, so an edit in /admin reaches tomorrow's post.
    """
    try:
        import sys

        sys.path.insert(0, _ROOT)
        from src.settings_store import get_persona

        text = (get_persona() or "").strip()
    except Exception as e:
        logger.warning("Mastodon persona read failed: %s", e)
        text = ""
    return text[:3000]


def _persona_stance(persona: str) -> str:
    """The ethical position stated by the persona, in instruction form.

    The owner asked (2026-11-18) that the posts take an abolitionist
    position, not merely avoid contradicting one. The persona already says
    "Express vegan and abolitionist ethics as self-evident realities, not as
    advice or recommendations", so the stance is derived from the persona
    instead of hardcoded: a persona without that clause gets no stance and
    the posts go back to being subject-only.
    """
    folded = _fold(persona)
    if "vegan" not in folded and "abolicion" not in folded:
        return ""
    return (
        "Assume a ética abolicionista animal como facto consumado: a exploração "
        "animal não é uma opinião a debater. O post afirma esta posição uma vez, "
        "ligada ao assunto escolhido, no tom frio da persona — nunca como sermão, "
        "conselho, apelo ou moral. Não repitas a formulação de ontem."
    )


def _subject_menu(research, subjects) -> list:
    """The material available today, one entry per usable source.

    An entry is a snippet from a trending tag's timeline, or a subject from the
    local timeline. Entries without prose are not subjects (see _has_prose).
    """
    menu = []
    for tag, snippets in research or []:
        for snippet in snippets:
            if _has_prose(snippet):
                menu.append(f"[#{tag}] {snippet}")
    for subject in subjects or []:
        if _has_prose(subject):
            menu.append(f"[timeline local] {subject}")
    return menu


def _novelty(entry: str, recent) -> float:
    """How much of this entry is absent from the recent posts (0.0 .. 1.0)."""
    words = set(re.findall(r"\w{4,}", _fold(entry)))
    if not words:
        return 0.0
    worst = 0.0
    for post in recent or []:
        other = set(re.findall(r"\w{4,}", _fold(post)))
        if other:
            worst = max(worst, len(words & other) / len(words))
    return 1.0 - worst


def _rank_subjects(menu, recent) -> list:
    """Subjects that can carry the position first, then most novel first.

    The generator used to hand the model a numbered menu of up to eight
    entries and ask it to choose one. On an 8B model that is an invitation to
    fall back on whatever it read last, and the menu entries it picked were
    the ones closest to yesterday's post. Choosing is the harness's job now.

    Ordering by stance first is what makes the owner's choice ("the position
    when the subject allows it") happen as often as the timeline allows: if
    there is animal or food material today, it is what gets written about.
    """
    return sorted(
        menu, key=lambda entry: (not _carries_stance(entry), -_novelty(entry, recent))
    )


# Stems that make a subject able to carry the position (see _carries_stance).
# "animal" covers animais/animal; "aliment" covers alimentação/alimentar.
STANCE_STEMS = (
    "animal",
    "carne",
    "vitela",
    "vegan",
    "vegetar",
    "aliment",
    "comida",
    "cozinha",
    "receita",
    "leite",
    "queijo",
    "pecuar",
    "matadouro",
    "curral",
    "tourada",
    "zoologic",
    "aquario",
    "laborator",
    "farmac",
    "cosmetic",
    "especismo",
    "abolicion",
    "senciente",
)

# Short words need both boundaries: "vaca" is also the start of "vacation".
STANCE_WORDS = (
    "ovo",
    "ovos",
    "peixe",
    "peixes",
    "pesca",
    "gado",
    "porco",
    "vaca",
    "galinha",
    "couro",
    "caca",
    "circo",
)


def _carries_stance(entry: str) -> bool:
    """True when the subject can carry the abolitionist position.

    Owner's decision, 2026-11-18: the position is asserted when the subject
    allows it, not forced onto every subject. Requiring it unconditionally
    produced posts that tied animal exploitation to a Mastodon filter tip by
    main force, and refusing to post when the tie failed; a daily account that
    goes silent is a worse outcome than a post that only describes its subject.

    The test is lexical on purpose: the harness decides, so the prompt and the
    verification agree and the model is never asked to judge its own subject.
    """
    folded = _fold(entry)
    if any(re.search(rf"\b{stem}", folded) for stem in STANCE_STEMS):
        return True
    return any(re.search(rf"\b{word}\b", folded) for word in STANCE_WORDS)


def _asserts_stance(text: str) -> bool:
    """True when a post states the position, not merely avoids contradicting it.

    A keyword floor, not a proof: it catches the 2026-11-18 post ("os patos
    silenciam antes de mergulhar"), which mentioned no animal exploitation at
    all while the persona requires the opposite to be asserted.
    """
    folded = _fold(text)
    return any(
        re.search(rf"\b{re.escape(marker)}", folded)
        for marker in (
            "animal",
            "animais",
            "senciente",
            "explora",
            "aboli",
            "abolicion",
            "carne",
            "gado",
            "matadouro",
            "curral",
            "especismo",
        )
    )


def _build_prompt(
    rag: str,
    subject: str,
    framing: str,
    persona: str = "",
    stance: str = "",
) -> str:
    memory_block = rag if rag else "(sem memórias relevantes)"
    persona_block = persona.strip() or "(sem persona configurada)"
    stance_block = (
        f"POSIÇÃO (da persona, obrigatória em cada post):\n{stance}\n\n" if stance else ""
    )
    subject_block = subject or "(sem assunto pesquisado — parte de uma memória)"

    # The position used to be one rule among ten, and the model ignored it
    # (2026-11-18: a post about Mastodon filters with no position at all). It
    # is now half of the task, stated as a required part rather than a ban.
    if stance:
        task = (
            "TAREFA: escreve um post para o Mastodon (máximo 500 caracteres) com "
            "duas partes: (1) uma imagem concreta tirada do ASSUNTO indicado no "
            "fim deste prompt — um gesto, um objecto, um lugar, um som, um "
            "material; (2) uma frase que afirma a POSIÇÃO como facto consumado, "
            "ligada a essa imagem. A VOZ vem da persona.\n\n"
        )
        position_rule = (
            "- As duas partes são obrigatórias: a POSIÇÃO não substitui o "
            "assunto e o assunto não substitui a POSIÇÃO. Sem sermão, sem "
            "conselho, sem apelo.\n"
        )
    else:
        task = (
            "TAREFA: escreve um post para o Mastodon (máximo 500 caracteres) "
            "sobre o ASSUNTO indicado no fim deste prompt. Retira dele uma "
            "imagem concreta (um gesto, um objecto, um lugar, um som, um "
            "material) e desenvolve-a. A VOZ vem da persona.\n\n"
        )
        position_rule = ""

    # One subject, no menu, and no recent posts. The recent posts used to be
    # in this prompt as "NÃO repetir", which on a small model reads as "this
    # is the register to continue": on 2026-11-18 the model reproduced the
    # previous post's clause almost verbatim. Non-repetition is enforced after
    # generation (_copied_phrase, _overlap_with_recent), not asked for here.
    return (
        "PERSONA — esta é a tua voz; segue-a sempre:\n"
        f"{persona_block}\n\n"
        f"{stance_block}"
        f"{task}"
        "REGRAS:\n"
        "- Escreve só sobre o ASSUNTO. Não escrevas sobre outro assunto.\n"
        "- A persona é vinculativa também no conteúdo: não escrevas nada que a "
        "contradiga.\n"
        f"{position_rule}"
        "- O ASSUNTO é matéria-prima, não texto. Não resumas, não cites, não "
        "escrevas notícia: podes inventar a partir dele, contradizê-lo ou "
        "deformá-lo.\n"
        "- Se o assunto estiver noutra língua, aproveita a ideia e escreve em "
        "português.\n"
        "- Evita as imagens gastas: tempo, relógio, horas, sombra, rio, "
        "silêncio. A mesma atmosfera diz-se com ferrugem, osso, máquina, "
        "distância.\n"
        "- Nunca comentes pessoas reais, protestos, guerras, tragédias ou "
        "política partidária.\n"
        "- Não escrevas hashtags no corpo do texto; as hashtags dos temas são "
        "adicionadas automaticamente no fim. Sem markdown. Sem aspas.\n"
        "- Texto corrido: duas a cinco frases, sem título, sem listas, sem "
        "quebras de linha.\n"
        "- Português de Portugal, nunca do Brasil: autónomo (não autônomo), "
        "controlo (não controle), ecrã (não tela), telemóvel (não celular), "
        "a superfície (não sua superfície).\n\n"
        f"Ângulo de hoje (aplicado ao assunto): {framing}\n\n"
        f"Memórias (tom, não assunto):\n{memory_block}\n\n"
        "ASSUNTO DE HOJE:\n"
        f"{subject_block}\n\n"
        "Post (em português de Portugal):"
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
                "num_predict": 220,
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
        if len(text) > 500:
            text = text[:497] + "..."
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
    # The prompt says "Sem markdown", but on 2026-11-19 a post shipped with
    # "*armazena o que os outros esquecem*" and Mastodon shows the asterisks
    # literally. Strip emphasis and inline code, then any stray marker.
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("*", "")
    text = re.sub(r"^\s*[#*\->]+\s*", "", text)
    # Hashtags in the body are not ours: the model copies them from the memory
    # chunks. The theme tags are appended afterwards, and only those. `\w*`
    # also removes a lone "#" -- the 2026-11-18 sample ended with one.
    text = re.sub(r"(?<!\w)#\w*", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"[\s\-–—:;,]+$", "", text)
    return text.strip()


def _entry_hashtags(entry: str, limit: int = 2) -> str:
    """Hashtags of the one subject the post was actually written from.

    The old code took the first two tags from *all* the research plus the
    trending list and the local subjects. On 2026-11-25 only #cat was trending,
    but the chosen subject was a local status about the Bonsai museum in
    Sintra: the post went out with "#cat #bonsai" and no cat in the text. A tag
    whose snippets were researched but not used is not a theme of the post.
    """
    if not entry:
        return ""
    label = re.match(r"\s*\[#([^\]]+)\]", entry)
    raws = (
        [label.group(1)]
        if label
        else re.findall(r"#([\w]+)", _fold(entry), flags=re.UNICODE)
    )
    tags = []
    seen = set()
    for raw in raws:
        tag = _tag_key(raw)
        if not tag or tag in seen or _blocked_subject(tag):
            continue
        seen.add(tag)
        tags.append(f"#{tag}")
        if len(tags) >= limit:
            break
    return " ".join(tags)


def _append_theme_tags(text: str, entry: str, max_len: int = 500) -> str:
    """Append the chosen subject's hashtags within the Mastodon length limit."""
    tags = _entry_hashtags(entry)
    if not tags or not text:
        return text
    # Already tagged (e.g. model ignored the instruction): don't double-tag.
    if tags.split()[0].lower() in text.lower():
        return text
    suffix = " " + tags
    if len(text) + len(suffix) <= max_len:
        return text + suffix
    room = max_len - len(suffix) - 1
    if room < 40:
        return text
    cut = text[:room].rstrip()
    # Don't end mid-word or mid-sentence fragment when we can avoid it.
    last_space = cut.rfind(" ")
    if last_space > room - 30:
        cut = cut[:last_space].rstrip(" ,;:")
    return cut + "…" + suffix


def _generate_reflective_post() -> str:
    """Generate a post that is not yesterday's post.

    Four inputs, two jobs. Subject: the trend research (what is actually being
    said under the trending tags), the local timeline and memory. Voice: the
    persona configured in /admin. They are kept separate on purpose -- the
    persona asks for "absence, quiet erosion and stillness", which is exactly
    the theme the account was stuck on; the persona decides HOW it is said,
    the research decides WHAT is said.

    Every candidate is scored against the account's recent posts and against
    the imagery it has been recycling. A candidate is thrown away -- not
    ranked, thrown away -- when it repeats a recent post: either the share of
    reused words reaches DUPLICATE_OVERLAP, or it contains a phrase copied
    verbatim (DUPLICATE_SHINGLE words). A copy is not retried on the same
    subject: the next subject is tried, because a small model handed the same
    prompt tends to produce the same paragraph (2026-11-18: six attempts, six
    copies, no post).

    The material is rotated too, not just filtered: a trending tag already
    carried by a recent post is dropped, timeline subjects that echo a recent
    post are dropped, and the remaining subjects are ordered by how little
    they share with recent posts. On 2026-11-18 the only trend was #vegan, the
    same tag that fed the previous day, and the model rewrote the same
    paragraph.

    The persona also supplies a POSITION (see _persona_stance): the owner asked
    that the posts take an abolitionist stance, not merely avoid contradicting
    one. On 2026-11-18 he chose that the position is asserted when the subject
    allows it rather than forced onto every subject -- requiring it always
    tied animal exploitation to a Mastodon filter tip by main force. So
    subjects that can carry it are ranked first, and the position is only
    asked for (and only verified) on those.
    """
    rag = _memory_snippet()
    recent = _recent_posts()
    trends = _fetch_trending_tags()
    # Prefer themes the account has not spent yet. The tags appended to each
    # post are the record of what fed it, so a tag that still trends but is
    # already in the history is yesterday's material, not today's.
    used = _used_theme_tags(recent)
    trends = [t for t in trends if _tag_key(t) not in used]
    subjects = _fetch_local_subjects()
    subjects = [
        subject
        for subject in subjects
        if _has_prose(subject) and not _is_stale_subject(subject, recent)
    ]
    research = _fetch_trend_research(trends)
    persona = _persona()
    stance = _persona_stance(persona)

    # The harness chooses the subject; the model only writes. The first entry
    # is the one least related to the recent posts, so a repeat would have to
    # come from the model, not from the material. If that entry still produces
    # a copy, the next most novel one is tried -- changing the subject, not
    # re-rolling the same prompt and hoping.
    menu = _rank_subjects(_subject_menu(research, subjects), recent)
    if not menu:
        menu = [""]  # nothing researched: a memory-only post is better than none
    max_attempts = POST_CANDIDATES + 1

    best = ""
    best_entry = ""
    best_score = None
    attempts = 0
    duplicates = 0
    off_stance = 0
    for entry in menu[:max_attempts]:
        attempts += 1
        framing = random.choice(FRAMINGS)
        # The position is required only where the subject can carry it; see
        # _carries_stance and the owner's decision of 2026-11-18.
        entry_stance = stance if _carries_stance(entry) else ""
        prompt = _build_prompt(rag, entry, framing, persona, entry_stance)
        text = _generate_once(prompt)
        if not text:
            continue
        overlap = _overlap_with_recent(text, recent)
        copied = _copied_phrase(text, recent)
        if overlap >= DUPLICATE_OVERLAP or copied:
            duplicates += 1
            print(
                f"[Mastodon Skill] subject {entry[:40]!r} produced a copy "
                f"(overlap={overlap:.2f}, phrase={copied or '-'}), trying the "
                "next subject",
                flush=True,
            )
            continue
        score = (_cliche_hits(text), overlap)
        if entry_stance and not _asserts_stance(text):
            off_stance += 1
            print(
                f"[Mastodon Skill] subject {entry[:40]!r} did not assert the "
                "position, trying the next subject",
                flush=True,
            )
            continue
        if best_score is None or score < best_score:
            # Keep the subject that produced the winner: the hashtags appended
            # below belong to this material, not to whatever was tried last.
            best, best_entry, best_score = text, entry, score
        # Clean: stop paying for inference. A cliché candidate is kept as the
        # fallback but the next subject gets a chance to do better.
        if score[0] == 0:
            break

    if not best:
        print(
            f"[Mastodon Skill] refusing to publish "
            f"({duplicates}/{attempts} candidates were copies, "
            f"{off_stance}/{attempts} did not assert the position)",
            flush=True,
        )
        return ""

    print(
        f"[Mastodon Skill] post candidate chosen (clichés={best_score[0]}, "
        f"overlap={best_score[1]:.2f}, attempts={attempts}, "
        f"subject={best_entry[:40]!r})",
        flush=True,
    )
    return _append_theme_tags(best, best_entry)


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
    polls = 0
    while _running:
        try:
            polled = await _poll_once()
        except Exception as e:
            polled = False
            print(f"[Mastodon Skill] poll error: {e}", flush=True)
        if polled:
            polls += 1
            # One line every ~10 minutes instead of every 30 seconds: the
            # journal had 118 "polled OK" lines per hour, which made it look
            # like the skill was doing something it was not.
            if polls % 20 == 0:
                print(f"[Mastodon Skill] polled OK ({polls} polls)", flush=True)

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
