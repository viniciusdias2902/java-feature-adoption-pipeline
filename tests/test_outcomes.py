"""Fase 11: cada fronteira das definições congeladas de desfecho."""

from __future__ import annotations

from jmod.outcomes.adoption_events import Period, Point, derive_outcome, exposure_days

FREEZE = "2026-09-01T00:00:00+00:00"
KW = dict(
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


# ------------------------------------------------------------------ exposição
def test_exposicao_soma_so_o_tempo_elegivel() -> None:
    periods = [Period(d("2020-01-01"), d("2020-03-01")), Period(d("2020-06-01"), None)]
    # jan+fev = 60 dias; março-maio pausado; junho até 2020-08-30 = 90 dias
    assert exposure_days(periods, d("2020-01-01"), d("2020-08-30")) == 150


def test_exposicao_com_pausa_e_o_diferencial_do_estudo() -> None:
    """17 -> downgrade -> 17: o relógio para no meio."""
    periods = [Period(d("2020-01-01"), d("2020-02-01")), Period(d("2021-01-01"), None)]
    out = derive_outcome(periods, [pt("2021-01-31", 1)], **KW)
    assert out.event == 1
    assert out.time_to_first_use_days == 31 + 30, "31 dias de jan/2020 + 30 de jan/2021"


# ------------------------------------------------------------------ first use
def test_first_use_e_censura() -> None:
    out = derive_outcome([Period(d("2020-01-01"), None)], [pt("2020-03-01", 2)], **KW)
    assert (out.event, out.first_use_date[:10]) == (1, "2020-03-01")

    censored = derive_outcome([Period(d("2020-01-01"), None)], [], **KW)
    assert censored.event == 0
    assert censored.censor_reason == "no_use_by_freeze"
    assert censored.time_to_first_use_days > 2000, "exposição até o freeze"


def test_uso_antes_da_elegibilidade_e_anomalia_explicita() -> None:
    out = derive_outcome([Period(d("2021-01-01"), None)], [pt("2020-06-01", 1)], **KW)
    assert out.event == 0
    assert out.censor_reason == "pre_eligibility_use"


def test_nunca_elegivel() -> None:
    out = derive_outcome([], [], **KW)
    assert out.censor_reason == "never_eligible"


# ------------------------------------------------------------------ sustained
def test_sustained_composto_exige_presenca_e_massa() -> None:
    series = [pt("2020-01-01", 1), pt("2020-04-01", 4, files=3)]
    out = derive_outcome([Period(d("2019-01-01"), None)], series, **KW)
    assert out.sustained == 1


def test_sumiu_dentro_da_janela_nao_e_sustained() -> None:
    series = [pt("2020-01-01", 1), pt("2020-03-01", 0), pt("2020-05-01", 5, files=4)]
    out = derive_outcome([Period(d("2019-01-01"), None)], series, **KW)
    assert out.sustained == 0, "zerou dentro dos 180 dias"


def test_presenca_sem_massa_nao_e_sustained() -> None:
    series = [pt("2020-01-01", 1)]  # 1 ocorrência, 1 arquivo
    out = derive_outcome([Period(d("2019-01-01"), None)], series, **KW)
    assert out.sustained == 0
    assert out.sustained_simple == 1, "a definição simples diverge — é a sensibilidade"


def test_janela_aberta_no_freeze_e_inclassificavel() -> None:
    series = [pt("2026-08-01", 5, files=3)]  # 31 dias antes do freeze
    out = derive_outcome([Period(d("2026-01-01"), None)], series, **KW)
    assert out.sustained is None
    assert out.censor_reason == "sustained_window_open"
    assert out.event == 1, "o first use em si continua valendo"


# ------------------------------------------------------------------ abandono
def test_abandono_apos_180_dias_de_silencio() -> None:
    series = [pt("2020-01-01", 2), pt("2021-01-01", 0)]
    out = derive_outcome([Period(d("2019-01-01"), None)], series, **KW)
    assert out.abandoned == 1
    assert out.abandoned_date[:10] == "2021-01-01"


def test_zerou_ha_pouco_nao_e_abandono_ainda() -> None:
    series = [pt("2020-01-01", 2), pt("2026-08-01", 0)]
    out = derive_outcome([Period(d("2019-01-01"), None)], series, **KW)
    assert out.abandoned is None, "31 dias de silêncio < 180: censura"


def test_readicao_zera_o_abandono() -> None:
    series = [pt("2020-01-01", 2), pt("2021-01-01", 0), pt("2022-01-01", 3, files=2)]
    out = derive_outcome([Period(d("2019-01-01"), None)], series, **KW)
    assert out.abandoned == 0


# ------------------------------------------------------------------ fronteiras
def test_fronteira_179_vs_181_dias() -> None:
    base = [Period(d("2019-01-01"), None)]
    quase = derive_outcome(base, [pt("2020-01-01", 5, 3), pt("2026-03-10", 0)], **KW)
    assert quase.abandoned is None, "175 dias de silêncio"
    passou = derive_outcome(base, [pt("2020-01-01", 5, 3), pt("2026-03-01", 0)], **KW)
    assert passou.abandoned == 1, "184 dias de silêncio"


def test_fronteira_2_vs_3_ocorrencias() -> None:
    base = [Period(d("2019-01-01"), None)]
    duas = derive_outcome(base, [pt("2020-01-01", 1), pt("2020-05-01", 2, 2)], **KW)
    tres = derive_outcome(base, [pt("2020-01-01", 1), pt("2020-05-01", 3, 2)], **KW)
    assert duas.sustained == 0
    assert tres.sustained == 1
