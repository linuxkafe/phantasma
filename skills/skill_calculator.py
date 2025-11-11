import re

TRIGGER_TYPE = "startswith"
TRIGGERS = ["quanto é", "quantos são"]

def handle(user_prompt_lower, user_prompt_full):
    """ Tenta calcular uma expressão matemática. """
    
    # Extrai apenas a expressão
    expression_str = re.sub(r'|'.join(TRIGGERS), '', user_prompt_lower, 1).strip()
    
    print(f"A tentar calcular localmente: '{expression_str}'")
    
    try:
        expr = re.sub(r"[?!]", "", expression_str)
        expr = expr.replace(",", ".")
        word_to_num = {
            r'\bum\b': '1', r'\bdois\b': '2', r'\btrês\b': '3', r'\bquatro\b': '4',
            r'\bcinco\b': '5', r'\bseis\b': '6', r'\bsete\b': '7', r'\boito\b': '8',
            r'\bnove\b': '9', r'\bdez\b': '10', r'\bzero\b': '0'
        }
        for word_re, num in word_to_num.items():
            expr = re.sub(word_re, num, expr)

        expr = expr.replace("x", "*").replace("vezes", "*")
        expr = expr.replace("a dividir por", "/").replace("dividido por", "/").replace("a dividir", "/").replace("dividido", "/")
        expr = expr.replace("mais", "+")
        expr = expr.replace("menos", "-")

        allowed_chars_pattern = r"[^0-9\.\+\-\*\/\(\)\s]"
        cleaned_expr = re.sub(allowed_chars_pattern, "", expr)

        if not cleaned_expr.strip():
            return None
            
        result = eval(cleaned_expr)
        if result == int(result): result = int(result)
        result_str = str(result).replace(".", ",")
        return f"O resultado é {result_str}."

    except ZeroDivisionError:
        return "Não é possível dividir por zero."
    except Exception as e:
        print(f"Cálculo local falhou (SyntaxError?): {e}")
        return None # Deixa o Ollama tratar
