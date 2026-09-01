"""A configuração é a especificação do estudo: erro nela invalida tudo a jusante."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jmod.config import ConfigError, load_features, load_study

BASE = Path(__file__).resolve().parents[1] / "config"


def test_carrega_configuracao_real() -> None:
    study = load_study()
    assert study.seed == 20260901
    assert str(study.freeze_date) == "2026-09-01"
    assert len(study.features) == 8
    assert study.eligibility["preview_counts"] is False


def test_catalogo_cobre_as_oito_features() -> None:
    ids = {f.id for f in load_features()}
    assert ids == {
        "var",
        "switch_expression",
        "text_block",
        "record",
        "pattern_instanceof",
        "sealed",
        "pattern_switch",
        "record_pattern",
    }


@pytest.mark.parametrize(
    ("feature_id", "stable"),
    [
        ("var", 10),
        ("switch_expression", 14),
        ("text_block", 15),
        ("record", 16),
        ("pattern_instanceof", 16),
        ("sealed", 17),
        ("pattern_switch", 21),
        ("record_pattern", 21),
    ],
)
def test_versoes_estaveis_conferem_com_as_jeps(feature_id: str, stable: int) -> None:
    assert load_study().feature(feature_id).stable == stable


def test_elegibilidade_ignora_preview_por_padrao() -> None:
    record = load_study().feature("record")
    assert record.eligible_at(16, preview_counts=False)
    assert not record.eligible_at(15, preview_counts=False)  # 15 é preview
    assert record.eligible_at(15, preview_counts=True)
    assert not record.eligible_at(13, preview_counts=True)


def _write(tmp: Path, body: str) -> Path:
    p = tmp / "study.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _valid_body() -> str:
    return (BASE / "study.toml").read_text(encoding="utf-8")


def test_rejeita_janela_invertida(tmp_path: Path) -> None:
    body = _valid_body().replace(
        'window_start      = "2018-03-20"', 'window_start      = "2027-01-01"'
    )
    with pytest.raises(ConfigError, match="window_start"):
        load_study(_write(tmp_path, body))


def test_rejeita_sustained_incoerente(tmp_path: Path) -> None:
    body = _valid_body().replace("sustained_min_files       = 2", "sustained_min_files       = 9")
    with pytest.raises(ConfigError, match="sustained_min_files"):
        load_study(_write(tmp_path, body))


def test_rejeita_escopo_primario_fora_dos_rastreados(tmp_path: Path) -> None:
    body = _valid_body().replace(
        'primary_scope           = "production"', 'primary_scope           = "inexistente"'
    )
    with pytest.raises(ConfigError, match="primary_scope"):
        load_study(_write(tmp_path, body))


def test_rejeita_feature_sem_estrategia_de_candidato(tmp_path: Path) -> None:
    p = tmp_path / "features.toml"
    p.write_text(
        textwrap.dedent("""
        [[feature]]
        id = "x"
        name = "X"
        jep = 1
        stable = 10
        preview = []
        node = "n"
    """),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="candidate_literal"):
        load_features(p)


def test_rejeita_preview_posterior_ao_estavel(tmp_path: Path) -> None:
    p = tmp_path / "features.toml"
    p.write_text(
        textwrap.dedent("""
        [[feature]]
        id = "x"
        name = "X"
        jep = 1
        stable = 14
        preview = [15]
        node = "n"
        candidate_literal = "x"
    """),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="preview"):
        load_features(p)
