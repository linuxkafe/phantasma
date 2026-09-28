import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional

import discord
import httpx

import config
import config as config_module
from src.brain.fly_brain import FlyBrain
from src.brain.persistence import FlyBrainStore

# --- Configuração da Skill ---
# Esta skill não é ativada por voz local, serve apenas para carregar o daemon.
TRIGGER_TYPE = "contains"
TRIGGERS = []

# --- Definições de Permissões ---
# Palavras-chave para identificar skills permitidas (Meteorologia e Calculadora)
# Duplicamos aqui os triggers principais para evitar importar outros módulos e causar ciclos.
ALLOWED_SKILL_KEYWORDS = [
    # Meteorologia
    "tempo",
    "clima",
    "meteorologia",
    "previsão",
    "vai chover",
    "qualidade do ar",
    # Calculadora
    "quanto é",
    "calcula",
    "a dividir",
    "vezes",
    "somado",
    "subtraído",
    "+",
    "-",
    "*",
    "/",
]

# Cache de Quotas: { user_id: { "date": "YYYY-MM-DD", "count": 0 } }
_USER_QUOTAS = {}

# FlyBrain instance for reaction feedback
_fly_brain = None

# --- Setup do Logging ---
logger = logging.getLogger("DiscordSkill")

# --- Lógica do Bot Discord ---

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
client = discord.Client(intents=intents)

# URL da API local do Phantasma (comunica com o assistant.py via HTTP para thread safety)
PHANTASMA_API_URL = "http://127.0.0.1:5000/comando"


def _check_access(user_id, prompt_lower):
    """
    Retorna (AcessoPermitido: bool, MensagemErro: str)
    """
    # 1. Verificar se é Admin
    if hasattr(config, "DISCORD_ADMIN_USERS") and user_id in config.DISCORD_ADMIN_USERS:
        return True, ""

    # 2. Verificar se é Standard
    if (
        hasattr(config, "DISCORD_STANDARD_USERS")
        and user_id in config.DISCORD_STANDARD_USERS
    ):
        return _process_standard_quota(user_id, prompt_lower)

    # 3. Não autorizado
    return False, "Acesso negado."


def _process_standard_quota(user_id, prompt_lower):
    """
    Gere a lógica de limites para utilizadores standard.
    """
    # A. Verifica se é uma Skill Permitida (Weather/Calc) -> Uso Gratuito/Ilimitado
    is_allowed_skill = any(
        keyword in prompt_lower for keyword in ALLOWED_SKILL_KEYWORDS
    )
    if is_allowed_skill:
        return True, ""

    # B. Se for LLM (Pergunta geral), verificar quota diária
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Inicializa ou Reinicia quota se mudou o dia
    if user_id not in _USER_QUOTAS or _USER_QUOTAS[user_id]["date"] != today_str:
        _USER_QUOTAS[user_id] = {"date": today_str, "count": 0}

    current_count = _USER_QUOTAS[user_id]["count"]
    limit = getattr(config, "DISCORD_DAILY_LLM_LIMIT", 3)

    if current_count < limit:
        _USER_QUOTAS[user_id]["count"] += 1
        return True, ""
    else:
        return (
            False,
            f"Atingiste o teu limite diário de {limit} perguntas ao cérebro do Phantasma (as ferramentas de tempo e cálculo continuam disponíveis).",
        )


async def _send_to_phantasma(prompt):
    """Envia o texto para a API local e recebe a resposta"""
    async with httpx.AsyncClient(timeout=300) as http_client:
        try:
            payload = {"prompt": prompt}
            # Usa a API local para processar (garante que passa pelo route_and_respond)
            response = await http_client.post(PHANTASMA_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "...")
        except Exception as e:
            return f"Erro de comunicação interna: {e}"


@client.event
async def on_ready():
    print(f"[Discord Skill] Ligado como {client.user}")
    await client.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="Buu!")
    )


@client.event
async def on_message(message):
    # Ignorar mensagens do próprio bot
    if message.author == client.user:
        return

    # Lógica de ativação: DM ou Menção
    prompt = ""
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = client.user in message.mentions

    if is_dm:
        prompt = message.content
    elif is_mention:
        prompt = message.content.replace(f"<@{client.user.id}>", "").strip()
    else:
        return

    if not prompt:
        return

    # --- VERIFICAÇÃO DE PERMISSÕES E QUOTAS ---
    allowed, error_msg = _check_access(message.author.id, prompt.lower())

    if not allowed:
        # Se for um user standard bloqueado, avisamos. Se for desconhecido, ignoramos ou logamos.
        if "limite diário" in error_msg:
            await message.channel.send(f"🚫 {error_msg}")
        else:
            print(
                f"[Discord Skill] Acesso negado para {message.author.name} ({message.author.id})"
            )
        return

    print(f"[Discord Skill] Comando aceite de {message.author.name}: {prompt}")

    async with message.channel.typing():
        response_text = await _send_to_phantasma(prompt)

        # Corta a resposta se exceder o limite do Discord (2000 chars)
        if len(response_text) > 2000:
            response_text = response_text[:1990] + "..."

        await message.channel.send(response_text)


_REACTION_REWARD = {
    "👍":  +1.0,
    "👎":  -1.0,
    "❤️":  +1.0,
    "🔥":  +1.0,
    "😡":  -1.0,
    "😢":  -0.5,
}


def _reward_for_emoji(emoji) -> Optional[float]:
    """Return the FlyBrain reward for a Discord reaction emoji, or None."""
    return _REACTION_REWARD.get(str(emoji))


@client.event
async def on_raw_reaction_add(payload):
    """Handle reactions via raw payload — works for DMs and uncached messages.

    Uses only network fetches (fetch_channel/fetch_message/fetch_user), never
    the internal cache, so reactions after a bot restart or in DM still apply
    the FlyBrain reward. Each failure path is logged instead of returning
    silently.
    """
    try:
        if client.user is not None and payload.user_id == client.user.id:
            return

        # Channel and message always network-fetched (cache may be empty).
        try:
            channel = await client.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
        except Exception as e:
            print(f"[Discord Skill] Falha ao obter canal/mensagem: {e}")
            return

        # Resolve reacting user (Member in guilds, fetch_user for DM).
        if getattr(payload, "member", None) is not None:
            user = payload.member
            if isinstance(user, discord.Member):
                user = user._user if hasattr(user, "_user") else user
            user_obj = user if isinstance(user, discord.User) else await client.fetch_user(payload.user_id)
        else:
            try:
                user_obj = await client.fetch_user(payload.user_id)
            except Exception as e:
                print(f"[Discord Skill] Falha ao obter user {payload.user_id}: {e}")
                return

        # Build a fake reaction object compatible with the handler.
        class _RawReaction:
            def __init__(self, message, emoji):
                self.message = message
                self.emoji = emoji

        reaction_obj = _RawReaction(message, payload.emoji)
        await _handle_reaction(reaction_obj, user_obj)
    except Exception as e:
        print(f"[Discord Skill] Erro inesperado em on_raw_reaction_add: {e}")


async def _handle_reaction(reaction, user):
    """Core reaction handling logic.

    Accepts reactions on the bot's own messages AND on the reacting user's
    own messages (personal assistant DM context: a thumbs-down on your own
    message is valid feedback about the last exchange). Reactions on third-
    party messages are ignored to keep the reward signal clean.
    """
    if user == client.user:
        return
    author = reaction.message.author
    if author != client.user and author != user:
        print(f"[Discord Skill] Ignorada reação de {user} em mensagem de terceiros")
        return

    reward = _reward_for_emoji(reaction.emoji)
    if reward is None or _fly_brain is None:
        if reward is None:
            print(f"[Discord Skill] Emoji sem reward mapeado: {reaction.emoji}")
        return

    print(f"[Discord Skill] Reaction {reaction.emoji} from {user} -> reward={reward}")

    # Apply to FlyBrain
    _fly_brain.step(
        topic_angle_deg=_fly_brain.ring.orientation_deg,
        novelty=0.1,
        reward=reward,
    )
    print(f"[Discord Skill] FlyBrain updated: reward={reward}")

    # Bridge to memory graph: reward the current topic node.
    try:
        from src.brain.memory_graph import apply_reward, get_current_topic, init_db

        init_db()
        current = get_current_topic()
        if current:
            apply_reward(reward, current)
        else:
            print("[Discord Skill] Sem tópico atual para recompensar no grafo")
    except Exception as e:
        print(f"[Discord Skill] Falha ao ligar reward ao memory graph: {e}")

    # Acknowledge in Discord
    try:
        if reward > 0:
            ack = "👍 Registado como reforço positivo."
        elif reward < -0.5:
            ack = "😡 Registado como punição forte."
        else:
            ack = "😢 Registado como punição leve."
        await reaction.message.channel.send(ack, delete_after=10)
        print("[Discord Skill] Ack sent")
    except Exception as e:
        print(f"[Discord Skill] Falha ao enviar ack: {e}")


# --- Daemon Setup ---


def _run_discord_loop():
    """
    Função que corre numa thread separada.
    Cria um novo event loop asyncio para o Discord.py não colidir com o Flask/Main thread.
    """
    token = getattr(config, "DISCORD_BOT_TOKEN", None)
    if not token:
        print("[Discord Skill] ERRO: Token não configurado.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(client.start(token))
    except Exception as e:
        print(f"[Discord Skill] O bot crashou: {e}")
    finally:
        loop.close()


def init_skill_daemon():
    """Inicia o bot do Discord em background quando o assistente arranca."""
    global _fly_brain
    if not hasattr(config, "DISCORD_BOT_TOKEN"):
        return

    # Resolve the shared FlyBrain rather than building a second one over the
    # same store. Two instances meant a Discord reaction stepped a brain whose
    # state the assistant never saw.
    from src.brain.fly_brain import get_shared_fly_brain

    _fly_brain = get_shared_fly_brain()
    print(f"[Discord Skill] FlyBrain inicializado: {_fly_brain is not None}")

    print("[Discord Skill] A iniciar daemon do Discord...")
    t = threading.Thread(target=_run_discord_loop, daemon=True)
    t.start()


# O handle é obrigatório pela estrutura do assistant.py, mas não faz nada via voz.
def handle(user_prompt_lower, user_prompt_full):
    return None
