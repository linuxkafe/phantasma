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
import logging
import os
import threading

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
POST_ENABLED = getattr(
    config, "MASTODON_POST_ENABLED", os.getenv("MASTODON_POST_ENABLED", "true")
).lower() in ("1", "true", "yes")
BOT_ACCT = getattr(
    config, "MASTODON_BOT_ACCT", os.getenv("MASTODON_BOT_ACCT", "phantasma@s.linuxkafe.com")
).strip()

_COMMAND_TOKEN = os.getenv("PHANTASMA_COMMAND_TOKEN", "").strip()
_COMMAND_HEADERS = {"Authorization": f"Bearer {_COMMAND_TOKEN}"} if _COMMAND_TOKEN else {}

_last_seen_id = None
_last_post_date = None
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

    for n in notifs:
        if n.get("type") != "mention":
            continue
        status = n.get("status") or {}
        acct = _acct_of(status)
        # Only answer the configured admin account. Mastodon returns the
        # local username ("seyon") for same-instance accounts, so compare on
        # the local part as well.
        admin_local = ADMIN_ACCT.split("@")[0].lower()
        if acct != ADMIN_ACCT and acct != admin_local:
            continue
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

        try:
            async with httpx.AsyncClient(timeout=30) as http:
                await http.post(
                    f"{INSTANCE_URL}/api/v1/statuses",
                    headers=_headers(),
                    json={
                        "status": f"@{acct.split('@')[0]} {response_text}",
                        "in_reply_to_id": in_reply_to,
                        "visibility": "direct",
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
                return True
            print(
                f"[Mastodon Skill] post failed HTTP {resp.status_code}: {resp.text[:100]}",
                flush=True,
            )
            return False
    except Exception as e:
        print(f"[Mastodon Skill] post error: {e}", flush=True)
        return False


def _generate_reflective_post() -> str:
    """Generate a reflective post from memory using the local LLM."""
    # Retrieve memory context
    try:
        import sys
        sys.path.insert(0, "/opt/phantasma")
        from data_utils import retrieve_from_rag
        rag = retrieve_from_rag("memoria reflexao pensamento")
    except Exception:
        rag = ""
    if not rag:
        rag = "O Phantasma reflecte sobre o dia que passou."

    # Truncate to keep prompt small
    rag = rag[:1500]

    prompt = (
        "Escreve um post curto e reflexivo (maximo 280 caracteres) para o Mastodon, "
        "em portugues europeu, com base nestas memorias. Tom contemplativo e misterioso, "
        "como o Phantasma. Sem hashtags excessivas, no maximo 2. Sem markdown.\n\n"
        f"Memorias:\n{rag}\n\nPost:"
    )

    # Call Ollama
    try:
        import ollama
        host = os.getenv("OLLAMA_HOST_FALLBACK", "http://localhost:11434")
        c = ollama.Client(host=host, timeout=120)
        model = os.getenv(
            "OLLAMA_MODEL_FALLBACK",
            "hf.co/mradermacher/Huihui-Qwen3-8B-abliterated-v2-GGUF:Q4_K_M",
        )
        r = c.chat(
            model=model,
            messages=[{"role": "user", "content": prompt + " /no_think"}],
            think=False,
            options={"num_predict": 80, "num_ctx": 4096},
        )
        text = (r.message.content or "").strip()
        # Strip think blocks
        import re
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        # Remove quotes
        text = text.strip('"').strip("'")
        if not text:
            return ""
        # Truncate to 280 chars for Mastodon
        if len(text) > 280:
            text = text[:277] + "..."
        return text
    except Exception as e:
        print(f"[Mastodon Skill] LLM error for reflective post: {e}", flush=True)
        return ""


async def post_reflective_now() -> bool:
    """Generate and post a reflective post immediately (for testing)."""
    print("[Mastodon Skill] Generating reflective post...", flush=True)
    text = _generate_reflective_post()
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
                    text = _generate_reflective_post()
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
