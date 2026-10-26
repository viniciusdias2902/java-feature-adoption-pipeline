"""Fase 15b: as propriedades que a análise de sensibilidade assume.

A sensibilidade só é interpretável se variar o limiar nunca inverter o sentido: apertar
não pode criar sustentadas, e alargar a janela não pode classificar mais pares. São essas
monotonicidades que os testes fixam, sobre `derive_outcome`, mais o contrato de que a
primeira variante de `VARIANTES` é a do estudo.
"""

from __future__ import annotations

from jmod.analysis.sensitivity import VARIANTES
from jmod.outcomes.adoption_events import Period, Point, derive_outcome

FREEZE = "2026-09-01T00:00:00+00:00"
BASE = dict(
    freeze=FREEZE,
    sustained_window_days=180,
    sustained_min_occurrences=3,
    sustained_min_files=2,
    abandonment_window_days=180,
)


def d(day: str) -> str:
    return f"{day}T12:00:00+00:00"


def pt(day: str, count: int, files: int = 1, sha: str = "s") -> Point:
    return Point(sha, d(day), count, files)


ELEG = [Period(d("2020-01-01"), None)]
#: Três ocorrências em dois arquivos, 60 dias após o primeiro uso: sustentada no estudo.
SERIE = [pt("2020-02-01", 1, 1, "a"), pt("2020-04-01", 3, 2, "b")]


def test_variante_do_estudo_e_a_primeira() -> None:
    """A tabela compara tudo contra a primeira; se ela deixar de ser a do estudo, o
    rho e a leitura do nível passam a medir outra coisa."""
    nome, mudanca = VARIANTES[0]
    assert mudanca == {}, "a primeira variante não pode sobrescrever limiar algum"
    assert nome.startswith("estudo")


def test_apertar_o_limiar_nunca_cria_sustentada() -> None:
    estudo = derive_outcome(ELEG, SERIE, **BASE)
    estrita = derive_outcome(
        ELEG, SERIE, **{**BASE, "sustained_min_occurrences": 5, "sustained_min_files": 3}
    )
    assert estudo.sustained == 1
    assert estrita.sustained == 0


def test_afrouxar_o_limiar_nunca_remove_sustentada() -> None:
    serie = [pt("2020-02-01", 1, 1, "a"), pt("2020-04-01", 2, 1, "b")]
    estudo = derive_outcome(ELEG, serie, **BASE)
    frouxa = derive_outcome(
        ELEG, serie, **{**BASE, "sustained_min_occurrences": 2, "sustained_min_files": 1}
    )
    assert (estudo.sustained, frouxa.sustained) == (0, 1)


def test_janela_longa_deixa_mais_pares_sem_veredito() -> None:
    """Primeiro uso perto do freeze: a janela de 365 dias não fecha, a de 90 fecha."""
    serie = [pt("2026-06-01", 1, 1, "a")]
    curta = derive_outcome(ELEG, serie, **{**BASE, "sustained_window_days": 90})
    longa = derive_outcome(ELEG, serie, **{**BASE, "sustained_window_days": 365})
    assert curta.sustained == 0, "janela fechada no freeze: veredito emitido"
    assert longa.sustained is None, "janela aberta no freeze: sem veredito"
