"""Wrapper do git contra repositórios sintéticos reais."""

from __future__ import annotations

import pytest

from jmod.repo.git import GitError, Repo


def _pom(v: object) -> str:
    return (
        "<project><properties>"
        f"<maven.compiler.release>{v}</maven.compiler.release>"
        "</properties></project>"
    )


POM_8 = _pom(8)
POM_17 = _pom(17)


def test_le_branch_default_do_repositorio(synthetic) -> None:
    r = synthetic()
    r.commit({"README.md": "x"})
    assert Repo(r.path).default_branch() == "main"


def test_show_recupera_conteudo_em_commit_especifico(synthetic) -> None:
    r = synthetic()
    a = r.commit({"pom.xml": POM_8})
    r.commit({"pom.xml": POM_17})
    repo = Repo(r.path)
    assert b"release>8<" in repo.show(a, "pom.xml")
    assert b"release>17<" in repo.show(repo.head_sha(), "pom.xml")


def test_log_changes_registra_adicao_modificacao_e_delecao(synthetic) -> None:
    r = synthetic()
    r.commit({"pom.xml": POM_8})
    r.commit({"pom.xml": POM_17})
    r.commit({"pom.xml": None, "build.gradle": "java {}"})

    changes = list(Repo(r.path).log_changes("main", "pom.xml"))
    tipos = [c.change_type for c in changes]
    assert sorted(tipos) == ["A", "D", "M"], "deleção precisa aparecer para detectar abandono"


def test_log_changes_filtra_por_pathspec(synthetic) -> None:
    r = synthetic()
    r.commit({"pom.xml": POM_8, "src/A.java": "class A {}"})
    r.commit({"src/A.java": "class A { int x; }"})
    changes = list(Repo(r.path).log_changes("main", "pom.xml"))
    assert len(changes) == 1
    assert changes[0].path == "pom.xml"


def test_log_changes_detecta_renomeacao(synthetic) -> None:
    r = synthetic()
    r.commit({"core/pom.xml": POM_8})
    r.commit({"core/pom.xml": POM_8 + "<!-- x -->"})
    r.rename("core/pom.xml", "modules/core/pom.xml")
    changes = list(Repo(r.path).log_changes("main", "**/pom.xml"))
    renames = [c for c in changes if c.change_type == "R"]
    assert renames, "renomeação de módulo precisa ser rastreável"
    assert renames[0].old_path == "core/pom.xml"
    assert renames[0].path == "modules/core/pom.xml"


def test_commits_trazem_committer_date_iso(synthetic) -> None:
    r = synthetic()
    r.commit({"pom.xml": POM_8})
    change = next(iter(Repo(r.path).log_changes("main", "pom.xml")))
    assert change.committed_at.startswith("2020-01-01T")


def test_pickaxe_literal_acha_introducao(synthetic) -> None:
    r = synthetic()
    r.commit({"src/A.java": "class A {}"})
    alvo = r.commit({"src/A.java": "class A { record P(int x) {} }"})
    hits = list(Repo(r.path).pickaxe("main", literal="record ", pathspecs=["*.java"]))
    assert [h.sha for h in hits] == [alvo]


def test_pickaxe_literal_nao_ve_conversao_que_preserva_contagem(synthetic) -> None:
    """A armadilha central da Fase 10, provada num repositório real.

    Converter `case 1:` em `case 1 ->` não altera a contagem de "switch",
    então `-S'switch'` não enxerga a modernização.
    """
    classico = "class A { void m(int d){ switch(d){ case 1: f(); break; } } }"
    seta = "class A { void m(int d){ switch(d){ case 1 -> f(); } } }"
    r = synthetic()
    r.commit({"src/A.java": classico})
    conversao = r.commit({"src/A.java": seta})
    repo = Repo(r.path)

    por_literal = [h.sha for h in repo.pickaxe("main", literal="switch", pathspecs=["*.java"])]
    assert conversao not in por_literal, "confirma que -S é cego para a conversão"

    por_regex = [h.sha for h in repo.pickaxe("main", regex="case.*->", pathspecs=["*.java"])]
    assert conversao in por_regex, "-G precisa capturar o que -S perde"


def test_pickaxe_exige_exatamente_uma_estrategia(synthetic) -> None:
    r = synthetic()
    r.commit({"src/A.java": "class A {}"})
    repo = Repo(r.path)
    with pytest.raises(ValueError, match="exatamente um"):
        list(repo.pickaxe("main", literal="a", regex="b"))
    with pytest.raises(ValueError, match="exatamente um"):
        list(repo.pickaxe("main"))


def test_erro_de_git_traz_stderr(synthetic) -> None:
    r = synthetic()
    r.commit({"a": "x"})
    with pytest.raises(GitError):
        Repo(r.path).show("deadbeef" * 5, "a")
