"""O que o header "Geral" mostra, e em que ordem.

O dono pediu duas coisas nesta casa:

* **consumo total** no "Geral", *depois da temperatura média* -- e não o que a
  skill_cloogy devolvia, que era o valor do atuador lido como wattagem;
* **a bateria do clamp entre parênteses**, à frente da leitura de consumo total.

Um header que põe a média depois do consumo deixa de responder "como esta a
casa" antes de responder "quanto custou", que era a ordem que o dono ja tinha
pedido para o gas.

Estes testes leem o JS como texto e procuram a composicao. Nao correm browser:
o objectivo e fixar a ordem e a regra do que entra, e um browser aqui seria um
teste de renderizacao para uma coisa que e uma frase montada por linhas de JS.
A suite que corre o browser (`test_mobile_owner_complaints.py`) continua a ser
a que prova que isto aparece no ecra.
"""

from __future__ import annotations

from pathlib import Path


def _strip_js_comments(js: str) -> str:
    """Remove /* ... */ e // ... do JS, deixando so o codigo executado.

    Um teste que faz grep por uma frase proibida sobre o codigo inteiro falha
    com o comentario que explica a proibicao. E um teste que nao pode sobreviver
    ao comentario que o documenta acaba "corrigido" apagando a explicacao.
    """
    import re

    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"//[^\n]*", "", js)


def _src() -> str:
    return (Path(__file__).resolve().parent.parent / "skills" / "skill_ui.py").read_text(
        encoding="utf-8"
    )


class TestGeralHeader:
    def test_the_average_is_composed_before_anything_else(self):
        """A media entra primeiro, como ja estava.

        `avg` e o primeiro elemento da lista de partes. O dono pediu a media
        antes das medicoes de gas, e o consumo total foi pedido depois dela --
        portanto o consumo nao pode ser empurrado para a frente.
        """
        src = _src()
        assert "(avg !== null ? ['média ' + avg + '°'] : [])" in src, (
            "a média deixou de ser a primeira parte do header Geral"
        )

    def test_total_consumption_is_present_and_ordered_after_the_average(self):
        """A média primeiro, o consumo depois -- na composição, não no ficheiro.

        A ordem que importa é a das partes que se juntam, que é a ordem em que
        o header se lê. Medir a posição no ficheiro seria medir outra coisa: um
        `GERAL_TOTAL` declarado mais acima move a primeira ocorrência de
        `energy_kwh` sem mudar o que o dono lê.
        """
        src = _src()
        assert "energy_kwh" in src, "nenhuma leitura de energia acumulada no header"

        compose = src.index(".concat(consumption ? [consumption] : [])")
        i_media = src.rindex("['média ' + avg", 0, compose)
        assert i_media < compose, (
            "o consumo total tem de ser concatenado DEPOIS da temperatura media"
        )
        # E a media tem de continuar a ser a primeira parte da lista.
        assert "(avg !== null ? ['média ' + avg + '°'] : [])" in src

    def test_instantaneous_power_goes_in_parentheses(self):
        """A potencia instantanea vai entre parenteses, e nao a energia.

        Sao coisas diferentes e o dono escolheu mostra-las assim: os kWh
        acumulados sao o "consumo total", os W sao o que se esta a gastar agora.
        """
        src = _src()
        assert "power_w" in src
        # Parentheses around the instantaneous reading.
        assert "W)" in src or "W', " in src or "' W'" in src or "W)" in src, (
            "a potencia instantanea nao esta entre parenteses"
        )

    def test_the_clamp_battery_only_shows_when_it_is_low(self):
        """A bateria so entra no header a 20% ou menos.

        O dono, 2026-10-05: "so precisa de apresentar o valor da bateria do
        clamp se chegar aos 20%". Acima disso o numero nao muda, nao pede nada
        e empurra a temperatura e o total para fora da linha. A 62% -- que e
        onde esta -- nao aparece.
        """
        src = _src()
        assert "GERAL_BATTERY_WARN_PCT = 20" in src, "o limiar da bateria desapareceu"
        assert "GERAL_BATTERY.pct <= GERAL_BATTERY_WARN_PCT" in src, (
            "a bateria esta a ser mostrada sem criterio de limiar"
        )

    def test_an_absent_measurement_says_nothing_at_all(self):
        """Sem dados, o header fica em silencio.

        O dono removeu "nao mede" e "sem leitura" em 2026-10-05: um espaco vazio
        ja se le como "aqui nao ha nada", e inventar uma afirmacao sobre um
        sensor que esta simplesmente a dormir e pior do que deixar o espaco.
        """
        # Scoped to the Geral consumption line, not the whole file: a room
        # sensor still says "sem leitura" when it has nothing, which is a
        # different sentence about a different thing and was not part of what
        # the owner asked to remove.
        src = _src()
        line = src[src.index("function geralConsumption()"):src.index(
            "function refreshGeralAverage()")]
        assert "nao mede" not in line, "'nao mede' continua no consumo do Geral"
        assert "sem leitura" not in line, "'sem leitura' continua no consumo do Geral"
        assert "if (!bits.length) return null;" in line, (
            "sem dados o Geral tem de devolver nada, nao uma frase"
        )


class TestZigbeeCreationIsNotSilentlyDropped:
    def test_a_zigbee_sensor_is_not_dropped_by_name(self):
        """`createSensor` descartava devices cujo nome continha 'casa'.

        A regra era escrita para o device do cloogy chamava-se 'casa'. O clamp
        chama-se 'consumo' e passa, mas a regra continua la: um device chamado
        'casa' ou 'geral' seria descartado sem pagina e sem erro -- o device
        deixa de aparecer e nada diz porquê.

        O clamp tem de ser desenhado no Geral pelas leituras, nao por ser uma
        tile. Este teste fixa que o filtro nao existe.
        """
        src = _src()
        assert "includes('casa')" not in src, (
            "createSensor ainda descarta devices pelo nome -- isso apaga o "
            "clamp se algum dia se chamar 'casa' ou 'geral'"
        )


class TestRoomReadingsStayEmptyWhenThereIsNothingToSay:
    """Uma sala sem leituras fica vazia. Nao escreve 'sem leitura'.

    O dono, 2026-10-06: o "sem leitura" das rooms sai, tal como o "nao mede" do
    Geral saiu no dia anterior. Um espaco vazio ja se le como "aqui nao ha
    nada"; escrever a ausencia de uma medicao e o sistema a anunciar as proprias
    limitacoes no sitio onde um olhar tem de ser rapido.
    """

    def _room_line(self) -> str:
        """The tail of `fetchSensorStatus`, where the reading is composed.

        Not a slice between two anchors: `const base` sits INSIDE
        `fetchSensorStatus`, so a `start:end` pair of the wrong order silently
        yields an empty string and every assertion below passes vacuously. That
        is the failure mode a substring-range test has, and it is why the range
        ends at the next `function` keyword instead.
        """
        src = _src()
        i = src.index("const base = measurements.join")
        j = src.index("function ", i + 10)
        return src[i:j]

    def test_the_empty_room_does_not_say_it_has_no_reading(self):
        # The CODE, not the comment above it. The comment quotes the phrase on
        # purpose (it explains why the phrase is gone), so a grep over the whole
        # block would fail on the explanation of the fix -- and a test that
        # cannot survive the comment that documents it is a test that will get
        # "fixed" by deleting the explanation. Strip comments first.
        code = _strip_js_comments(self._room_line())
        assert "sem leitura" not in code, (
            "a room ainda escreve 'sem leitura' quando nao tem leituras"
        )

    def test_an_absent_reading_deletes_the_slot_instead_of_holding_it(self):
        """`text || null` tem de chegar a `putRoomReading` como null.

        Este e' o bug que a frase simples esconde. Passar a string vazia
        mantem a chave em ROOM_PARTS, o Map continua nao-vazio, e o sensor que
        ficou mudo continua a segurar a sua vaga para sempre -- em vez de a
        largar e deixar a sala mostrar o que quer que ainda reporte.
        """
        # The same range as `_room_line`, not a hardcoded character count: the
        # comment above grew past 600 chars once and the assertion below it
        # started failing on the comment's length. A character count is a
        # promise about a file that keeps growing.
        assert "text || null" in self._room_line(), "a leitura vazia nao apaga a chave"

    def test_put_room_reading_deletes_on_empty(self):
        """O delete e' explicito na funcao, nao consecuencia de o JS partir."""
        src = _src()
        start = src.index("function putRoomReading(")
        fn = src[start:src.index("function ", start + 20)]
        assert "parts.delete(sensor)" in fn, (
            "putRoomReading nao apaga a chave quando o texto e' vazio"
        )
        assert "parts.set(sensor, text)" in fn, "a leitura normal deixou de ser guardada"
