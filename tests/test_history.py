"""Fase 4: redução do histórico e mapa arquivo -> módulo."""

from __future__ import annotations

from jmod.build.history import ModuleMap, build_file_history, is_primary
from jmod.repo.git import Repo


def _pom(v: object) -> str:
    return (
        "<project><properties>"
        f"<maven.compiler.release>{v}</maven.compiler.release>"
        "</properties></project>"
    )


GRADLE = "java {{ sourceCompatibility = {v} }}"


def test_reduz_historico_a_commits_de_build(synthetic) -> None:
    r = synthetic()
    r.commit({"pom.xml": _pom(8), "src/main/java/A.java": "class A {}"})
    for i in range(10):  # ruído: só código
        r.commit({"src/main/java/A.java": f"class A {{ int x{i}; }}"})
    r.commit({"pom.xml": _pom(17)})  # o que importa

    changes = list(build_file_history(Repo(r.path), "main"))
    assert len(changes) == 2, "10 commits de código não podem entrar"
    assert {c.change_type for c in changes} == {"A", "M"}


def test_captura_todas_as_fontes_de_build(synthetic) -> None:
    r = synthetic()
    r.commit(
        {
            "pom.xml": _pom(8),
            "modules/core/build.gradle": GRADLE.format(v=11),
            "app/build.gradle.kts": "java { }",
            "gradle.properties": "org.gradle.jvmargs=-Xmx2g",
            ".sdkmanrc": "java=17.0.1-tem",
            ".java-version": "17",
            ".github/workflows/ci.yml": "jobs: {}",
            "Dockerfile": "FROM eclipse-temurin:21",
            "README.md": "ignorar",
        }
    )
    paths = {c.path for c in build_file_history(Repo(r.path), "main")}
    assert "pom.xml" in paths
    assert "modules/core/build.gradle" in paths
    assert "app/build.gradle.kts" in paths
    assert "gradle.properties" in paths
    assert ".sdkmanrc" in paths
    assert ".java-version" in paths
    assert ".github/workflows/ci.yml" in paths
    assert "Dockerfile" in paths
    assert "README.md" not in paths


def test_distingue_fonte_primaria_de_auxiliar() -> None:
    assert is_primary("pom.xml")
    assert is_primary("core/build.gradle.kts")
    assert not is_primary(".sdkmanrc")
    assert not is_primary(".github/workflows/ci.yml")
    assert not is_primary("Dockerfile")


def test_delecao_de_modulo_aparece(synthetic) -> None:
    r = synthetic()
    r.commit({"pom.xml": _pom(8), "old/pom.xml": _pom(8)})
    r.commit({"old/pom.xml": None})
    changes = list(build_file_history(Repo(r.path), "main"))
    assert any(c.change_type == "D" and c.path == "old/pom.xml" for c in changes)


# ------------------------------------------------------------------ ModuleMap
def test_mapa_de_modulos_mono(synthetic) -> None:
    r = synthetic()
    sha = r.commit({"pom.xml": _pom(8), "src/main/java/A.java": "class A {}"})
    m = ModuleMap.at(Repo(r.path), sha)
    assert len(m) == 1
    assert m.owner_of("src/main/java/A.java") == ""


def test_mapa_de_modulos_multi(synthetic) -> None:
    r = synthetic()
    sha = r.commit(
        {
            "pom.xml": _pom(8),
            "core/pom.xml": _pom(8),
            "api/pom.xml": _pom(17),
            "core/src/main/java/C.java": "class C {}",
            "api/src/main/java/A.java": "class A {}",
            "src/main/java/Root.java": "class Root {}",
        }
    )
    m = ModuleMap.at(Repo(r.path), sha)
    assert len(m) == 3
    assert m.owner_of("core/src/main/java/C.java") == "core"
    assert m.owner_of("api/src/main/java/A.java") == "api"
    assert m.owner_of("src/main/java/Root.java") == ""


def test_modulo_dono_e_o_ancestral_mais_proximo(synthetic) -> None:
    """Módulo aninhado: o arquivo pertence ao mais próximo, não ao mais externo."""
    r = synthetic()
    sha = r.commit(
        {
            "pom.xml": _pom(8),
            "parent/pom.xml": _pom(8),
            "parent/child/pom.xml": _pom(17),
            "parent/child/src/main/java/A.java": "class A {}",
            "parent/src/main/java/B.java": "class B {}",
        }
    )
    m = ModuleMap.at(Repo(r.path), sha)
    assert m.owner_of("parent/child/src/main/java/A.java") == "parent/child"
    assert m.owner_of("parent/src/main/java/B.java") == "parent"


def test_mapa_gradle(synthetic) -> None:
    r = synthetic()
    sha = r.commit(
        {
            "build.gradle": "java {}",
            "lib/build.gradle.kts": "java {}",
            "lib/src/main/java/L.java": "class L {}",
        }
    )
    m = ModuleMap.at(Repo(r.path), sha)
    assert m.owner_of("lib/src/main/java/L.java") == "lib"


def test_mapa_muda_ao_longo_do_historico(synthetic) -> None:
    """Módulo criado depois: o mapa é por commit, não global."""
    r = synthetic()
    antes = r.commit({"pom.xml": _pom(8), "src/main/java/A.java": "class A {}"})
    depois = r.commit({"novo/pom.xml": _pom(17), "novo/src/main/java/N.java": "class N {}"})
    repo = Repo(r.path)
    assert len(ModuleMap.at(repo, antes)) == 1
    assert len(ModuleMap.at(repo, depois)) == 2
    assert ModuleMap.at(repo, depois).owner_of("novo/src/main/java/N.java") == "novo"
