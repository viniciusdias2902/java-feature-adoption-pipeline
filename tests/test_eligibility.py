"""Fase 8: as regras que decidem quando um módulo PODIA usar cada feature."""

from __future__ import annotations

from jmod.config import load_study
from jmod.events.eligibility import preview_eligibility, stable_eligibility
from jmod.events.upgrades import Sample, build_intervals

FIM = "2026-09-01T00:00:00+00:00"
STUDY = load_study()
RECORD = STUDY.feature("record")  # estável 16, preview 14-15
VAR = STUDY.feature("var")  # estável 10, sem preview


def ivs(*samples: tuple[str, str, int | None] | tuple[str, str, int | None, bool]):
    return build_intervals(
        [
            Sample(t[0], f"2020-{t[1]}T12:00:00+00:00", t[2], t[3] if len(t) > 3 else False)
            for t in samples
        ]
    )


def stable(intervals, feature=RECORD):
    return stable_eligibility(intervals, feature, persistence_days=30, end_of_observation=FIM)


# ------------------------------------------------------------------ básico
def test_upgrade_abre_elegibilidade_no_sha_da_transicao() -> None:
    ps = stable(ivs(("a", "01-01", 8), ("b", "06-01", 17)))
    assert len(ps) == 1
    assert (ps[0].start_sha, ps[0].end_date) == ("b", None)
    assert ps[0].start_date.startswith("2020-06-01"), "committer date da transição, inclusive"


def test_baseline_insuficiente_nao_gera_periodo() -> None:
    assert stable(ivs(("a", "01-01", 8), ("b", "06-01", 11))) == []


def test_estado_inicial_ja_elegivel() -> None:
    ps = stable(ivs(("a", "01-01", 17)))
    assert len(ps) == 1
    assert ps[0].start_sha == "a"


# ------------------------------------------------------------------ pausa
def test_downgrade_real_pausa_e_upgrade_retoma() -> None:
    ps = stable(ivs(("a", "01-01", 17), ("b", "03-01", 8), ("c", "07-01", 17)))
    assert len(ps) == 2, "o relógio pausa no downgrade e retoma no novo upgrade"
    assert ps[0].end_date.startswith("2020-03-01")
    assert ps[1].start_sha == "c"
    assert ps[1].end_date is None


def test_blip_transient_para_baixo_nao_interrompe() -> None:
    """17 -> 8 por dez dias -> 17: o blip não é downgrade, elegibilidade contínua."""
    ps = stable(ivs(("a", "01-01", 17), ("b", "03-01", 8), ("c", "03-11", 17)))
    assert len(ps) == 1
    assert ps[0].end_date is None


def test_blip_transient_para_cima_nao_cria_elegibilidade() -> None:
    """8 -> 17 por dez dias -> 8: não houve upgrade, não há elegibilidade."""
    ps = stable(ivs(("a", "01-01", 8), ("b", "03-01", 17), ("c", "03-11", 8)))
    assert ps == []


# ------------------------------------------------------------------ gaps
def test_gap_desconhecido_interrompe_mesmo_sem_mudanca() -> None:
    """17 -> ? -> 17: manter elegibilidade através do gap seria interpolar."""
    ps = stable(ivs(("a", "01-01", 17), ("b", "03-01", None), ("c", "07-01", 17)))
    assert len(ps) == 2
    assert ps[0].end_date.startswith("2020-03-01")
    assert ps[1].start_sha == "c"


def test_estado_curto_depois_de_gap_vale_imediatamente() -> None:
    """Após reset por gap, o próximo estado conhecido vale sem exigir persistência."""
    ps = stable(
        ivs(
            ("a", "01-01", 17),
            ("b", "03-01", None),
            ("c", "07-01", 17),
            ("d", "07-15", 8),
            ("e", "09-01", 17),
        )
    )
    assert ps[0].end_date.startswith("2020-03-01")
    assert ps[1].start_sha == "c", "o 17 pós-gap abre período na hora"


def test_comeco_desconhecido_nao_e_elegivel() -> None:
    ps = stable(ivs(("a", "01-01", None), ("b", "06-01", 17)))
    assert len(ps) == 1
    assert ps[0].start_sha == "b"


# ------------------------------------------------------------------ preview
def test_preview_exige_versao_na_janela_e_flag() -> None:
    ps = preview_eligibility(ivs(("a", "01-01", 15, True)), RECORD)
    assert len(ps) == 1
    assert ps[0].kind == "preview"


def test_preview_sem_flag_nao_conta() -> None:
    assert preview_eligibility(ivs(("a", "01-01", 15)), RECORD) == []


def test_versao_estavel_nao_gera_periodo_preview() -> None:
    assert preview_eligibility(ivs(("a", "01-01", 16, True)), RECORD) == []


def test_preview_fecha_quando_flag_desliga() -> None:
    ps = preview_eligibility(ivs(("a", "01-01", 15, True), ("b", "04-01", 15, False)), RECORD)
    assert len(ps) == 1
    assert ps[0].end_date.startswith("2020-04-01")


def test_feature_sem_janela_preview_nunca_tem_periodo() -> None:
    assert preview_eligibility(ivs(("a", "01-01", 10, True)), VAR) == []


def test_elegibilidade_estavel_ignora_flag_preview() -> None:
    ps = stable(ivs(("a", "01-01", 10, True), ("b", "06-01", 10, False)), VAR)
    assert len(ps) == 1, "toggle de preview não fecha período estável"
    assert ps[0].end_date is None
