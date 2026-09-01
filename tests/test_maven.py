"""Fase 5: baseline Maven, com herança, interpolação e conflito."""

from __future__ import annotations

from jmod.build.baseline import Level, maven_baselines
from jmod.build.maven import interpolate, parse_pom

NS = 'xmlns="http://maven.apache.org/POM/4.0.0"'


def pom(body: str, ns: bool = True) -> bytes:
    return f"<project {NS if ns else ''}>{body}</project>".encode()


# ------------------------------------------------------------------ tags diretas
def test_release_e_a_evidencia_mais_forte() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties>"
                "<maven.compiler.release>17</maven.compiler.release>"
                "<maven.compiler.source>8</maven.compiler.source>"
                "</properties>"
            )
        }
    )[""]
    assert obs.java_version == 17
    assert obs.level == Level.MAVEN_RELEASE
    assert obs.confidence == "strong"
    assert obs.conflict is False, "níveis diferentes não são conflito, é precedência"


def test_source_e_target_normalizam_notacao_antiga() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties>"
                "<maven.compiler.source>1.8</maven.compiler.source>"
                "<maven.compiler.target>1.8</maven.compiler.target>"
                "</properties>"
            )
        }
    )[""]
    assert obs.java_version == 8
    assert obs.level == Level.MAVEN_SOURCE_TARGET


def test_conflito_no_mesmo_nivel_resolve_pelo_menor() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties>"
                "<maven.compiler.source>8</maven.compiler.source>"
                "<maven.compiler.target>17</maven.compiler.target>"
                "</properties>"
            )
        }
    )[""]
    assert obs.java_version == 8, "compila contra o menor"
    assert obs.conflict is True, "a divergência precisa ficar registrada"


def test_configuracao_do_plugin_compilador() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<build><plugins><plugin>"
                "<artifactId>maven-compiler-plugin</artifactId>"
                "<configuration><release>21</release></configuration>"
                "</plugin></plugins></build>"
            )
        }
    )[""]
    assert obs.java_version == 21


def test_plugin_management_tambem_conta() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<build><pluginManagement><plugins><plugin>"
                "<artifactId>maven-compiler-plugin</artifactId>"
                "<configuration><source>11</source><target>11</target></configuration>"
                "</plugin></plugins></pluginManagement></build>"
            )
        }
    )[""]
    assert obs.java_version == 11


def test_java_version_e_nivel_5_nao_nivel_1() -> None:
    obs = maven_baselines(
        {"pom.xml": pom("<properties><java.version>21</java.version></properties>")}
    )[""]
    assert obs.java_version == 21
    assert obs.level == Level.JAVA_VERSION_PROPERTY
    assert obs.confidence == "medium"


# ------------------------------------------------------------------ interpolação
def test_interpola_propriedade() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties>"
                "<java.version>17</java.version>"
                "<maven.compiler.release>${java.version}</maven.compiler.release>"
                "</properties>"
            )
        }
    )[""]
    assert obs.java_version == 17
    assert obs.level == Level.MAVEN_RELEASE


def test_interpolacao_transitiva() -> None:
    props = {"a": "${b}", "b": "${c}", "c": "11"}
    assert interpolate("${a}", props) == "11"


def test_referencia_irresolvivel_nao_vira_versao() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties><maven.compiler.release>${nao.existe}</maven.compiler.release></properties>"
            )
        }
    )[""]
    assert obs.java_version is None
    assert obs.resolution == "unparseable"
    assert obs.raw_value == "${nao.existe}"


def test_interpolacao_ciclica_termina() -> None:
    assert interpolate("${a}", {"a": "${b}", "b": "${a}"}) is not None


# ------------------------------------------------------------------ herança
def test_herda_propriedade_do_pai_interno() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties><maven.compiler.release>17</maven.compiler.release></properties>"
            ),
            "core/pom.xml": pom("<parent><artifactId>root</artifactId></parent>"),
        }
    )
    assert obs["core"].java_version == 17
    assert obs["core"].resolution == "resolved"


def test_filho_sobrescreve_o_pai() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties><maven.compiler.release>8</maven.compiler.release></properties>"
            ),
            "api/pom.xml": pom(
                "<parent><artifactId>root</artifactId></parent>"
                "<properties><maven.compiler.release>17</maven.compiler.release></properties>"
            ),
        }
    )
    assert obs[""].java_version == 8
    assert obs["api"].java_version == 17, "elegibilidade é por módulo"


def test_parent_externo_vira_unresolved() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<parent>"
                "<groupId>org.springframework.boot</groupId>"
                "<artifactId>spring-boot-starter-parent</artifactId>"
                "<relativePath/>"
                "</parent>"
            )
        }
    )[""]
    assert obs.java_version is None
    assert obs.resolution == "unresolved_parent"


def test_parent_externo_nao_impede_declaracao_propria() -> None:
    """Se o módulo declara a versão, o pai remoto é irrelevante."""
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<parent><artifactId>spring-boot-starter-parent</artifactId><relativePath/></parent>"
                "<properties><java.version>21</java.version></properties>"
            )
        }
    )[""]
    assert obs.java_version == 21
    assert obs.resolution == "resolved"


def test_parent_declarado_mas_ausente_no_repositorio() -> None:
    obs = maven_baselines(
        {
            "sub/pom.xml": pom("<parent><artifactId>fora</artifactId></parent>"),
        }
    )["sub"]
    assert obs.resolution == "unresolved_parent"


def test_cadeia_de_tres_niveis() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties><maven.compiler.release>11</maven.compiler.release></properties>"
            ),
            "a/pom.xml": pom("<parent><artifactId>root</artifactId></parent>"),
            "a/b/pom.xml": pom(
                "<parent><artifactId>a</artifactId><relativePath>../pom.xml</relativePath></parent>"
            ),
        }
    )
    assert obs["a/b"].java_version == 11


# ------------------------------------------------------------------ robustez
def test_xml_invalido_nao_derruba_o_commit_inteiro() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties><maven.compiler.release>17</maven.compiler.release></properties>"
            ),
            "quebrado/pom.xml": b"<project><properties>",
        }
    )
    assert obs[""].java_version == 17, "o POM válido continua sendo lido"
    assert obs["quebrado"].resolution == "unparseable"


def test_pom_sem_namespace() -> None:
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties><maven.compiler.release>8</maven.compiler.release></properties>",
                ns=False,
            )
        }
    )[""]
    assert obs.java_version == 8


def test_sem_nenhuma_evidencia() -> None:
    obs = maven_baselines({"pom.xml": pom("<artifactId>x</artifactId>")})[""]
    assert obs.java_version is None
    assert obs.resolution == "no_evidence"


def test_versao_impossivel_e_rejeitada() -> None:
    """D-007: a detecção real não pode aceitar o que a regex do probe aceitava."""
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties><maven.compiler.release>2</maven.compiler.release></properties>"
            )
        }
    )[""]
    assert obs.java_version is None
    assert obs.resolution == "unparseable"


def test_modulo_e_derivado_do_caminho() -> None:
    p = parse_pom("modules/core/pom.xml", pom("<artifactId>core</artifactId>"))
    assert p.module == "modules/core"


def test_parent_sem_relative_path_que_sai_do_repositorio() -> None:
    """POM raiz herdando de parent publicado, sem declarar <relativePath/>.

    O padrão `../pom.xml` aponta para fora do repositório. Isso é `unresolved_parent`,
    não `no_evidence` — a diferença entre "não sabemos" e "o projeto não declara".
    Caso real: jpmml-sklearn, cujo pai é jpmml-parent.
    """
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<parent><groupId>org.jpmml</groupId><artifactId>jpmml-parent</artifactId></parent>"
            )
        }
    )[""]
    assert obs.resolution == "unresolved_parent"
    assert obs.java_version is None


def test_submodulo_com_parent_interno_implicito_continua_resolvendo() -> None:
    """O padrão `../pom.xml` a partir de um submódulo aponta para dentro."""
    obs = maven_baselines(
        {
            "pom.xml": pom(
                "<properties><maven.compiler.release>17</maven.compiler.release></properties>"
            ),
            "core/pom.xml": pom("<parent><artifactId>root</artifactId></parent>"),
        }
    )["core"]
    assert obs.java_version == 17
    assert obs.resolution == "resolved"
