"""Normalização de versão. O critério é rejeitar o impossível, não adivinhar."""

from __future__ import annotations

import pytest

from jmod.build.java_versions import MAX_KNOWN_RELEASE, is_known_release, parse_java_version


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [
        ("1.8", 8),
        ("1.7", 7),
        ("1.6", 6),
        ("1.5", 5),
        ("1.4", 4),
        ("1.1", 1),
        ("1.9", 9),
        ("1.10", 10),
        ("8", 8),
        ("11", 11),
        ("17", 17),
        ("21", 21),
        ("25", 25),
        (" 17 ", 17),
        ("'1.8'", 8),
        ('"11"', 11),
        ("JavaVersion.VERSION_1_8", 8),
        ("JavaVersion.VERSION_11", 11),
        ("JavaVersion.VERSION_21", 21),
        ("JavaLanguageVersion.of(17)", 17),
        ("JavaLanguageVersion.of('21')", 21),
    ],
)
def test_aceita_formas_validas(raw: str, esperado: int) -> None:
    assert parse_java_version(raw) == esperado


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "1.0",  # não é release de linguagem utilizável
        "0",
        "-1",
        "2",
        "3",
        "4",  # D-007: número solto abaixo de 5 é falso casamento
        "1.11",
        "1.99",  # a notação 1.x parou no 1.10
        str(MAX_KNOWN_RELEASE + 1),
        "99",
        "2024",
        "abc",
        "${java.version}",
        "latest",
        "VERSION_UNSPECIFIED",
        "1.8.0_292",  # versão de JDK, não de linguagem
    ],
)
def test_rejeita_formas_invalidas(raw: str | None) -> None:
    assert parse_java_version(raw) is None


def test_d007_valores_do_probe_seriam_rejeitados() -> None:
    """`java 2` apareceu no probe; a detecção real não pode aceitá-lo."""
    assert parse_java_version("2") is None
    assert parse_java_version("1.2") == 2, "mas `1.2` explícito é notação legítima"


def test_propriedade_nao_interpolada_nunca_vira_versao() -> None:
    assert parse_java_version("${maven.compiler.release}") is None


def test_is_known_release() -> None:
    assert is_known_release(8) and is_known_release(21)
    assert not is_known_release(None)
    assert not is_known_release(MAX_KNOWN_RELEASE + 1)
