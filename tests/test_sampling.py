"""O sorteio precisa ser determinístico e cobrir toda célula de estrato não-vazia."""

from __future__ import annotations

from typing import Any

from jmod.ingest.sampling import baseline_band, stratified_draw, stratum_key, stratum_matrix


def _row(
    pid: str, build: str = "maven", modules: int = 1, external: int = 0, baseline: int | None = 17
) -> dict[str, Any]:
    return {
        "project_id": pid,
        "build_system": build,
        "module_count": modules,
        "has_external_parent": external,
        "head_baseline": baseline,
    }


def test_faixas_de_baseline() -> None:
    assert baseline_band(8) == "legacy"
    assert baseline_band(11) == "legacy"
    assert baseline_band(14) == "mid"
    assert baseline_band(17) == "modern"
    assert baseline_band(None) == "unknown"


def test_chave_de_estrato_combina_as_quatro_dimensoes() -> None:
    key = stratum_key(_row("a", "gradle_kts", 5, 1, 8))
    assert key == "gradle_kts|multi|external_parent|legacy"


def test_sorteio_e_deterministico() -> None:
    rows = [_row(f"p{i}", baseline=8 + i % 12) for i in range(60)]
    a = stratified_draw(rows, seed=20260901, n=15)
    b = stratified_draw(list(reversed(rows)), seed=20260901, n=15)
    assert [d.project_id for d in a] == [d.project_id for d in b], "ordem de entrada não importa"


def test_seed_diferente_muda_o_sorteio() -> None:
    rows = [_row(f"p{i}", baseline=8 + i % 12) for i in range(60)]
    a = {d.project_id for d in stratified_draw(rows, 20260901, 15)}
    b = {d.project_id for d in stratified_draw(rows, 1, 15)}
    assert a != b


def test_cobre_toda_celula_antes_de_repetir() -> None:
    rows = (
        [_row(f"m{i}", "maven", 1, 0, 8) for i in range(20)]
        + [_row(f"g{i}", "gradle_groovy", 3, 0, 17) for i in range(20)]
        + [_row("k0", "gradle_kts", 1, 1, None)]
    )
    draws = stratified_draw(rows, 20260901, 3)
    assert len({d.stratum for d in draws}) == 3, "3 sorteios devem cair em 3 estratos distintos"


def test_nao_sorteia_alem_da_populacao() -> None:
    rows = [_row("a"), _row("b")]
    assert len(stratified_draw(rows, 1, 15)) == 2


def test_rank_e_sequencial_a_partir_de_um() -> None:
    rows = [_row(f"p{i}") for i in range(10)]
    assert [d.rank for d in stratified_draw(rows, 1, 5)] == [1, 2, 3, 4, 5]


def test_sem_duplicatas() -> None:
    rows = [_row(f"p{i}", baseline=8 + i % 3) for i in range(30)]
    picked = [d.project_id for d in stratified_draw(rows, 20260901, 15)]
    assert len(picked) == len(set(picked))


def test_matriz_conta_por_estrato() -> None:
    rows = [_row("a"), _row("b"), _row("c", "gradle_groovy")]
    m = stratum_matrix(rows)
    assert m["maven|mono|internal_parent|modern"] == 2
    assert m["gradle_groovy|mono|internal_parent|modern"] == 1
