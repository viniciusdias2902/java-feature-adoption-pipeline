"""Fase 12: os proxies contam sítios convertíveis, e só eles."""

from __future__ import annotations

from jmod.features.opportunity import count_proxy
from jmod.features.parser import parse


def n(proxy: str, src: str) -> int:
    return count_proxy(proxy, parse(src.encode()), src.encode())


# ------------------------------------------------------------ instanceof+cast
def test_instanceof_com_cast_do_mesmo_tipo_conta() -> None:
    src = (
        "class A { void m(Object o){ if (o instanceof String) "
        "{ String s = (String) o; s.length(); } } }"
    )
    assert n("instanceof_cast", src) == 1


def test_instanceof_ja_com_binding_nao_e_oportunidade() -> None:
    src = "class A { void m(Object o){ if (o instanceof String s) { s.length(); } } }"
    assert n("instanceof_cast", src) == 0


def test_instanceof_sem_cast_nao_conta() -> None:
    src = "class A { boolean m(Object o){ return o instanceof String; } }"
    assert n("instanceof_cast", src) == 0


def test_cast_para_tipo_diferente_nao_conta() -> None:
    src = "class A { void m(Object o){ if (o instanceof Number) { String s = (String) o; } } }"
    assert n("instanceof_cast", src) == 0


def test_negacao_sem_cast_no_consequente_nao_conta() -> None:
    src = "class A { void m(Object o){ if (!(o instanceof String)) { return; } } }"
    assert n("instanceof_cast", src) == 0


# ------------------------------------------------------- switch atributivo
def test_switch_de_returns_e_conversivel() -> None:
    src = (
        "class A { int m(int d){ switch(d){ case 1: return 10; case 2: return 20; "
        "default: return 0; } } }"
    )
    assert n("attributive_switch", src) == 1


def test_switch_de_atribuicao_e_break_e_conversivel() -> None:
    src = (
        "class A { void m(int d){ int x; switch(d){ case 1: x = 1; break; "
        "case 2: x = 2; break; default: x = 0; } } }"
    )
    assert n("attributive_switch", src) == 1


def test_switch_com_logica_nao_e_conversivel() -> None:
    src = (
        "class A { void m(int d){ switch(d){ case 1: f(); g(); break; "
        "default: break; } } void f(){} void g(){} }"
    )
    assert n("attributive_switch", src) == 0


def test_switch_de_seta_nao_e_oportunidade() -> None:
    src = "class A { void m(int d){ switch(d){ case 1 -> f(); } } void f(){} }"
    assert n("attributive_switch", src) == 0


def test_fallthrough_vazio_agrupavel_conta() -> None:
    src = "class A { int m(int d){ switch(d){ case 1: case 2: return 12; default: return 0; } } }"
    assert n("attributive_switch", src) == 1


# ------------------------------------------- denominador no dataset: zero vs. ausente
def test_denominador_de_oportunidade_separa_zero_de_ausente() -> None:
    """O desenho só define proxy para duas features; e proxy ausente num projeto medido é 0.

    A Fase 12 grava uma linha por (proxy, escopo) observado. Tratar a chave ausente como
    nulo confundia "não tinha oportunidade" com "não foi medido" — 158 zeros legítimos
    viravam nulos e saíam do desfecho normalizado (D-017).
    """
    from jmod.dataset.build_survival import opportunity_head

    counts = {("a/b", "instanceof_cast"): 7}
    measured = {"a/b", "c/d"}

    assert opportunity_head(counts, measured, "a/b", "pattern_instanceof") == 7
    # medido, mas o proxy não ocorre no HEAD -> zero, não nulo
    assert opportunity_head(counts, measured, "a/b", "switch_expression") == 0
    assert opportunity_head(counts, measured, "c/d", "pattern_instanceof") == 0
    # projeto que a Fase 12 nunca mediu (clone falhou) -> continua nulo
    assert opportunity_head(counts, measured, "e/f", "pattern_instanceof") is None
    # features sem proxy de alta confiabilidade não têm denominador
    for feature in ("record", "var", "text_block", "sealed", "record_pattern", "pattern_switch"):
        assert opportunity_head(counts, measured, "a/b", feature) is None


def test_diretorio_com_sufixo_java_nao_derruba_o_projeto(tmp_path) -> None:
    """Projetos Eclipse têm diretórios pontuados (`org.eclipse.foo.java/`) que casam com
    `rglob("*.java")`; `read_bytes` neles estourava IsADirectoryError e matava a medição
    do projeto inteiro (D-017)."""
    (tmp_path / "org.eclipse.foo.java").mkdir()
    (tmp_path / "Real.java").write_bytes(b"class A {}")

    lidos = []
    for java in tmp_path.rglob("*.java"):
        if not java.is_file():
            continue
        lidos.append(java.name)
        java.read_bytes()  # não pode estourar

    assert lidos == ["Real.java"]
