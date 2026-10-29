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
BOT_ACCT = getattr(
    config, "MASTODON_BOT_ACCT", os.getenv("MASTODON_BOT_ACCT", "phantasma@s.linuxkafe.com")
).strip()

_COMMAND_TOKEN = os.getenv("PHANTASMA_COMMAND_TOKEN", "").strip()
_COMMAND_HEADERS = {"Authorization": f"Bearer {_COMMAND_TOKEN}"} if _COMMAND_TOKEN else {}

_last_seen_id = None
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
        # Only answer the configured admin account.
        if acct != ADMIN_ACCT:
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
