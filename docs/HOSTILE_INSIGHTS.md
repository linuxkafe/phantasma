# Hostile Insights Registry

Lessons from hostile analysis across this project.
Add entries when hostile analysis reveals assumptions that proved false or critical.

## Format

## [Category] - [Brief Title]
- **Task**: [link or description]
- **Insight**: [what we learned]
- **Origin**: [Plan/Build/Verify/Review phase that revealed it]
- **Impact**: [what happened when ignored / what was mitigated]
- **Applied To**: [how approach changed since]
- **Date**: YYYY-MM-DD

---

## [Epistemics] — "o serviço caiu" vs "a conta morreu": sondar o servidor, não o sintoma
- **Task**: T051 — skill_chacon inoperante
- **Insight**: A mensagem do dono foi "a cloud deixou de estar disponível". A
  cloud estava **no ar**: TLS válido, `101 Switching Protocols`, o protocolo DIO
  a responder — e a rejeitar a *conta*. Duas sondas provaram-no: o
  `POST /api/session/login` devolve HTTP 200 com `Invalid username/password`
  (um serviço em baixo não devolve 200 com JSON estruturado), e credenciais
  inventadas devolvem a **mesma** string byte a byte. Esse segundo teste é o que
  fecha o caso: um oráculo de resposta constante prova que nenhuma sondagem
  adicional distingue "password errada" de "conta apagada".
- **Origin**: Reconhecimento (Phase 0), antes de escrever código
- **Impact**: Sem isto, a resposta would've sido implementar um bypass de LAN
  contra a premissa errada — e a família DIO não responde localmente por
  desenho, portanto o bypass não teria funcionado. O custo de pararmos para
  medir pagou-se.
- **Applied To**: Antes de aceitar "X está em baixo", distinguir o *transporte*
  do *autor*. `TLS ok` + `protocolo responde` + `credencial rejeitada` = conta.
- **Date**: 2026-09-29

## [Process] — `except ImportError` com `return None` transforma uma falha em silêncio
- **Task**: T051 — skill_chacon
- **Insight**: O `pyproject.toml` declarava `dio-chacon-wifi-api` desde T035, mas
  o venv de dev nunca a recebeu. O `except ImportError` punha um flag e
  `handle()` devolvia `None` para sempre — a skill parecia *desligada*, não
  *avariada*. O `T035-learn.md` registou isto como comportamento pretendido
  ("returns None so Tuya can handle the request"), o que transformou uma falha
  de instalação num requisito de design. Um `print` dentro do `except` não
  chega ao journald de forma filtrável nem a nenhum teste.
- **Origin**: Reconhecimento, confirmado por execução (`DioChaconApi = None`)
- **Impact**: A skill nunca funcionou em dev, durante ~10 meses após a
  dependência ser declarada. Nenhum teste, nenhuma linha de log.
- **Applied To**: O motivo do import failure tem de ser um **atributo de módulo**
  (`IMPORT_ERROR`) legível por teste, não um `print`. Acresce-se fallback das
  classes de excepção, porque os `except` do handler as nomeiam e um import
  falhado deixa-as unbound — o `NameError` esconderia a causa real.
- **Date**: 2026-09-29

## [Verification] — Declarar uma dependência não é tê-la; testar o import, não o `pyproject`
- **Task**: T051 — skill_chacon
- **Insight**: `pyproject.toml` é uma *intenção*; o venv é o *facto*. Só
  `import dio_chacon_wifi_api` diz se a dependência existe. O teste
  `test_dependency_is_importable` existe por isso, e a verificação por mutação
  provou que tem dentes: revertida a skill ao comportamento original
  (`print` + `None`), o teste falha.
- **Origin**: Build/Verify
- **Impact**: Um `grep` no `pyproject` daria "verde" para uma skill morta.
- **Applied To**: Testes que importam a dependência real, e sempre confirmar
  que o teste falha quando o bug é reintroduzido.
- **Date**: 2026-09-29

## [Scope] — Parar antes de disparar frames para hardware não identificado
- **Task**: T051 — fallback local do Chacon
- **Insight**: `10.0.0.115` (MAC Espressif) era o único candidato a plug DIO na
  rede, com uma porta que faz SYN-ACK e depois silêncio. Escrever controlo
  local contra ele seria atirar frames de um protocolo de fabricante para um
  dispositivo doméstico não identificado, porque "Espressif" é uma
  *plausibilidade*, não uma *identificação*. O pedido era explícito
  ("entrar no dispositivo directamente") e ainda assim o custo de errar era alto.
- **Origin**: Phase 1 Hostile Analysis
- **Impact**: Evitado. O trabalho parou num pedido de identificação (MAC/IP) em
  vez de gerar código não testado contra hardware real.
- **Applied To**: Antes de automação que **actua** em hardware físico, exigir
  identificação positiva do alvo.address, ou autorização explícita para sondar.
- **Date**: 2026-09-29

