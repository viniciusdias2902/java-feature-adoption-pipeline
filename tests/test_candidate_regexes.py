"""Auditoria dos regexes de candidato contra o MOTOR DO PRÓPRIO GIT.

O `-G` compila POSIX ERE via regcomp — não PCRE, não o `re` do Python. Três armadilhas
já custaram recall em produção (D-010, D-012): `\\[` dentro de classe, `\\t` como letra t,
e formas sintáticas não previstas (`instanceof final`, `case double[][]`, binding `_`).

Cada linha positiva aqui veio de um erro real de recall. Não remova nenhuma.
"""

from __future__ import annotations

import subprocess

import pytest

from jmod.config import load_study

STUDY = load_study()

# (feature, linha, deve_casar) — positivas vieram de falhas de recall reais em produção.
CASOS = [
    ("switch_expression", "case 1 -> 10;", True),
    ("switch_expression", "default -> 0;", True),
    ("switch_expression", "yield 42;", True),
    ("switch_expression", "yield(x);", True),
    ("switch_expression", "case 1: break;", False),
    ("pattern_instanceof", "if (o instanceof ServiceDependency that) return;", True),
    ("pattern_instanceof", "if (e instanceof final ServerPlayer s) {", True),  # TestMod3
    ("pattern_instanceof", "if (x instanceof Map.Entry<String, String> entry) {", True),
    ("pattern_instanceof", "if (x instanceof int[] arr) {}", True),
    ("pattern_switch", "case Integer i -> f();", True),
    ("pattern_switch", "case double[][] doublePixels -> {", True),  # qupath
    ("pattern_switch", "case PathAnnotationObject _ -> g();", True),  # qupath
    ("pattern_switch", "case null, Foo f -> h();", True),
    ("pattern_switch", "case Point(int x, int y) -> k();", True),
    ("pattern_switch", "case 1 -> f();", False),
    ("pattern_switch", "case FOO: break;", False),
    ("record_pattern", "if (o instanceof Point(int x, int y)) {}", True),
    ("record_pattern", "case Line(Point a, Point b) -> g();", True),
    ("record_pattern", "if (o instanceof Point p) {}", False),
]


def _git_matches(regex: str, line: str, tmp_path) -> bool:
    probe = tmp_path / "probe.txt"
    probe.write_text(line + "\n", encoding="utf-8")
    result = subprocess.run(  # noqa: S603
        ["git", "grep", "--no-index", "-qE", regex, "probe.txt"],
        capture_output=True,
        check=False,
        cwd=tmp_path,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    ("feature_id", "linha", "esperado"),
    CASOS,
    ids=[f"{f}-{'pos' if e else 'neg'}-{i}" for i, (f, _, e) in enumerate(CASOS)],
)
def test_regex_contra_o_motor_do_git(feature_id: str, linha: str, esperado: bool, tmp_path) -> None:
    feature = STUDY.feature(feature_id)
    assert feature.candidate_regex, f"{feature_id} deveria usar -G"
    got = _git_matches(feature.candidate_regex, linha, tmp_path)
    if esperado:
        assert got, f"RECALL: {feature_id} deixaria de ver {linha!r}"
    # negativas são desejáveis, não obrigatórias: falso candidato é custo, não erro


def test_features_com_literal_nao_tem_regex_e_vice_versa() -> None:
    for f in STUDY.features:
        assert (f.candidate_literal is None) != (f.candidate_regex is None), f.id
