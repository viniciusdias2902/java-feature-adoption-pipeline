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
