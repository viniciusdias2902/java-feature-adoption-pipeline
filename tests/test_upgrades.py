"""Fase 7: as regras que decidem o que conta como upgrade."""

from __future__ import annotations

from jmod.events.upgrades import Sample, build_intervals, derive_events

FIM = "2026-09-01T00:00:00+00:00"


def s(sha: str, day: str, v: int | None) -> Sample:
    return Sample(sha, f"2020-{day}T12:00:00+00:00", v)


def eventos(samples: list[Sample], persistencia: int = 30):
    return derive_events(
        build_intervals(samples), persistence_days=persistencia, end_of_observation=FIM
    )


# ------------------------------------------------------------------ intervalos
def test_observacoes_iguais_colapsam() -> None:
    ivs = build_intervals([s("a", "01-01", 8), s("b", "02-01", 8), s("c", "03-01", 8)])
    assert len(ivs) == 1
    assert ivs[0].end_date is None, "vigente até o fim da observação"


def test_gap_desconhecido_nao_e_emendado() -> None:
    """8 -> ? -> 8: dois intervalos de 8 com o buraco explícito entre eles."""
    ivs = build_intervals([s("a", "01-01", 8), s("b", "02-01", None), s("c", "03-01", 8)])
    assert [i.java_version for i in ivs] == [8, None, 8]


def test_intervalo_fechado_carrega_sha_e_data_de_fim() -> None:
    ivs = build_intervals([s("a", "01-01", 8), s("b", "06-01", 17)])
    assert ivs[0].end_sha == "b"
    assert ivs[0].end_date.startswith("2020-06-01")


# ------------------------------------------------------------------ eventos
def test_upgrade_persistente() -> None:
    evs = eventos([s("a", "01-01", 8), s("b", "06-01", 17)])
    assert len(evs) == 1
    ev = evs[0]
    assert (ev.kind, ev.from_version, ev.to_version, ev.sha) == ("upgrade", 8, 17, "b")


def test_downgrade_persistente() -> None:
    evs = eventos([s("a", "01-01", 17), s("b", "06-01", 8)])
    assert evs[0].kind == "downgrade"


def test_upgrade_revertido_em_dez_dias_e_transient() -> None:
    """O caso de validação do plano: upgrade revertido em 10 dias não é upgrade."""
    evs = eventos([s("a", "01-01", 8), s("b", "03-01", 17), s("c", "03-11", 8)])
    assert [e.kind for e in evs] == ["transient"]
    assert evs[0].to_version == 17
    assert evs[0].persisted_days == 10


def test_reversao_ao_estavel_nao_e_downgrade() -> None:
    """No episódio 8 -> 17(curto) -> 8, a volta ao 8 não pode virar evento."""
    evs = eventos([s("a", "01-01", 8), s("b", "03-01", 17), s("c", "03-11", 8)])
    assert all(e.kind != "downgrade" for e in evs)


def test_transicao_encadeada_colapsa_no_estavel_final() -> None:
    """8 -> 17 (5 dias) -> 21 estável: o evento real é 8 -> 21."""
    evs = eventos([s("a", "01-01", 8), s("b", "03-01", 17), s("c", "03-06", 21)])
    kinds = {(e.kind, e.from_version, e.to_version) for e in evs}
    assert ("upgrade", 8, 21) in kinds
    assert ("transient", 8, 17) in kinds
    assert ("upgrade", 8, 17) not in kinds


def test_ultimo_intervalo_curto_persiste_ate_o_head() -> None:
    """Regra: 'persista >= 30 dias OU até o HEAD'. Upgrade na véspera do freeze conta."""
    evs = eventos([s("a", "01-01", 8), Sample("b", "2026-08-25T12:00:00+00:00", 17)])
    assert evs[0].kind == "upgrade"


def test_upgrade_atraves_de_gap_e_emitido_na_observacao() -> None:
    """8 -> ? -> 17: o evento sai no commit que observa 17, com origem 8."""
    evs = eventos([s("a", "01-01", 8), s("b", "02-01", None), s("c", "04-01", 17)])
    assert len(evs) == 1
    assert (evs[0].from_version, evs[0].to_version, evs[0].sha) == (8, 17, "c")


def test_gap_sem_mudanca_nao_gera_evento() -> None:
    evs = eventos([s("a", "01-01", 8), s("b", "02-01", None), s("c", "04-01", 8)])
    assert evs == []


def test_primeira_observacao_nao_e_evento() -> None:
    assert eventos([s("a", "01-01", 17)]) == []


def test_comeco_desconhecido_depois_conhecido_nao_e_evento() -> None:
    """? -> 11: a primeira baseline conhecida é estado inicial, não upgrade."""
    evs = eventos([s("a", "01-01", None), s("b", "02-01", 11)])
    assert evs == []


def test_persisted_days_do_upgrade_vigente() -> None:
    evs = eventos([s("a", "01-01", 8), s("b", "06-01", 17)])
    assert evs[0].persisted_days > 2000, "do evento até o fim da observação em 2026"


def test_ordem_de_entrada_nao_importa() -> None:
    amostras = [s("c", "07-01", 21), s("a", "01-01", 8), s("b", "04-01", 17)]
    evs = eventos(amostras)
    assert [(e.from_version, e.to_version) for e in evs] == [(8, 17), (17, 21)]
