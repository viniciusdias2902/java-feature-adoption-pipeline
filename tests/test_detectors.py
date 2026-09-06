"""Detectores estruturais.

O critério de conclusão da Fase 9 é **100% nas fixtures negativas**: código Java 8 válido
que contém a palavra da feature como identificador não pode gerar detecção alguma.
"""

from __future__ import annotations

import pytest

from jmod.features.detectors import detect, registered
from jmod.features.parser import has_errors, parse

# ------------------------------------------------------------------ positivas
POSITIVAS: dict[str, list[str]] = {
    "record": [
        "public record Point(int x, int y) {}",
        "class A { record Pair<K,V>(K k, V v) {} }",
        "record R(int a) { R { if (a < 0) throw new IllegalArgumentException(); } }",
    ],
    "var": [
        'class A { void m(){ var s = "x"; } }',
        "class A { void m(){ for (var e : list) {} } }",
        "class A { void m(){ var n = compute(); n.use(); } }",
    ],
    "text_block": [
        'class A { String s = """\n  hi\n  """; }',
        'class A { String q = """\n  {"a": 1}\n  """; }',
    ],
    "sealed": [
        "public sealed interface S permits A, B {}",
        "sealed class C permits D {}",
        "non-sealed class D extends C {}",
    ],
    "pattern_instanceof": [
        "class A { void m(Object o){ if (o instanceof String s) { s.length(); } } }",
        "class A { boolean m(Object o){ return o instanceof Integer i && i > 0; } }",
    ],
    "switch_expression": [
        "class A { int m(int d){ return switch(d){ case 1 -> 10; default -> 0; }; } }",
        "class A { int m(int d){ return switch(d){ case 1: yield 1; default: yield 0; }; } }",
        "class A { void m(int d){ switch(d){ case 1 -> f(); default -> g(); } } }",
    ],
    "pattern_switch": [
        'class A { String m(Object o){ return switch(o){ case Integer i -> "i"; '
        'case String s -> "s"; default -> "?"; }; } }',
    ],
    "record_pattern": [
        "class A { void m(Object o){ if (o instanceof Point(int x, int y)) {} } }",
        "class A { String m(Object o){ return switch(o){ "
        'case Line(Point(var x1, var y1), Point p2) -> "l"; default -> "?"; }; } }',
    ],
}

# ------------------------------------------------------------------ negativas
# Todo trecho abaixo é Java 8 VÁLIDO. Nenhum pode disparar o detector.
NEGATIVAS: dict[str, list[str]] = {
    "record": [
        "class record { int x; }",
        "class A { void record() {} }",
        "class A { void m(){ int record = 1; record++; } }",
        'class A { String s = "record Point(int x)"; }',
    ],
    "var": [
        "class A { void m(){ int var = 5; var++; } }",
        "class A { void m(){ String var = null; } }",
        "class A { void var() {} }",
        'class A { String s = "var x = 1"; }',
        "class A { void m(){ Object o = new Object(); } }",
    ],
    "text_block": [
        'class A { String s = "hi"; }',
        'class A { String s = "\\"quoted\\""; }',
        'class A { String s = "a" + "b" + "c"; }',
    ],
    "sealed": [
        "class A { void m(){ int sealed = 1; } }",
        "class A { void sealed() {} }",
        "class A { boolean permits = false; }",
    ],
    "pattern_instanceof": [
        "class A { void m(Object o){ if (o instanceof String) {} } }",
        "class A { void m(Object o){ if (o instanceof String) { String s = (String) o; } } }",
    ],
    "switch_expression": [
        "class A { void m(int d){ switch(d){ case 1: break; default: break; } } }",
        "class A { void m(int d){ switch(d){ case 1: f(); break; } } }",
    ],
    "pattern_switch": [
        'class A { String m(int d){ return switch(d){ case 1 -> "a"; default -> "b"; }; } }',
        "class A { void m(int d){ switch(d){ case 1: break; } } }",
    ],
    "record_pattern": [
        "class A { void m(Object o){ if (o instanceof Point p) {} } }",
        "class A { void m(){ Point p = new Point(1, 2); } }",
    ],
}


def _hits(feature: str, src: str) -> list:
    return detect(feature, parse(src.encode()), src.encode())


def test_todos_os_oito_detectores_registrados() -> None:
    assert registered() == set(POSITIVAS) == set(NEGATIVAS)
    assert len(registered()) == 8


@pytest.mark.parametrize(
    ("feature", "src"),
    [(f, s) for f, srcs in POSITIVAS.items() for s in srcs],
    ids=lambda v: v if isinstance(v, str) and len(v) < 22 else "",
)
def test_positiva_detecta(feature: str, src: str) -> None:
    assert _hits(feature, src), f"{feature} não detectou: {src!r}"


@pytest.mark.parametrize(
    ("feature", "src"),
    [(f, s) for f, srcs in NEGATIVAS.items() for s in srcs],
    ids=lambda v: v if isinstance(v, str) and len(v) < 22 else "",
)
def test_negativa_nao_detecta(feature: str, src: str) -> None:
    hits = _hits(feature, src)
    assert not hits, f"{feature} FALSO POSITIVO em {src!r}: {hits}"


def test_var_identificador_e_var_real_no_mesmo_arquivo() -> None:
    src = 'class A { void m(){ int var = 5; var s = "x"; } }'
    hits = _hits("var", src)
    assert len(hits) == 1, "só a segunda declaração usa inferência de tipo"


def test_switch_registra_subtipo() -> None:
    arrow_stmt = "class A { void m(int d){ switch(d){ case 1 -> f(); } } }"
    expr = "class A { int m(int d){ return switch(d){ case 1 -> 1; default -> 0; }; } }"
    assert _hits("switch_expression", arrow_stmt)[0].node_subtype == "arrow_statement"
    assert _hits("switch_expression", expr)[0].node_subtype == "expression_position"


def test_sealed_distingue_non_sealed() -> None:
    assert _hits("sealed", "sealed class C permits D {}")[0].node_subtype == "sealed"
    assert _hits("sealed", "non-sealed class D extends C {}")[0].node_subtype == "non_sealed"


def test_record_pattern_aninhado_e_marcado() -> None:
    src = (
        "class A { String m(Object o){ return switch(o){ "
        'case Line(Point(var a, var b), Point p) -> "l"; default -> "?"; }; } }'
    )
    subtipos = {h.node_subtype for h in _hits("record_pattern", src)}
    assert "nested" in subtipos


def test_hits_carregam_linha_e_posicao() -> None:
    src = "class A {\n  record P(int x) {}\n}"
    hit = _hits("record", src)[0]
    assert hit.line == 2
    assert hit.byte_start > 0


def test_sintaxe_desconhecida_nao_derruba_o_parser() -> None:
    """Um arquivo com construto futuro degrada localmente, nunca por inteiro."""
    src = "class A { void m(){ some!!invalid@@syntax; } record R(int x) {} }"
    tree = parse(src.encode())
    assert has_errors(tree)
    assert _hits("record", src), "o record válido continua detectável apesar do erro"


def test_switch_classico_aninhado_nao_e_expressao() -> None:
    """Pai != block não significa expressão: case, if sem chaves e label são statements."""
    casos = [
        "class A { void m(int a, int b){ switch(a){ case 1: switch(b){ case 2: break; } } } }",
        "class A { void m(int a){ if (a > 0) switch(a){ case 1: break; } } }",
        "class A { void m(int a){ done: switch(a){ case 1: break done; } } }",
    ]
    for src in casos:
        assert _hits("switch_expression", src) == [], src


# Tabela exaustiva de posição do switch (D-016). A gramática chama todo switch de
# `switch_expression`, então só a posição separa a feature do switch clássico:
# classificar exige conhecer *todos* os contêineres de statement. `constructor_body`
# faltava e fazia todo switch clássico dentro de um construtor virar falso positivo —
# o defeito atingiu 351 projetos da população. Caso real: mikera/vectorz,
# `ZeroArray.java:35`.
STATEMENT_CONTEXTS: dict[str, str] = {
    "construtor": "class A { A(int a){ switch(a){ case 1: break; } } }",
    "método": "class A { void m(int a){ switch(a){ case 1: break; } } }",
    "inicializador estático": "class A { static { switch(a){ case 1: break; } } }",
    "inicializador de instância": "class A { { switch(a){ case 1: break; } } }",
    "try": "class A { void m(int a){ try { switch(a){ case 1: break; } } catch (E e) {} } }",
    "finally": "class A { void m(int a){ try {} finally { switch(a){ case 1: break; } } } }",
    "synchronized": (
        "class A { void m(int a, Object o){ synchronized (o) { switch(a){ case 1: break; } } } }"
    ),
    "corpo de while sem chaves": (
        "class A { void m(int a){ while (a > 0) switch(a){ case 1: break; } } }"
    ),
    "corpo de for sem chaves": "class A { void m(int a){ for (;;) switch(a){ case 1: break; } } }",
    "corpo de for-each sem chaves": (
        "class A { void m(int[] xs){ for (int a : xs) switch(a){ case 1: break; } } }"
    ),
    "corpo de lambda": (
        "class A { void m(int a){ Runnable r = () -> { switch(a){ case 1: break; } }; } }"
    ),
}

EXPRESSION_CONTEXTS: dict[str, str] = {
    "declaração de variável": (
        "class A { void m(int a){ int v = switch(a){ case 1 -> 2; default -> 3; }; } }"
    ),
    "atribuição": (
        "class A { int v; void m(int a){ v = switch(a){ case 1 -> 2; default -> 3; }; } }"
    ),
    "campo": "class A { int v = switch(a){ case 1 -> 2; default -> 3; }; }",
    "return": "class A { int m(int a){ return switch(a){ case 1 -> 2; default -> 3; }; } }",
    "argumento": "class A { void m(int a){ f(switch(a){ case 1 -> 2; default -> 3; }); } }",
    "expressão binária": (
        "class A { void m(int a){ int v = 1 + switch(a){ case 1 -> 2; default -> 3; }; } }"
    ),
    "ternário": (
        "class A { void m(int a, boolean c){ int v = c ? switch(a){ case 1 -> 2;"
        " default -> 3; } : 0; } }"
    ),
    "cast": (
        "class A { void m(int a){ Object o = (Object) switch(a){ case 1 -> 2; default -> 3; }; } }"
    ),
    "parênteses": "class A { void m(int a){ int v = (switch(a){ case 1 -> 2; default -> 3; }); } }",
    "inicializador de array": (
        "class A { void m(int a){ int[] v = { switch(a){ case 1 -> 2; default -> 3; } }; } }"
    ),
    "condição de if": (
        "class A { void m(int a){ if (switch(a){ case 1 -> true; default -> false; }) f(); } }"
    ),
    "iterável do for-each": (
        "class A { void m(int a){ for (int x : switch(a){ case 1 -> xs; default -> ys; }) f(); } }"
    ),
}


@pytest.mark.parametrize("nome", sorted(STATEMENT_CONTEXTS))
def test_switch_classico_em_contexto_de_statement_nao_conta(nome: str) -> None:
    assert _hits("switch_expression", STATEMENT_CONTEXTS[nome]) == []


@pytest.mark.parametrize("nome", sorted(EXPRESSION_CONTEXTS))
def test_switch_em_posicao_de_expressao_conta(nome: str) -> None:
    hits = _hits("switch_expression", EXPRESSION_CONTEXTS[nome])
    assert [h.node_subtype for h in hits] == ["expression_position"]


def test_switch_classico_dentro_de_case_com_seta() -> None:
    """O switch clássico interno é statement; o externo, com setas, é a feature."""
    src = "class A { void m(int a){ switch(a){ case 1 -> { switch(a){ case 2: break; } } } } }"
    assert [h.node_subtype for h in _hits("switch_expression", src)] == ["arrow_statement"]


def test_switch_em_posicao_de_expressao_continua_contando() -> None:
    casos = [
        "class A { int m(int d){ return switch(d){ case 1: yield 1; default: yield 0; }; } }",
        "class A { int m(int d){ int x = switch(d){ case 1: yield 1;"
        " default: yield 0; }; return x; } }",
    ]
    for src in casos:
        assert _hits("switch_expression", src), src


def test_pattern_switch_com_tipos_primitivos_e_binding_anonimo() -> None:
    """Casos reais do qupath que escaparam do regex de candidatos (a AST sempre viu)."""
    casos = [
        "class A { void m(Object o){ switch(o){ case double[][] px -> f(px);"
        " default -> g(); } } void f(Object x){} void g(){} }",
        "class A { void m(Object o){ switch(o){ case String _ -> f();"
        " default -> g(); } } void f(){} void g(){} }",
    ]
    for src in casos:
        assert _hits("pattern_switch", src), src
