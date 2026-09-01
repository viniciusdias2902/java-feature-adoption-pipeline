"""O parser precisa tolerar variação de cabeçalho e nunca descartar em silêncio."""

from __future__ import annotations

from pathlib import Path

import pytest

from jmod.config import load_study
from jmod.ingest.seart import SeartFormatError, read_csv, screen

HEADER = (
    "name,isFork,commits,stargazers,createdAt,lastCommit,"
    "defaultBranch,totalLinesOfCode,isArchived,mainLanguage"
)
OK = "acme/lib,false,5000,1200,2014-05-02,2026-06-01,main,80000,false,Java"


def _csv(tmp: Path, *lines: str) -> Path:
    p = tmp / "seart.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_le_linha_valida(tmp_path: Path) -> None:
    rows = list(read_csv(_csv(tmp_path, HEADER, OK)))
    assert len(rows) == 1
    r = rows[0]
    assert r.full_name == "acme/lib"
    assert (r.stars, r.commits, r.loc_java) == (1200, 5000, 80000)
    assert r.is_fork is False


def test_aceita_alias_alternativo_de_coluna(tmp_path: Path) -> None:
    alt = "nameWithOwner,fork,commitsCount,starsCount,created_at,pushedAt"
    rows = list(read_csv(_csv(tmp_path, alt, "acme/lib,false,5000,1200,2014-05-02,2026-06-01")))
    assert rows[0].full_name == "acme/lib"
    assert rows[0].stars == 1200


def test_cabecalho_irreconhecivel_falha_alto_mostrando_colunas(tmp_path: Path) -> None:
    with pytest.raises(SeartFormatError, match="colunas obrigatórias ausentes"):
        list(read_csv(_csv(tmp_path, "foo,bar", "1,2")))


@pytest.mark.parametrize(
    ("linha", "motivo"),
    [
        ("acme/lib,true,5000,1200,2014-05-02,2026-06-01,main,80000,false,Java", "fork"),
        ("acme/lib,false,5000,1200,2014-05-02,2026-06-01,main,80000,true,Java", "archived"),
        ("acme/lib,false,5000,50,2014-05-02,2026-06-01,main,80000,false,Java", "below_min_stars"),
        ("acme/lib,false,10,1200,2014-05-02,2026-06-01,main,80000,false,Java", "below_min_commits"),
        ("acme/lib,false,5000,1200,2014-05-02,2026-06-01,main,900,false,Java", "below_min_loc"),
        ("acme/lib,false,5000,1200,2024-05-02,2026-06-01,main,80000,false,Java", "too_young"),
        ("acme/lib,false,5000,1200,2014-05-02,2019-01-01,main,80000,false,Java", "inactive"),
        ("acme/lib,false,5000,1200,2014-05-02,2026-06-01,main,80000,false,Kotlin", "not_java"),
    ],
)
def test_filtros_produzem_motivo_de_exclusao(tmp_path: Path, linha: str, motivo: str) -> None:
    row = next(iter(read_csv(_csv(tmp_path, HEADER, linha))))
    assert screen(row, load_study()) == motivo


def test_projeto_valido_nao_e_excluido(tmp_path: Path) -> None:
    row = next(iter(read_csv(_csv(tmp_path, HEADER, OK))))
    assert screen(row, load_study()) is None


def test_screen_nunca_levanta_em_data_ausente(tmp_path: Path) -> None:
    linha = "acme/lib,false,5000,1200,,,main,80000,false,Java"
    row = next(iter(read_csv(_csv(tmp_path, HEADER, linha))))
    assert screen(row, load_study()) == "missing_created_at"
