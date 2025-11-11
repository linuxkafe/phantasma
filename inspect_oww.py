import sys
from openwakeword.model import Model

print("A carregar modelo 'alexa' para inspeção...")
try:
    oww = Model(wakeword_models=["alexa"])
except Exception as e:
    print(f"Erro ao carregar o modelo: {e}")
    sys.exit(1)

print("\n========= Inspecionando 'oww.preprocessor' =========\n")

# Vamos imprimir todos os atributos do pré-processador
# O nome que queremos DEVE estar nesta lista
for attr in dir(oww.preprocessor):
    if not attr.startswith('_'): # Ignorar atributos privados
        try:
            value = getattr(oww.preprocessor, attr)
            print(f"  -> oww.preprocessor.{attr} = {value}")
        except Exception:
            pass # Ignora métodos ou atributos que não podemos ler

print("\n========= Inspeção Concluída =========\n")
print("Por favor, cola todo o output acima (especialmente a lista de atributos).")
