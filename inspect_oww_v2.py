import sys
from openwakeword.model import Model

print("A carregar modelo 'alexa' para inspeção...")
try:
    oww = Model(wakeword_models=["alexa"])
except Exception as e:
    print(f"Erro ao carregar o modelo: {e}")
    sys.exit(1)

print("\n========= Inspecionando 'oww' (o objeto principal) =========\n")

# Vamos procurar em todos os atributos do objeto principal
for attr in dir(oww):
    if not attr.startswith('_'): # Ignorar atributos privados
        try:
            value = getattr(oww, attr)
            # Estamos à procura de um número inteiro (provavelmente o chunk size)
            if isinstance(value, int): 
                print(f"  -> oww.{attr} = {value} (Tipo: {type(value)})")
        except Exception:
            pass # Ignora métodos ou atributos que não podemos ler

print("\n========= Inspeção Concluída =========\n")
print("Cola-me este output. Estamos à procura de um atributo com um número,")
print("como 'chunk_size', 'hop_length', 'hop_samples', 'frame_samples', etc.")

# Vamos também verificar o preprocessor outra vez, mas só para inteiros
print("\n========= Re-inspecionando 'oww.preprocessor' (só por inteiros) =========\n")
for attr in dir(oww.preprocessor):
    if not attr.startswith('_'):
        try:
            value = getattr(oww.preprocessor, attr)
            if isinstance(value, int):
                print(f"  -> oww.preprocessor.{attr} = {value} (Tipo: {type(value)})")
        except Exception:
            pass
