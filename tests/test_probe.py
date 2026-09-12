"""Fixtures de árvore real: Maven simples, multi-módulo, parent externo, Gradle, Android."""

from __future__ import annotations

from pathlib import Path

import pytest

from jmod.ingest.probe import inspect_tree

POM_RELEASE = """<project><modelVersion>4.0.0</modelVersion>
  <properties><maven.compiler.release>17</maven.compiler.release></properties>
</project>"""

POM_SOURCE_TARGET = """<project>
  <properties>
    <maven.compiler.source>1.8</maven.compiler.source>
    <maven.compiler.target>1.8</maven.compiler.target>
  </properties>
</project>"""

POM_SPRING_PARENT = """<project>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.0</version>
    <relativePath/>
  </parent>
  <properties><java.version>21</java.version></properties>
</project>"""

POM_INTERNAL_PARENT = """<project>
  <parent>
    <groupId>com.acme</groupId><artifactId>root</artifactId><version>1.0</version>
    <relativePath>../pom.xml</relativePath>
  </parent>
</project>"""

GRADLE_GROOVY = """plugins { id 'java' }
sourceCompatibility = JavaVersion.VERSION_11
targetCompatibility = JavaVersion.VERSION_11
"""

GRADLE_KTS_TOOLCHAIN = """plugins { java }
java { toolchain { languageVersion.set(JavaLanguageVersion.of(21)) } }
"""

GRADLE_ANDROID = """plugins { id 'com.android.application' }
android { compileSdk 34 }
"""


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def test_maven_mono_modulo_com_release(tmp_path: Path) -> None:
    r = inspect_tree(_tree(tmp_path, {"pom.xml": POM_RELEASE}))
    assert r.build_system == "maven"
    assert r.module_count == 1
    assert r.head_baseline == 17
    assert r.has_external_parent is False


def test_maven_source_target_com_prefixo_1_ponto(tmp_path: Path) -> None:
    r = inspect_tree(_tree(tmp_path, {"pom.xml": POM_SOURCE_TARGET}))
    assert r.head_baseline == 8, "1.8 precisa virar 8"


def test_maven_multi_modulo_conta_modulos(tmp_path: Path) -> None:
    r = inspect_tree(
        _tree(
            tmp_path,
            {
                "pom.xml": POM_RELEASE,
                "core/pom.xml": POM_INTERNAL_PARENT,
                "api/pom.xml": POM_INTERNAL_PARENT,
            },
        )
    )
    assert r.module_count == 3
    assert r.has_external_parent is False, "relativePath preenchido = parent interno"


def test_parent_externo_e_detectado_por_relativepath_vazio(tmp_path: Path) -> None:
    r = inspect_tree(_tree(tmp_path, {"pom.xml": POM_SPRING_PARENT}))
    assert r.has_external_parent is True
    assert r.head_baseline == 21, "cai na propriedade java.version"


def test_gradle_groovy(tmp_path: Path) -> None:
    r = inspect_tree(_tree(tmp_path, {"build.gradle": GRADLE_GROOVY}))
    assert r.build_system == "gradle_groovy"
    assert r.head_baseline == 11


def test_gradle_kts_toolchain(tmp_path: Path) -> None:
    r = inspect_tree(_tree(tmp_path, {"build.gradle.kts": GRADLE_KTS_TOOLCHAIN}))
    assert r.build_system == "gradle_kts"
    assert r.head_baseline == 21


def test_projeto_misto(tmp_path: Path) -> None:
    r = inspect_tree(_tree(tmp_path, {"pom.xml": POM_RELEASE, "app/build.gradle": GRADLE_GROOVY}))
    assert r.build_system == "mixed"


def test_android_e_sinalizado(tmp_path: Path) -> None:
    r = inspect_tree(_tree(tmp_path, {"build.gradle": GRADLE_ANDROID}))
    assert r.is_android is True


def test_sem_arquivo_de_build(tmp_path: Path) -> None:
    r = inspect_tree(_tree(tmp_path, {"README.md": "nada"}))
    assert r.build_system == "none"
    assert r.head_baseline is None


def test_menor_versao_entre_modulos(tmp_path: Path) -> None:
    pom_misto = """<project><properties>
      <maven.compiler.source>8</maven.compiler.source>
      <maven.compiler.target>17</maven.compiler.target>
    </properties></project>"""
    r = inspect_tree(_tree(tmp_path, {"pom.xml": pom_misto}))
    assert r.head_baseline == 8, "conflito no mesmo nível resolve pelo menor"


@pytest.mark.parametrize("nome", ["build.gradle", "build.gradle.kts"])
def test_ignora_arvore_git(tmp_path: Path, nome: str) -> None:
    r = inspect_tree(_tree(tmp_path, {nome: GRADLE_GROOVY, ".git/objects/pom.xml": POM_RELEASE}))
    assert r.build_system.startswith("gradle")


def test_diretorio_com_nome_de_arquivo_de_build_nao_derruba_o_probe(tmp_path) -> None:
    """`rglob` casa diretório: `jib-core/examples/build.gradle` é uma pasta no
    googlecontainertools/jib, e `read_text` nela estourava IsADirectoryError, derrubando o
    probe do projeto inteiro. Mesmo defeito da D-017, encontrado depois em segundo lugar."""
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "build.gradle").mkdir()
    (tmp_path / "build.gradle").write_text("sourceCompatibility = 17\n")
    (tmp_path / "pom.xml").mkdir()

    achados = []
    for padrao in ("pom.xml", "build.gradle", "build.gradle.kts"):
        for p in tmp_path.rglob(padrao):
            if ".git" in p.parts or not p.is_file():
                continue
            achados.append(p.name)
            p.read_text()  # não pode estourar

    assert achados == ["build.gradle"]
