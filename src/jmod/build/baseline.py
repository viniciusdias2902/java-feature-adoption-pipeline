"""Hierarquia de evidência da baseline.

Uma observação nunca é só "versão X": é versão, fonte, nível de evidência, confiança,
se houve conflito e se a resolução ficou incompleta. Sem isso não há como distinguir
"o projeto está em Java 8" de "não conseguimos saber", e essa diferença decide se o
projeto entra ou sai do denominador de elegibilidade.

Regra que não se negocia: níveis 6–7 (`.sdkmanrc`, `.java-version`, CI, Dockerfile) são
**corroborantes**. Nunca definem baseline sozinhos. Um workflow rodando JDK 21 num
projeto com `release=8` não é upgrade de baseline — é matriz de teste.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from jmod.build.java_versions import parse_java_version
from jmod.build.maven import Pom, interpolate, parent_chain


class Confidence(str):
    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"
    CORROBORATING = "corroborating"


class Level(IntEnum):
    """Menor é mais forte: nível 1 é a evidência mais confiável."""

    MAVEN_RELEASE = 1
    MAVEN_SOURCE_TARGET = 2
    GRADLE_TOOLCHAIN = 3
    GRADLE_COMPATIBILITY = 4
    JAVA_VERSION_PROPERTY = 5
    VERSION_FILE = 6
    CI_OR_DOCKER = 7


CONFIDENCE_BY_LEVEL = {
    Level.MAVEN_RELEASE: Confidence.STRONG,
    Level.MAVEN_SOURCE_TARGET: Confidence.STRONG,
    Level.GRADLE_TOOLCHAIN: Confidence.STRONG,
    Level.GRADLE_COMPATIBILITY: Confidence.MEDIUM,
    Level.JAVA_VERSION_PROPERTY: Confidence.MEDIUM,
    Level.VERSION_FILE: Confidence.WEAK,
    Level.CI_OR_DOCKER: Confidence.CORROBORATING,
}

DEFINING_LEVELS = frozenset(
    {
        Level.MAVEN_RELEASE,
        Level.MAVEN_SOURCE_TARGET,
        Level.GRADLE_TOOLCHAIN,
        Level.GRADLE_COMPATIBILITY,
        Level.JAVA_VERSION_PROPERTY,
    }
)


@dataclass(frozen=True)
class Evidence:
    """Um sinal isolado, antes de qualquer arbitragem."""

    module: str
    source: str
    level: Level
    java_version: int | None
    raw_value: str | None

    @property
    def confidence(self) -> str:
        return CONFIDENCE_BY_LEVEL[self.level]

    @property
    def defines_baseline(self) -> bool:
        return self.level in DEFINING_LEVELS and self.java_version is not None


@dataclass(frozen=True)
class Observation:
    """Baseline de um módulo depois de arbitrar as evidências."""

    module: str
    java_version: int | None
    source: str
    level: Level | None
    confidence: str | None
    conflict: bool
    resolution: str  # resolved | unresolved_parent | unparseable | no_evidence
    raw_value: str | None
    corroborating: tuple[Evidence, ...] = ()


def maven_evidence(pom: Pom, by_module: dict[str, Pom]) -> tuple[list[Evidence], bool]:
    """Evidências de um POM, já com propriedades herdadas interpoladas."""
    chain, external = parent_chain(pom, by_module)

    # Propriedades: o mais distante primeiro, para que o filho sobrescreva.
    props: dict[str, str] = {}
    for ancestor in reversed(chain):
        props.update(ancestor.properties)

    # Configuração do plugin também é herdada: vale a do ancestral mais próximo que a
    # declara. Por isso a cadeia é percorrida do filho para a raiz.
    def inherited(attr: str) -> str | None:
        for ancestor in chain:
            value = getattr(ancestor, attr)
            if value is not None:
                return str(value)
        return None

    out: list[Evidence] = []

    def add(raw: str | None, source: str, level: Level) -> None:
        if raw is None:
            return
        resolved = interpolate(raw, props)
        out.append(Evidence(pom.module, source, level, parse_java_version(resolved), resolved))

    add(
        inherited("release") or props.get("maven.compiler.release"),
        "maven_compiler_release",
        Level.MAVEN_RELEASE,
    )
    add(
        inherited("source") or props.get("maven.compiler.source"),
        "maven_compiler_source",
        Level.MAVEN_SOURCE_TARGET,
    )
    add(
        inherited("target") or props.get("maven.compiler.target"),
        "maven_compiler_target",
        Level.MAVEN_SOURCE_TARGET,
    )

    for key in ("java.version", "jdk.version", "javaVersion"):
        if key in props:
            add(props[key], f"property_{key}", Level.JAVA_VERSION_PROPERTY)
            break

    return out, external


def resolve(module: str, evidence: list[Evidence], external_parent: bool) -> Observation:
    """Arbitra as evidências de um módulo.

    Ordem: nível mais forte vence. Dentro do mesmo nível, conflito resolve pelo MENOR
    valor — porque um módulo que declara `source=8` e `target=17` compila contra 8.
    """
    defining = [e for e in evidence if e.defines_baseline]
    corroborating = tuple(e for e in evidence if e.level not in DEFINING_LEVELS)

    if not defining:
        # Havia sinal, mas nenhum virou versão utilizável?
        unparseable = [e for e in evidence if e.level in DEFINING_LEVELS]
        if external_parent:
            resolution = "unresolved_parent"
        elif unparseable:
            resolution = "unparseable"
        else:
            resolution = "no_evidence"
        raw = unparseable[0].raw_value if unparseable else None
        return Observation(module, None, "none", None, None, False, resolution, raw, corroborating)

    best_level = min(e.level for e in defining)
    same_level = [e for e in defining if e.level == best_level]
    versions = {e.java_version for e in same_level}
    conflict = len(versions) > 1
    chosen = min(same_level, key=lambda e: e.java_version or 0)

    return Observation(
        module=module,
        java_version=chosen.java_version,
        source=chosen.source,
        level=best_level,
        confidence=chosen.confidence,
        conflict=conflict,
        resolution="resolved",
        raw_value=chosen.raw_value,
        corroborating=corroborating,
    )


def maven_baselines(poms: dict[str, bytes]) -> dict[str, Observation]:
    """Baseline por módulo, para todos os POMs de um commit.

    `poms` mapeia caminho -> conteúdo. Retorna módulo -> observação.
    """
    from jmod.build.maven import PomParseError, module_of, parse_pom

    parsed: dict[str, Pom] = {}
    broken: dict[str, str] = {}
    for path, content in poms.items():
        try:
            pom = parse_pom(path, content)
        except PomParseError as exc:
            broken[module_of(path)] = str(exc)
            continue
        parsed[pom.module] = pom

    result: dict[str, Observation] = {}
    for module, pom in parsed.items():
        evidence, external = maven_evidence(pom, parsed)
        result[module] = resolve(module, evidence, external)
    for module, reason in broken.items():
        result[module] = Observation(
            module, None, "none", None, None, False, "unparseable", reason[:200]
        )
    return result
