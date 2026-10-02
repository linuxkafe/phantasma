import asyncio
import logging
import os
import threading
from typing import Optional

import discord
import httpx

import config

# --- Configuração da Skill ---
# Esta skill não é ativada por voz local, serve apenas para carregar o daemon.
TRIGGER_TYPE = "contains"
TRIGGERS = []




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

# Command endpoints require a credential, and this is a program, not a browser:
# it has no session and cannot log in as the owner. It therefore presents the
# command token, exactly as any shell script or Home Assistant integration does.
#
# It used to send nothing and be accepted, because the gate returned True
# whenever PHANTASMA_COMMAND_TOKEN was unset -- so the "protection" was only
# ever protecting a box where someone had remembered to configure it, and the
# LAN was open everywhere else. Discord is also the one caller with a wide blast
# radius: a message from an allowed server reaches the house, and the
# allow-list here is a user-id check on Discord's side, not on ours.
#
# If the token is unset the request is now refused, and the message says why
# rather than surfacing as a bare 401.
_COMMAND_TOKEN = os.getenv("PHANTASMA_COMMAND_TOKEN", "").strip()
_COMMAND_HEADERS = (
    {"Authorization": f"Bearer {_COMMAND_TOKEN}"} if _COMMAND_TOKEN else {}
)



def _check_access(user_id, prompt_lower, self=None):
    """
    Retorna (AcessoPermitido: bool, MensagemErro: str)

    A regra vive em src/api/discord_access.py. Ficava aqui e nao havia testes
    possiveis: este modulo importa o discord.py e constroi um Client vivo no
    import, portanto testar a regra exigia instalar um SDK de 15 MB no venv de
    desenvolvimento (so existe no de producao) ou construir um falso que
    ganhava um atributo novo por cada linha do skill importada -- Intents, um
    Client que aceita keywords, Client().event. Um falso que cresce e um sitio
    onde o teste deixa de testar a coisa real.

    A precedencia, que e o que interessa:

    1. As listas do ambiente (DISCORD_ADMIN_USERS / DISCORD_STANDARD_USERS) sao
       a escolha deliberada do dono, feita fora da aplicacao, e nenhum campo de
       perfil lhes pode tirar o que tem.
    2. O perfil. Qualquer conta com sessao pode reclamar o seu id de Discord em
       /perfil, e o PAPEL WEB dessa conta decide: admin passa a acesso total,
       user passa ao acesso normal com a mesma quota. E assim que a segunda
       pessoa da casa entra, sem editar um ficheiro que so o dono abre.
    3. Negado.

    E uma mudanca de AUTORIZACAO, e o movimento perigoso e um-utilizador
    reclamar o id de outro. Daí o indice unico, a recusa de sobrescrever, e --
    para ids que existam duas vezes -- a recusa em vez da palpite. Em qualquer
    caso ambiguo a resposta e "sem acesso", nunca "provavelmente o acesso errado".
    """
    from src.api import discord_access

    # Resolve which skills this message lands on BEFORE anything is spent, and
    # refuse on that. Deciding after the fact would mean the house has already
    # acted by the time we noticed.
    #
    # `matching=None` on purpose: an unwired resolver must REFUSE, not read as
    # "no skill matched". Otherwise a wiring fault quietly turns into "every
    # guest request is just conversation", which is fail-open on the one check
    # that keeps them away from the devices.
    matching = None
    resolver = getattr(getattr(self, "context", None), "resolve_skill", None)
    if resolver is None:
        resolver = _resolve_skill
    if resolver is not None:
        matching = resolver(prompt_lower)
    return discord_access.check(user_id, prompt_lower, matching)


async def _send_to_phantasma(prompt):
    """Envia o texto para a API local e recebe a resposta"""
    async with httpx.AsyncClient(timeout=300) as http_client:
        try:
            payload = {"prompt": prompt}
            if not _COMMAND_HEADERS:
                return (
                    "Não tenho credencial para falar com a casa. "
                    "Define PHANTASMA_COMMAND_TOKEN no ambiente do phantasma "
                    "e reinicia o serviço."
                )
            # Usa a API local para processar (garante que passa pelo route_and_respond)
            response = await http_client.post(
                PHANTASMA_API_URL, json=payload, headers=_COMMAND_HEADERS
            )
            if response.status_code == 401:
                return (
                    "A casa recusou o comando (401). O "
                    "PHANTASMA_COMMAND_TOKEN do Discord não corresponde ao do "
                    "serviço."
                )
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


# The resolver for the skill a request lands on. `on_message` is a module-level
# event handler, so it has no `self` to read a context off -- a previous revision
# passed `self` here and raised NameError on EVERY message. `discord.py` swallows
# exceptions raised inside an event handler, so the bot received the message,
# failed, and said nothing; from Discord that is indistinguishable from an
# offline bot.
_resolve_skill = None


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
    # No `self`. This function is registered as a module-level `on_message`
    # event, so its only parameter is `message` and there is no instance to name
    # here. A previous revision passed `self` and raised NameError on EVERY
    # message: discord.py swallows exceptions raised inside an event handler, so
    # the bot received the message, failed silently, and said nothing -- which
    # is indistinguishable from "the bot is offline" from the outside.
    #
    # So the skill reads the resolver off its own class object, which is where
    # the loader put it. `skill` is bound at import by the adapter.
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
    global _fly_brain, _resolve_skill
    if not hasattr(config, "DISCORD_BOT_TOKEN"):
        return

    # Resolve the shared FlyBrain rather than building a second one over the
    # same store. Two instances meant a Discord reaction stepped a brain whose
    # state the assistant never saw.
    from src.brain.fly_brain import get_shared_fly_brain

    _fly_brain = get_shared_fly_brain()
    print(f"[Discord Skill] FlyBrain inicializado: {_fly_brain is not None}")

    # The skill resolver, for deciding which skill a request lands on BEFORE any
    # of it is spent. `assistant.py` builds the shared `SkillContext` WITHOUT
    # passing `resolve_skill`, so there is no instance for the event handler to
    # read it off either; without this, every guest request arrives with
    # `matching=None` and is refused as "could not verify what it touches".
    #
    # So the resolver is built here against the loader's own skill directory.
    # A second SkillLoader is the concern that `_installed_skills` documents,
    # and it applies here too -- but this loader is used to MATCH, never to
    # execute, and it loads no daemons and holds no state.
    try:
        from skills.loader import SkillLoader

        _resolver_loader = SkillLoader(skills_dir=config.SKILLS_DIR)
        _resolve_skill = _resolver_loader.resolve_matching_skills
        print("[Discord Skill] Resolver de skills ligado")
    except Exception as exc:  # noqa: BLE001
        print(f"[Discord Skill] Resolver de skills NAO ligado: {exc}")

    print("[Discord Skill] A iniciar daemon do Discord...")
    t = threading.Thread(target=_run_discord_loop, daemon=True)
    t.start()


# O handle é obrigatório pela estrutura do assistant.py, mas não faz nada via voz.
def handle(user_prompt_lower, user_prompt_full):
    return None
