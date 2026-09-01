"""Fase 6: baseline Gradle. Fragilidade estrutural — o teste cobre o que É extraível
e, igualmente importante, confirma que o irresolvível NÃO vira palpite."""

from __future__ import annotations

from jmod.build.baseline import Level, gradle_baselines
from jmod.build.gradle import (
    block_stack_at,
    parse_gradle,
    parse_gradle_properties,
    resolve_value,
    strip_comments,
)


def g(text: str) -> bytes:
    return text.encode()


# ------------------------------------------------------------------ toolchain
def test_toolchain_kotlin_dsl() -> None:
    obs = gradle_baselines(
        {
            "build.gradle.kts": g(
                "java { toolchain { languageVersion.set(JavaLanguageVersion.of(21)) } }"
            )
        }
    )[""]
    assert obs.java_version == 21
    assert obs.level == Level.GRADLE_TOOLCHAIN
    assert obs.confidence == "strong"


def test_toolchain_groovy_com_igual() -> None:
    obs = gradle_baselines(
        {
            "build.gradle": g(
                "java {\n  toolchain {\n    languageVersion = JavaLanguageVersion.of(17)\n  }\n}"
            )
        }
    )[""]
    assert obs.java_version == 17


# ------------------------------------------------------------- compatibilidade
def test_source_compatibility_enum() -> None:
    obs = gradle_baselines(
        {
            "build.gradle": g(
                "sourceCompatibility = JavaVersion.VERSION_11\n"
                "targetCompatibility = JavaVersion.VERSION_11"
            )
        }
    )[""]
    assert obs.java_version == 11
    assert obs.level == Level.GRADLE_COMPATIBILITY
    assert obs.confidence == "medium"


def test_source_compatibility_notacao_antiga_em_string() -> None:
    obs = gradle_baselines({"build.gradle": g("sourceCompatibility = '1.8'")})[""]
    assert obs.java_version == 8


def test_source_compatibility_estilo_groovy_sem_igual() -> None:
    obs = gradle_baselines({"build.gradle": g("sourceCompatibility JavaVersion.VERSION_1_8")})[""]
    assert obs.java_version == 8


def test_conflito_source_target_resolve_pelo_menor() -> None:
    obs = gradle_baselines(
        {"build.gradle": g("sourceCompatibility = 8\ntargetCompatibility = 17")}
    )[""]
    assert obs.java_version == 8
    assert obs.conflict is True


def test_toolchain_tem_precedencia_sobre_compatibility() -> None:
    obs = gradle_baselines(
        {
            "build.gradle": g(
                "sourceCompatibility = 8\n"
                "java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }"
            )
        }
    )[""]
    assert obs.java_version == 21
    assert obs.conflict is False, "níveis diferentes são precedência, não conflito"


def test_options_release() -> None:
    obs = gradle_baselines(
        {"build.gradle": g("tasks.withType(JavaCompile) { options.release = 17 }")}
    )[""]
    assert obs.java_version == 17


# ------------------------------------------------------------------ variáveis
def test_resolve_variavel_de_gradle_properties() -> None:
    props = parse_gradle_properties(g("# comentário\njavaVersion=17\nfoo=bar"))
    obs = gradle_baselines({"build.gradle": g("sourceCompatibility = javaVersion")}, props)[""]
    assert obs.java_version == 17


def test_resolve_variavel_definida_no_arquivo() -> None:
    obs = gradle_baselines(
        {"build.gradle": g("def javaVersion = 17\nsourceCompatibility = javaVersion")}
    )[""]
    assert obs.java_version == 17


def test_resolve_interpolacao_groovy() -> None:
    obs = gradle_baselines({"build.gradle": g('ext { jdk = 21 }\nsourceCompatibility = "${jdk}"')})[
        ""
    ]
    assert obs.java_version == 21


def test_variavel_irresolvivel_nao_vira_palpite() -> None:
    """Caso real: rxjava usa `JavaLanguageVersion.of(toolchainJdk)`, definido por ambiente."""
    obs = gradle_baselines(
        {
            "build.gradle": g(
                "java { toolchain { languageVersion = JavaLanguageVersion.of(toolchainJdk) } }"
            )
        }
    )[""]
    assert obs.java_version is None
    assert obs.resolution == "unparseable"
    assert "toolchainJdk" in (obs.raw_value or "")


# ------------------------------------------------------------------ propagação
def test_subprojects_propaga_para_filhos_mas_nao_para_a_raiz() -> None:
    obs = gradle_baselines(
        {
            "build.gradle": g("subprojects {\n  sourceCompatibility = 11\n}"),
            "core/build.gradle": g("dependencies { }"),
        }
    )
    assert obs["core"].java_version == 11
    assert obs[""].java_version is None, "subprojects não alcança o próprio raiz"


def test_allprojects_alcanca_a_raiz_tambem() -> None:
    obs = gradle_baselines(
        {
            "build.gradle": g("allprojects {\n  sourceCompatibility = 17\n}"),
            "core/build.gradle": g("dependencies { }"),
        }
    )
    assert obs[""].java_version == 17
    assert obs["core"].java_version == 17


def test_declaracao_propria_vence_a_propagada() -> None:
    obs = gradle_baselines(
        {
            "build.gradle": g("subprojects {\n  sourceCompatibility = 8\n}"),
            "api/build.gradle": g("sourceCompatibility = 21"),
        }
    )
    assert obs["api"].java_version == 21, "elegibilidade é por módulo"


# ------------------------------------------------------------------ robustez
def test_comentarios_nao_geram_deteccao() -> None:
    obs = gradle_baselines(
        {
            "build.gradle": g(
                "// sourceCompatibility = 8\n"
                "/* targetCompatibility = 8 */\n"
                "sourceCompatibility = 17"
            )
        }
    )[""]
    assert obs.java_version == 17


def test_strip_comments_preserva_numero_de_linhas() -> None:
    texto = "linha1\n// comentário\n/* bloco\n   segue */\nlinha5"
    assert strip_comments(texto).count("\n") == texto.count("\n")


def test_block_stack_identifica_bloco_envolvente() -> None:
    texto = "subprojects {\n  java {\n    X\n  }\n}"
    stack = block_stack_at(texto, texto.index("X"))
    assert stack == ["subprojects", "java"]


def test_resolve_value_devolve_original_quando_nao_sabe() -> None:
    assert resolve_value("desconhecida", {}) == "desconhecida"


def test_arquivo_sem_declaracao() -> None:
    obs = gradle_baselines({"build.gradle": g("plugins { id 'java' }")})[""]
    assert obs.java_version is None
    assert obs.resolution == "no_evidence"


def test_versao_impossivel_e_rejeitada() -> None:
    obs = gradle_baselines({"build.gradle": g("sourceCompatibility = 2")})[""]
    assert obs.java_version is None
    assert obs.resolution == "unparseable"


def test_parse_gradle_registra_linha() -> None:
    decls = parse_gradle(g("plugins { }\n\nsourceCompatibility = 17"))
    assert decls[0].line == 3


# ------------------------------------------------- plugins de convenção (buildSrc)
def test_convention_plugin_do_buildsrc_e_aplicado_ao_modulo() -> None:
    """Caso real: jbake declara a versão num convention plugin, não no build.gradle."""
    obs = gradle_baselines(
        {
            "buildSrc/src/main/groovy/org.acme.convention.java-common.gradle": g(
                "java {\n  sourceCompatibility = 1.8\n  targetCompatibility = 1.8\n}"
            ),
            "build.gradle": g("plugins { id 'org.acme.convention.java-common' }"),
        }
    )[""]
    assert obs.java_version == 8


def test_convention_plugin_kotlin_dsl() -> None:
    obs = gradle_baselines(
        {
            "buildSrc/src/main/kotlin/acme.java-conventions.gradle.kts": g(
                "java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }"
            ),
            "core/build.gradle.kts": g('plugins { id("acme.java-conventions") }'),
        }
    )["core"]
    assert obs.java_version == 21


def test_modulo_que_nao_aplica_o_plugin_nao_herda() -> None:
    obs = gradle_baselines(
        {
            "buildSrc/src/main/kotlin/acme.java-conventions.gradle.kts": g(
                "java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }"
            ),
            "outro/build.gradle.kts": g('plugins { id("outro.coisa") }'),
        }
    )["outro"]
    assert obs.java_version is None, "o vínculo é por id de plugin aplicado, não por proximidade"


def test_declaracao_propria_vence_convention_plugin() -> None:
    obs = gradle_baselines(
        {
            "buildSrc/src/main/kotlin/acme.java-conventions.gradle.kts": g(
                "java { toolchain { languageVersion = JavaLanguageVersion.of(8) } }"
            ),
            "api/build.gradle.kts": g(
                'plugins { id("acme.java-conventions") }\nsourceCompatibility = 21'
            ),
        }
    )["api"]
    assert obs.java_version == 21


def test_id_do_plugin_vem_do_nome_do_arquivo() -> None:
    from jmod.build.gradle import convention_plugin_id

    assert convention_plugin_id("buildSrc/src/main/kotlin/qupath.java-conventions.gradle.kts") == (
        "qupath.java-conventions"
    )
    assert convention_plugin_id("buildSrc/build.gradle.kts") is None, "o build do próprio buildSrc"
    assert convention_plugin_id("core/build.gradle") is None


def test_apply_plugin_estilo_antigo() -> None:
    from jmod.build.gradle import applied_plugins

    assert applied_plugins(g("apply plugin: 'org.acme.java'")) == {"org.acme.java"}
