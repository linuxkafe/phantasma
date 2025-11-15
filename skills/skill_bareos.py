import subprocess
import re
import config

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
# Triggers simples, a lógica de intenção está no 'handle'
TRIGGERS = ["backups", "bareos"]

# --- Constantes da Skill ---
# RECIPIENT_EMAIL = "mail@linuxkafe.com" # <-- 2. REMOVIDO
# O comando a executar, como pediste
BAREOS_COMMAND = 'echo "status director" | sudo bconsole'

def send_email_alert(error_output):
    """
    Tenta enviar um email de alerta usando o sendmail local.
    """
    
    # 3. VERIFICA SE O EMAIL ESTÁ DEFINIDO
    if not config.ALERT_EMAIL:
        print("ERRO (skill_bareos): ALERT_EMAIL não definido em config.py. Email não enviado.")
        return
        
    print(f"A enviar alerta de Bareos para {config.ALERT_EMAIL}...") # 4. USA A VARIÁVEL
    try:
        subject = "Alerta de Falha - Backups Bareos (Phantasma)"
        body = (
            "O assistente Phantasma detectou uma falha nos backups.\n\n"
            "Foi pedida uma verificação e o output não foi o esperado.\n\n"
            "--- Output do bconsole ---\n"
            f"{error_output}\n"
            "---------------------------\n"
        )
        
        # -t = Lê os cabeçalhos (To, From, Subject) da mensagem
        msg = f"To: {config.ALERT_EMAIL}\nFrom: phantasma-bot@linuxkafe.com\nSubject: {subject}\n\n{body}" # 4. USA A VARIÁVEL
        
        # Usamos Popen para injetar o email no sendmail
        p = subprocess.Popen(['sendmail', '-t'], stdin=subprocess.PIPE, text=True)
        stdout, stderr = p.communicate(msg) # Envia a msg
        
        if p.returncode == 0:
            print("Alerta enviado com sucesso via sendmail.")
        else:
            print(f"ERRO (skill_bareos): sendmail retornou código {p.returncode}. Stderr: {stderr}")

    except FileNotFoundError:
        print("ERRO (skill_bareos): Comando 'sendmail' não encontrado.")
        print("NOTA: Verifique se 'sendmail' está instalado e se /usr/sbin está no PATH do phantasma.service.")
    except Exception as e:
        print(f"ERRO (skill_bareos): Falha ao enviar email: {e}")

def handle(user_prompt_lower, user_prompt_full):
    """
    Verifica o estado do bareos e envia email em caso de falha.
    """
    
    # 1. Verifica se a intenção é mesmo "como estão..."
    if "como estão" not in user_prompt_lower:
        return None # Ignora se for outra frase (ex: "apaga os backups")
        
    # 1.5. VERIFICAÇÃO DE SEGURANÇA (ADICIONADA)
    if not config.ALERT_EMAIL:
         print("ERRO (skill_bareos): ALERT_EMAIL não está definido em config.py.")
         return "É precisa de definir o ALERT_EMAIL no ficheiro config.py antes que eu possa usar esta skill."
         
    print("A verificar o estado dos backups (Bareos)...")
    
    try:
        # 2. Executa o comando
        # Usamos shell=True pela simplicidade da pipeline que pediste
        result = subprocess.run(
            BAREOS_COMMAND,
            shell=True,
            capture_output=True,
            text=True,
            timeout=20 # Timeout de 20s para o bconsole
        )
        
        output = result.stdout.strip()
        
        # 3. Analisa o Output
        
        # Caso 1: Sem output
        if not output:
            print("Bareos: Comando não retornou output.")
            return "O sistema de backups não tem mensagens."
            
        # Caso 2: Tudo OK (contém "No" - ex: "No Errors" ou "No ...")
        # Usamos regex para procurar "No" como uma palavra inteira, case-insensitive
        if re.search(r'\bno\b', output, re.IGNORECASE):
            print(f"Bareos: Output OK. Output: {output}")
            return "Parece que está tudo ok com os backups."
        
        # Caso 3: Falha (contém output, mas não contém "No")
        print(f"Bareos: FALHA. Output: {output}")
        
        # Envia o email de alerta
        send_email_alert(output)
        
        # 4. USA A VARIÁVEL
        return f"Existe uma falha que é melhor verificar. Já agora, aproveitei para enviar um email com o erro para {config.ALERT_EMAIL}."

    except subprocess.TimeoutExpired:
        print("ERRO (skill_bareos): Timeout ao executar bconsole.")
        return "Chefe, o comando dos backups demorou demasiado tempo a responder."
        
    except Exception as e:
        print(f"ERRO CRÍTICO (skill_bareos): {e}")
        return f"Ocorreu um erro ao tentar executar o comando bconsole: {e}"
