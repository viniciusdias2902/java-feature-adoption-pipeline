"""Fase 5: baseline Java em projetos Maven.

Três coisas tornam isto mais difícil do que ler uma tag:

1. **Interpolação.** `<release>${java.version}</release>` só significa algo depois de
   resolver `<properties>`, possivelmente de forma transitiva.
2. **Herança.** Propriedades e configuração de plugin podem vir do POM pai. Cadeias
   internas ao repositório são resolvidas; um pai externo (`spring-boot-starter-parent`)
   exigiria rede por commit e é inviável em escala — vira `unresolved_parent`.
3. **Precedência.** `release` supera `source`/`target`, que superam `java.version`
   Conflito no mesmo nível resolve pelo MENOR valor.

Limitação assumida: `<profiles>` não-default são ignorados. Um perfil que altere a versão
condicionalmente não é observável estaticamente, e ativá-los exigiria simular a resolução
do Maven. Documentado como limitação conhecida.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

PROPERTY_REF = re.compile(r"\$\{([^}]+)\}")
MAX_INTERPOLATION_DEPTH = 10

RELEASE_PROPS = ("maven.compiler.release",)
SOURCE_PROPS = ("maven.compiler.source",)
TARGET_PROPS = ("maven.compiler.target",)
JAVA_VERSION_PROPS = ("java.version", "jdk.version", "javaVersion")


class PomParseError(ValueError):
    pass


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(elem: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    """Busca por nome ignorando namespace, seguindo um caminho."""
    current: ElementTree.Element | None = elem
    for name in names:
        if current is None:
            return None
        current = next((c for c in current if _strip_ns(c.tag) == name), None)
    return current


def _text(elem: ElementTree.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    return elem.text.strip() or None


@dataclass(frozen=True)
class Pom:
    """Um `pom.xml` lido, ainda sem herança aplicada."""

    path: str
    module: str
    properties: dict[str, str] = field(default_factory=dict)
    release: str | None = None
    source: str | None = None
    target: str | None = None
    parent_module: str | None = None  # módulo do pai, se interno ao repositório
    has_external_parent: bool = False

    @property
    def java_version_property(self) -> str | None:
        for key in JAVA_VERSION_PROPS:
            if key in self.properties:
                return self.properties[key]
        return None


def module_of(pom_path: str) -> str:
    return pom_path.rsplit("/", 1)[0] if "/" in pom_path else ""


def parse_pom(pom_path: str, content: bytes) -> Pom:
    try:
        root = ElementTree.fromstring(content)  # noqa: S314 — POM de repositório público
    except ElementTree.ParseError as exc:
        raise PomParseError(f"{pom_path}: XML inválido: {exc}") from exc

    props: dict[str, str] = {}
    if (node := _find(root, "properties")) is not None:
        for child in node:
            value = _text(child)
            if value is not None:
                props[_strip_ns(child.tag)] = value

    release = source = target = None
    for plugin in _compiler_plugins(root):
        cfg = _find(plugin, "configuration")
        if cfg is None:
            continue
        release = release or _text(_find(cfg, "release"))
        source = source or _text(_find(cfg, "source"))
        target = target or _text(_find(cfg, "target"))

    release = release or _first_prop(props, RELEASE_PROPS)
    source = source or _first_prop(props, SOURCE_PROPS)
    target = target or _first_prop(props, TARGET_PROPS)

    parent_module, external = _parent_of(root, module_of(pom_path))

    return Pom(
        path=pom_path,
        module=module_of(pom_path),
        properties=props,
        release=release,
        source=source,
        target=target,
        parent_module=parent_module,
        has_external_parent=external,
    )


def _first_prop(props: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in props:
            return props[key]
    return None


def _compiler_plugins(root: ElementTree.Element) -> list[ElementTree.Element]:
    """maven-compiler-plugin, tanto em `build/plugins` quanto em `pluginManagement`."""
    found = []
    for container in (
        _find(root, "build", "plugins"),
        _find(root, "build", "pluginManagement", "plugins"),
    ):
        if container is None:
            continue
        for plugin in container:
            if _text(_find(plugin, "artifactId")) == "maven-compiler-plugin":
                found.append(plugin)
    return found


def _parent_of(root: ElementTree.Element, module: str) -> tuple[str | None, bool]:
    """Módulo do pai (se interno) e se o pai é externo ao repositório.

    `<relativePath/>` vazio é a forma canônica de declarar pai remoto. Ausente significa
    o padrão `../pom.xml`, que pode ou não existir no repositório — quem decide é o
    resolvedor, que conhece o conjunto de POMs presentes no commit.
    """
    parent = _find(root, "parent")
    if parent is None:
        return None, False

    rel_node = _find(parent, "relativePath")
    if rel_node is not None and _text(rel_node) is None:
        return None, True  # <relativePath/> => remoto, explicitamente

    rel = _text(rel_node) if rel_node is not None else "../pom.xml"
    candidate = _normalize(module, rel or "../pom.xml")
    if candidate is None:
        # O caminho relativo sai do repositório: o pai é externo, ainda que o POM não
        # tenha declarado <relativePath/>. É o caso de um POM raiz que herda de um
        # parent publicado (jpmml-parent, por exemplo).
        return None, True
    return candidate, False


def _normalize(module: str, relative: str) -> str | None:
    """Módulo do pai a partir de um caminho relativo, ou None se ele sai do repositório.

    Sair do repositório significa que o pai não pode ser interno — não é o mesmo que
    "não tem pai". Grampear na raiz aqui produziria `no_evidence` onde a resposta certa
    é `unresolved_parent`.
    """
    parts = [p for p in module.split("/") if p] if module else []
    rel = relative.removesuffix("pom.xml").rstrip("/")
    for piece in rel.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(piece)
    return "/".join(parts)


def interpolate(value: str | None, properties: dict[str, str]) -> str | None:
    """Resolve `${...}` contra o mapa de propriedades, transitivamente.

    Devolve o texto original quando alguma referência fica sem resolver — cabe ao chamador
    perceber que `parse_java_version` vai recusá-lo.
    """
    if value is None:
        return None
    current = value
    for _ in range(MAX_INTERPOLATION_DEPTH):
        refs = PROPERTY_REF.findall(current)
        if not refs:
            return current
        replaced = current
        for ref in refs:
            if ref in properties:
                replaced = replaced.replace(f"${{{ref}}}", properties[ref])
        if replaced == current:
            return current  # nada mais resolvível
        current = replaced
    return current


def parent_chain(pom: Pom, by_module: dict[str, Pom]) -> tuple[list[Pom], bool]:
    """Cadeia do módulo até a raiz interna, e se ela bateu num pai externo.

    Vem do mais próximo para o mais distante. Ciclos são cortados.
    """
    chain: list[Pom] = []
    seen: set[str] = set()
    current: Pom | None = pom
    external = False
    while current is not None and current.module not in seen:
        seen.add(current.module)
        chain.append(current)
        if current.has_external_parent:
            external = True
            break
        if current.parent_module is None:
            break
        current = by_module.get(current.parent_module)
        if current is None:
            external = True  # pai declarado mas ausente no repositório
            break

    return chain, external


def effective_properties(pom: Pom, by_module: dict[str, Pom]) -> tuple[dict[str, str], bool]:
    """Propriedades do módulo somadas às herdadas. O filho vence o pai, como no Maven."""
    chain, external = parent_chain(pom, by_module)
    merged: dict[str, str] = {}
    for ancestor in reversed(chain):  # mais distante primeiro; filho sobrescreve
        merged.update(ancestor.properties)
    return merged, external
