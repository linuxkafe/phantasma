# Phantasma Voice Assistant

Phantasma (ou bumblebee até conseguir uma hotword funcional) é um assistente de voz local-first (offline) e modular, construído em Python. Ele foi desenhado para ser privado, correndo inteiramente no teu próprio servidor, sem depender de serviços de nuvem de terceiros (exceto para pesquisas na web, que são feitas através da tua própria instância do SearxNG).

Ele usa `pvporcupine` para a deteção da *hotword* ("Bumblebee"), `whisper` para transcrição, `ollama` (Llama3) como cérebro, e `piper`/`sox` para uma voz robótica personalizada (inspirada no Bumblebee dos Transformers).

## Funcionalidades

* **Hotword 100% Offline:** Usa o `pvporcupine` para uma deteção de *hotword* ("Bumblebee") fiável e sem falsos positivos.
* **Transcrição Local:** Utiliza o `whisper` (modelo `medium`) para transcrição de voz para texto.
* **Cérebro Local (LLM):** Integrado com o `ollama` para usar o modelo `llama3:8b-instruct-8k`.
* **Voz Robótica (TTS):** Usa o `piper` com efeitos do `sox` para criar a voz do assistente.
* **API e CLI:** Além da voz, pode ser controlado por uma API REST (Flask) e um *script* de CLI (`phantasma-cli.sh`).
* **Sistema de Skills Modular:** As funcionalidades (Cálculo, Meteorologia, Música, Memória) são carregadas dinamicamente a partir da pasta `skills/`.
* **RAG (Retrieval-Augmented Generation):**
    * **Memória de Longo Prazo:** Pode memorizar factos ("Bumblebee, memoriza isto...") numa base de dados SQLite.
    * **Pesquisa Web:** Enriquece as respostas do Ollama com resultados de pesquisa em tempo real, usando a tua instância local do **SearxNG**.
* **Feedback de Áudio:** Toca um *snippet* de música aleatório e uma saudação quando a *hotword* é detetada, para que saibas quando começar a falar.
* **Personalidade:** O *prompt* do sistema está configurado para a personalidade do Bumblebee, com regras para evitar *bugs* de TTS ("WOOHOO") e manter as preferências do utilizador (vegan).

---

## Arquitetura e Componentes

| Componente | Tecnologia Utilizada | Propósito |
| :--- | :--- | :--- |
| **Hotword** | `pvporcupine` (Picovoice) | Deteção "Bumblebee" offline. |
| **STT (Voz->Texto)** | `openai-whisper` (Medium) | Transcrição local. |
| **LLM (Cérebro)** | `ollama` (Llama3 8K) | Processamento de linguagem. |
| **TTS (Texto->Voz)** | `piper` + `sox` | Geração de voz. |
| **Leitor de Música** | `mpg123` | Tocar *snippets* e músicas. |
| **Pesquisa Web** | `searxng` (Docker) | RAG - Contexto da Web. |
| **Memória** | `sqlite3` | RAG - Memória de Longo Prazo. |
| **API** | `flask` | Receber comandos via `curl`. |
| **Serviço** | `systemd` | Correr o assistente em *background*. |

---

## Instalação

### 1. Pré-requisitos (Sistema)

Assume-se um servidor Ubuntu/Debian. Estes pacotes são necessários para o áudio e para o `pvporcupine`.
sudo apt update
sudo apt install sox mpg123 portaudio19-dev

### 2. Serviços Externos (Ollama e SearxNG)

Este guia assume que já tens:
* **Ollama** instalado e a correr.
* **SearxNG** a correr num contentor Docker, acessível em `http://127.0.0.1:8081`.

### 3. Criar o Modelo 8K do Ollama

Precisas de dizer ao Ollama para usar os 8K de contexto do Llama3.

Cria um ficheiro chamado `Modelfile_Llama3_8k`:
vim Modelfile_Llama3_8k
Cola o seguinte:
FROM llama3:8b-instruct-q5_k_m
PARAMETER num_ctx 8192

Agora, cria o modelo no Ollama:
ollama create llama3:8b-instruct-8k -f Modelfile_Llama3_8k

### 4. Ambiente Python (Venv)

É **altamente recomendado** recriar o `venv` para garantir que não tens bibliotecas antigas (como o Pocketsphinx).

# Navega para a pasta do projeto
cd /root/scripts/phantasma

# Apaga o venv antigo (se existir)
rm -rf venv

# Cria um venv limpo
python3 -m venv venv

# Ativa o venv
source venv/bin/activate

# Atualiza o pip
pip install --upgrade pip

# Instala todas as dependências necessárias
pip install sounddevice openai-whisper ollama torch httpx flask pvporcupine

## Configuração

### 1. `config.py`

Este é o ficheiro de controlo principal. Edita-o (`vim config.py`) para ajustar os teus caminhos e chaves:

* `ACCESS_KEY`: A tua chave do Picovoice (Porcupine).
* `SEARXNG_URL`: Garante que está a apontar para a tua instância (ex: `http://127.0.0.1:8081`).
* `ALSA_DEVICE_IN` e `ALSA_DEVICE_OUT`: Ajusta os IDs do teu microfone e altifalantes.
    * Usa `arecord -l` para encontrar dispositivos de entrada (Input).
    * Usa `aplay -l` para encontrar dispositivos de saída (Output).

### 2. `phantasma.service` (systemd)

Cria o ficheiro de serviço para o assistente correr em *background*.

vim /etc/systemd/system/phantasma.service
Cola o seguinte conteúdo (já inclui as correções de `PATH` e prioridade `Nice`):

[Unit]
Description=Bumblebee Voice Assistant
After=network-online.target sound.target

[Service]
Type=simple
User=user
Group=group
WorkingDirectory=/root/scripts/phantasma

# Define o HOME e o PATH (para pyenv, piper, sox, mpg123)
Environment="HOME=/root"
Environment="PATH=/root/.pyenv/shims:/usr/local/bin:/usr/bin:/sbin:/bin"

# Define a prioridade do CPU e Disco como a mais baixa
Nice=19
IOSchedulingClass=idle

# Executa o python de dentro da venv
ExecStart=/root/scripts/phantasma/venv/bin/python -u /root/scripts/phantasma/assistant.py

Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target

---

## Execução

Após criares todos os ficheiros (`.py`, `config.py`, `.service`):

**1. Recarrega o systemd:**
systemctl daemon-reload

**2. Ativa e Inicia o Serviço:**
systemctl enable --now phantasma.service

**3. Vê os Logs (para debug):**
journalctl -u phantasma -f

---

## Utilização

### 1. Comandos de Voz

1.  Diz a *hotword*: **"Bumblebee"**.
2.  Espera pelo *snippet* de música e pela saudação (ex: "Estou a postos!").
3.  Faz o teu pedido (ex: "como vai estar o tempo amanhã?", "memoriza que o meu gato se chama Bimby", "põe música").

### 2. Comandos via CLI (`phantasma-cli.sh`)

Usa o *script* `phantasma-cli.sh` para enviar comandos pela API:

**Ajuda (Dinâmica):**
./phantasma-cli.sh -h

**Comando "diz":**
./phantasma-cli.sh diz olá, isto é um teste

**Comando para o Ollama (com RAG):**
./phantasma-cli.sh quem é o primeiro-ministro de portugal

**Comando para Skills (Ex: Tocar Música):**
./phantasma-cli.sh põe uma música

### 3. Adicionar Novas Skills

Para adicionar uma nova funcionalidade (ex: "abrir o portão"):

1.  Cria um novo ficheiro em `/root/scripts/phantasma/skills/` (ex: `skill_portao.py`).
2.  Define os `TRIGGERS` (ex: `["abre o portão", "abrir portão"]`) e o `TRIGGER_TYPE` ("startswith" ou "contains").
3.  Cria a função `handle(user_prompt_lower, user_prompt_full)` que executa a lógica.
4.  Reinicia o serviço (`systemctl restart phantasma`). O assistente irá carregar a nova *skill* automaticamente.

## ⚙️ Integração de Dispositivos Domésticos (Skills de IoT)

O Phantasma utiliza *skills* dedicadas (`skill_xiaomi.py`, `skill_tuya.py`) para o controlo **100% local** dos dispositivos, cumprindo a filosofia **offline-first** do projeto.

### ❗ CRÍTICO: Reserva de DHCP

Para que o controlo local funcione de forma fiável, é **obrigatório** definir uma **Reserva de DHCP (IP Estático)** no seu router para o **MAC Address** de cada dispositivo. Se o IP mudar, a *skill* falhará.

---

## 1. Skill Tuya (SmartLife)

Esta *skill* permite o controlo local de dispositivos Tuya/SmartLife (tomadas, exaustores, luzes).

### A. Dependências

É necessário instalar a biblioteca Python para controlo local. Use o terminal (ou abra o ficheiro com o seu editor de eleição, como **Vim**, para inspecionar):

pip install tinytuya

### B. Obter Chaves (Device ID e Local Key)

O controlo local Tuya exige o **Device ID (`id`)** e a **Local Key (`key`)** de cada dispositivo. O método mais fiável é através da **Plataforma de Desenvolvimento Tuya IoT** e da ferramenta `tuya-cli wizard`.

1.  Crie um projeto em [https://iot.tuya.com/](https://iot.tuya.com/) e ligue-o à sua app SmartLife (via scan de QR Code).
2.  Execute a ferramenta de linha de comandos `tuya-cli wizard` e forneça as chaves de API e Segredo do seu projeto para extrair a **Local Key** de 16 ou 32 caracteres.

### C. Estrutura do `config.py`

Defina os seus dispositivos no dicionário `TUYA_DEVICES` em `config.py`. O Phantasma usa o nome do dispositivo para determinar o código **DPS** (Data Point Switch).

| Dispositivo | DPS ON/OFF | Protocolo Status | Observação |
| :--- | :---: | :---: | :--- |
| Exaustor, Tomada, Desumidificador | 1 | 3.3 | DPS 1 é o padrão para switches. |
| Lâmpada (Luz) | 20 | 3.3 | DPS 20 é o padrão para dispositivos de iluminação mais avançados (Cor, Brilho, etc.). |
| Sensor de T/H | N/A | 3.1 | Leitura de status usa o protocolo mais antigo. |

**Exemplo Completo do `config.py`:**

# config.py

TUYA_DEVICES = {
    # Exaustores e Tomadas (DPS 1)
    "exaustor 1": {
        "ip": "10.0.0.107",       
        "id": "ID_DO_EXAUSTOR_1_32_CHARS",
        "key": "CHAVE_LOCAL_16_CHARS" 
    },
    
    # Lâmpadas (DPS 20)
    "luz da sala": {
        "ip": "10.0.0.118",       
        "id": "ID_DA_LUZ_32_CHARS",
        "key": "CHAVE_LOCAL_16_CHARS"
    },
    
    # Sensores de Temperatura/Humidade (Leitura de status v3.1)
    "sensor do quarto": {
        "ip": "10.0.0.123",
        "id": "ID_DO_SENSOR_32_CHARS",
        "key": "CHAVE_LOCAL_16_CHARS"
    }
}
## 2. Skill Xiaomi (Miio)

Esta *skill* integra dispositivos Mi Home (aspiradores, Yeelight, etc.) através do protocolo **Miio**.

### A. Dependências

Instale a biblioteca de código aberto `python-miio` para a integração:

pip install python-miio
### B. Obter Token

O controlo Miio exige o **Token** (o equivalente à Local Key da Tuya).

O **Token (32 caracteres)** é obtido usando a ferramenta `micloud` ou scripts de extração de terceiros, autenticando-se na nuvem da Xiaomi. Lembre-se de especificar o servidor correto (`de`, `us`, `cn`, etc.) durante a extração.

### C. Estrutura do `config.py`

Defina os seus dispositivos no dicionário `MIIO_DEVICES` em `config.py`.

**Exemplo Completo do `config.py`:**

# config.py

MIIO_DEVICES = {
    # Exemplo: Aspirador Robot (usa a classe ViomiVacuum)
    "robot da sala": {
        "ip": "10.0.0.X",
        "token": "SEU_TOKEN_32_CHARS_ASPIRADOR" # <-- Token de 32 caracteres
    },
    
    # Exemplo: Lâmpada Yeelight (usa a classe Yeelight)
    "luz da cabeceira": {
        "ip": "10.0.0.Y",
        "token": "SEU_TOKEN_32_CHARS_LUZ"
    }
}
## 3. Execução e Controlo do Sistema Phantasma

O projeto Phantasma é executado como um serviço Python. O controlo dos dispositivos é acionado através de comandos de linha de comandos ou, tipicamente, por uma interface de voz/mensagem externa que interage com os *skills*.

### A. Estrutura de Execução

Recomenda-se a utilização de um ambiente virtual Python (venv) para isolar as dependências e iniciar o serviço principal (`phantasma_core.py`).

1. **Ativar o Ambiente Virtual:**

source venv/bin/activate

### B. Comandos de Controlo de Dispositivos (Interação Direta)

Embora o Phantasma seja concebido para ser ativado por voz ou *scripts* externos, pode testar o controlo de dispositivos diretamente através do *core* se este expuser uma API ou *endpoint*. Para a filosofia *local-first*, o controlo baseia-se na identificação do dispositivo configurado em `config.py`.

* **Exemplo de comando Tuya (Exaustor):** O core envia um comando para ligar/desligar o **DPS 1**.
* **Exemplo de comando Miio (Robot):** O core invoca o método `start_clean` da classe `ViomiVacuum`.

Este *core* (e a sua arquitetura *skill*) é o motor que traduz o comando de utilizador (ex: "Liga exaustor 1") nas chamadas de controlo local `tinytuya` ou `python-miio`.

---

## 4. Resolução de Problemas Comuns (Troubleshooting) 🛠️

* **Falha de Conexão (Tuya/Miio):** Quase sempre devido a uma falha na **Reserva de DHCP** (o IP do dispositivo mudou) ou a um **Token/Chave Local** incorreto. Verifique a tabela de DHCP do seu *router* e volte a extrair as chaves se necessário.
* **Controlo Não Funciona:** Verifique se as dependências (`tinytuya`, `python-miio`) estão instaladas no ambiente virtual ativo e se o `phantasma_core.py` está a ser executado.

### Notas finais:
Este bot originalmente era (e ainda é) previsto ter o nome phantasma, mas ainda não foi possível com os testes executados uma hotword funcional, nem sequer usar o sistema para hotwords originalmente previsto que não tinha o requisito de necessitar de uma licença como com o openwakeword, mas era ter falso-positivos com a palavra OH, ou ter o modelo completamente surdo com qualquer outra palavra, acabei por acatar e seguir com o pvporcupine, que irá necessitar da ativação de uma chave (gratuito para um dispositivo).
O código deste modelo e até idealização do projeto, e até mesmo este readme é fortemente gerado pelo Google Gemini.
Como equipamento, estou a usar um HP Mini G4, com 16GB de RAM e um Jabra SPEAK 410 como dispositivo de audio.

## Licença

O código-fonte deste projeto (os ficheiros `.py`, `.sh`, etc.) é licenciado sob a **Licença MIT**, como detalhado no ficheiro `LICENSE`.

Este projeto depende de *software* de terceiros com as suas próprias licenças, incluindo:

* **pvporcupine (Picovoice):** Esta é uma biblioteca proprietária. A sua utilização está sujeita aos termos de serviço da Picovoice e requer uma `ACCESS_KEY` pessoal.
* **Ollama (MIT)**
* **OpenAI Whisper (MIT)**
