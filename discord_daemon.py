import discord
import httpx
import asyncio
import sys
import os
import logging

# Importar configurações
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [DiscordBot] - %(levelname)s - %(message)s'
)

# API Local do Phantasma (onde injetamos os comandos)
PHANTASMA_API_URL = "http://127.0.0.1:5000/comando"

# Setup do Cliente Discord
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

def is_user_allowed(user_id):
    """ Verifica se o ID do utilizador está na lista branca do config.py """
    if hasattr(config, 'DISCORD_ALLOWED_USERS'):
        return user_id in config.DISCORD_ALLOWED_USERS
    return False

async def send_to_phantasma(prompt):
    """ Envia o texto para a API local do Phantasma e recebe a resposta """
    async with httpx.AsyncClient(timeout=300) as http_client:
        try:
            payload = {"prompt": prompt}
            response = await http_client.post(PHANTASMA_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            
            # A API retorna algo como: 
            # {"status": "ok", "action": "...", "response": "Texto da resposta"}
            return data.get("response", "O Phantasma processou o comando, mas não devolveu texto.")
            
        except httpx.HTTPStatusError as e:
            logging.error(f"Erro HTTP da API: {e}")
            return f"Erro ao contactar o cérebro: {e.response.status_code}"
        except httpx.RequestError as e:
            logging.error(f"Erro de Conexão à API: {e}")
            return "O cérebro do Phantasma parece estar desligado (API incontactável)."
        except Exception as e:
            logging.error(f"Erro genérico: {e}")
            return f"Erro inesperado: {str(e)}"

@client.event
async def on_ready():
    logging.info(f'Logado no Discord como {client.user}')
    # Define o status do Bot
    await client.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, 
        name="Buuu"
    ))

@client.event
async def on_message(message):
    # Ignorar mensagens do próprio bot
    if message.author == client.user:
        return

    # Segurança: Verificar whitelist
    if not is_user_allowed(message.author.id):
        # Opcional: Logar tentativas não autorizadas
        logging.warning(f"Acesso negado para: {message.author.name} ({message.author.id})")
        return

    # Lógica de ativação:
    # 1. Se for Mensagem Direta (DM), processa tudo.
    # 2. Se for num servidor (Guild), exige menção (@Bot).
    prompt = ""
    
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = client.user in message.mentions

    if is_dm:
        prompt = message.content
    elif is_mention:
        # Remove a menção para ficar só o comando limpo
        prompt = message.content.replace(f'<@{client.user.id}>', '').strip()
    else:
        return # Não é para nós

    if not prompt:
        return

    logging.info(f"Comando recebido de {message.author.name}: {prompt}")

    # Indica que está a "escrever" (a pensar)
    async with message.channel.typing():
        # Envia para o Phantasma
        response_text = await send_to_phantasma(prompt)
        
        # Limita a resposta a 2000 caracteres (limite do Discord)
        if len(response_text) > 2000:
            response_text = response_text[:1990] + "..."

        # Responde no Discord
        await message.channel.send(response_text)

# Execução
if __name__ == "__main__":
    if not hasattr(config, 'DISCORD_BOT_TOKEN') or not config.DISCORD_BOT_TOKEN:
        logging.error("Token do Discord não encontrado em config.py")
        sys.exit(1)
        
    try:
        client.run(config.DISCORD_BOT_TOKEN)
    except Exception as e:
        logging.error(f"Falha ao iniciar o Bot: {e}")
