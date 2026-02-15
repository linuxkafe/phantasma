# vim skill_calculator.py

import re

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"

# Gatilhos expandidos para apanhar operações no meio da frase
TRIGGERS = [
    "quanto é", "quantos são", "calcula", 
    "dividir", "a dividir", "dividido", 
    "vezes", "multiplicado", 
    "mais", "somado", 
    "menos", "subtraído",
    "+", "-", "*", "x", "/"
]

# Apenas estes prefixos serão removidos do início da string.
PREFIXES_TO_CLEAN = [
    "quanto é", "quantos são", "calcula", "diz-me", "sabes", 
    "o que achas de", "o que achas", "o que te parece"
]

def handle(user_prompt_lower, user_prompt_full):
    """ Tenta calcular uma expressão matemática detetada em qualquer parte da frase. """
    
    # 1. Limpeza inteligente: Remove apenas prefixos de pergunta
    expression_str = user_prompt_lower
    for prefix in PREFIXES_TO_CLEAN:
        if expression_str.startswith(prefix):
            expression_str = expression_str[len(prefix):].strip()
            break
    
    try:
        # Limpeza de pontuação de fim de frase (evita que o "." final da frase estrague o número)
        expr = expression_str.rstrip(".?!")
        
        # --- Tratamento de Números em Formato Português (PT) ---
        
        # 1. Remover pontos de milhar: 1.108 -> 1108
        # Um ponto é considerado separador de milhar se estiver entre dígitos e seguido de exatamente 3 algarismos.
        expr = re.sub(r"(\d)\.(\d{3})(?!\d)", r"\1\2", expr)
        
        # 2. Converter vírgula decimal para ponto (padrão Python): 1,5 -> 1.5
        expr = expr.replace(",", ".")
        
        # Conversão de palavras numéricas para dígitos
        word_to_num = {
            r'\bum\b': '1', r'\bdois\b': '2', r'\btrês\b': '3', r'\bquatro\b': '4',
            r'\bcinco\b': '5', r'\bseis\b': '6', r'\bsete\b': '7', r'\boito\b': '8',
            r'\bnove\b': '9', r'\bdez\b': '10', r'\bzero\b': '0'
        }
        for word_re, num in word_to_num.items():
            expr = re.sub(word_re, num, expr)

        # Substituição de operadores naturais por matemáticos
        expr = expr.replace("x", "*").replace("vezes", "*").replace("multiplicado por", "*")
        expr = expr.replace("a dividir por", "/").replace("dividido por", "/").replace("dividir por", "/")
        expr = expr.replace("a dividir", "/").replace("dividido", "/").replace("dividir", "/")
        expr = expr.replace("mais", "+").replace("somado a", "+")
        expr = expr.replace("menos", "-").replace("subtraído de", "-")

        # Limpeza final: mantém apenas números, operadores e parênteses
        allowed_chars_pattern = r"[^0-9\.\+\-\*\/\(\)\s]"
        cleaned_expr = re.sub(allowed_chars_pattern, "", expr)

        # Verificação de Segurança
        if not cleaned_expr.strip() or not any(char.isdigit() for char in cleaned_expr):
            return None
            
        print(f"A tentar calcular localmente: '{cleaned_expr}'")

        # Cálculo
        result = eval(cleaned_expr)
        
        # Formatação do resultado para o utilizador (voltar ao formato PT com vírgula)
        if result == int(result): 
            result = int(result)
        else:
            result = round(result, 2)
            
        result_str = str(result).replace(".", ",")
        return f"O resultado é {result_str}."

    except ZeroDivisionError:
        return "Não é possível dividir por zero."
    except Exception as e:
        # Em caso de erro de sintaxe, deixa o Ollama tentar resolver
        return None
