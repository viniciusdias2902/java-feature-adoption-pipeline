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


def test_exposure_days_merges_overlapping_module_periods() -> None:
    # 23 módulos elegíveis desde a mesma data não multiplicam a exposição (união).
    periods = [Period(d("2023-03-14"), None) for _ in range(23)]
    assert exposure_days(periods, d("2023-03-14"), d("2026-09-01")) == 1267
    # sobreposição parcial: [jan, jun) ∪ [abr, out) = jan..out
    periods = [Period(d("2020-01-01"), d("2020-06-01")), Period(d("2020-04-01"), d("2020-10-01"))]
    assert exposure_days(periods, d("2020-01-01"), d("2020-12-31")) == 274
    # intervalos disjuntos continuam somando
    periods = [Period(d("2020-01-01"), d("2020-02-01")), Period(d("2020-03-01"), d("2020-04-01"))]
    assert exposure_days(periods, d("2020-01-01"), d("2020-12-31")) == 62


# ------------------------------------- reprocessamento parcial: parar e usar o que deu
def test_feature_com_deteccao_corrigida_so_entra_de_projeto_reminerado() -> None:
    """Reprocessar a população leva horas; parar no meio não pode gerar dataset inválido.

    A saída é por linha: a feature cuja detecção mudou (D-016) só entra vinda de projeto
    já reminerado, e as demais entram sempre. Cada projeto reprocessado só acrescenta
    linhas, então parar a qualquer momento deixa um dataset menor porém correto (D-018).
    """
    from jmod.dataset.build_survival import feature_is_current

    versions = {"novo/proj": "0.2.0", "velho/proj": "0.1.0"}

    # switch_expression exige 0.2.0
    assert feature_is_current(versions, "novo/proj", "switch_expression")
    assert not feature_is_current(versions, "velho/proj", "switch_expression")
    # projeto que nunca foi minerado também não entrega a feature
    assert not feature_is_current(versions, "ausente/proj", "switch_expression")

    # as outras sete não dependem da correção e entram de qualquer versão
    for feature in ("record", "var", "text_block", "sealed", "pattern_instanceof",
                    "record_pattern", "pattern_switch"):
        assert feature_is_current(versions, "velho/proj", feature), feature
        assert feature_is_current(versions, "novo/proj", feature), feature


def test_versao_maior_que_o_minimo_continua_valendo() -> None:
    """O piso é `>=`, não igualdade: uma versão futura não invalida o que já foi minerado."""
    from jmod.dataset.build_survival import feature_is_current

    assert feature_is_current({"p/q": "0.10.0"}, "p/q", "switch_expression")
    assert feature_is_current({"p/q": "1.0.0"}, "p/q", "switch_expression")
    assert not feature_is_current({"p/q": "0.1.9"}, "p/q", "switch_expression")


def test_projeto_interrompido_no_meio_fica_fora_dos_desfechos(tmp_path) -> None:
    """Mineração morta no meio deixa dado parcial e o `ok` da rodada anterior de pé.

    Incluir esse projeto o faria parecer não-adotante das features que não chegaram a ser
    mineradas — o mesmo erro da D-016, por outra porta. A Fase 11 passa a exigir que a
    mineração MAIS RECENTE do projeto tenha terminado bem (D-020).
    """
    from jmod.db import Store
    from jmod.provenance import PIPELINE_VERSION

    def linha(pid: str, run: str, status: str, quando: str) -> dict:
        return {
            "run_id": run,
            "phase": "mine",
            "project_id": pid,
            "status": status,
            "started_at": quando,
            "ended_at": quando if status != "started" else None,
            "error": None,
            "pipeline_version": PIPELINE_VERSION,
        }

    with Store(tmp_path / "s.sqlite") as store:
        store.init_schema()
        store.upsert(
            "run_log",
            [
                linha("completo/proj", "r1", "ok", "2026-09-01T00:00:00+00:00"),
                linha("completo/proj", "r2", "ok", "2026-09-06T00:00:00+00:00"),
                # interrompido: ok antigo, mas a rodada mais recente ficou em `started`
                linha("parcial/proj", "r1", "ok", "2026-09-01T00:00:00+00:00"),
                linha("parcial/proj", "r2", "started", "2026-09-06T00:00:00+00:00"),
            ],
        )
        aptos = {
            r["project_id"]
            for r in store.query(
                "SELECT r.project_id FROM run_log r WHERE r.phase='mine' AND r.status='ok' "
                "  AND r.started_at = (SELECT MAX(r2.started_at) FROM run_log r2 "
                "                      WHERE r2.phase='mine' AND r2.project_id = r.project_id) "
                "GROUP BY r.project_id"
            )
        }

    assert aptos == {"completo/proj"}
