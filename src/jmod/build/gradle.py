"""Fase 6: baseline Java em projetos Gradle (Groovy e Kotlin DSL).

Esta é a fase mais frágil da pipeline, e a fragilidade é **estrutural, não de
implementação**: `build.gradle` é Groovy Turing-completo. `languageVersion.of(jdkVar)`,
convention plugins em `buildSrc/`, lógica condicional e plugins de terceiros não são
observáveis estaticamente. Nenhuma quantidade de esforço aqui resolve isso — só executar
o Gradle resolveria, e isso é inviável por commit em escala.

O desenho assume a limitação em vez de disfarçá-la:

* extrai apenas declarações **inequívocas**;
* resolve variáveis simples (`gradle.properties`, `def`/`val`, `ext`) e **desiste** do resto,
  registrando o texto bruto irresolvível;
* rastreia `subprojects {}` / `allprojects {}`, que propagam para os submódulos;
* nunca infere: valor não resolvido vira `None`, não um palpite.

O que sai daqui alimenta a taxa de extração que dispara (ou não) o gatilho de cobertura.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jmod.build.java_versions import parse_java_version

# ------------------------------------------------------------------ padrões
# Nível 3 (strong): toolchain é a forma canônica moderna.
TOOLCHAIN = re.compile(
    r"languageVersion\s*(?:=|\.set\(|\.value\()\s*"
    r"(?:JavaLanguageVersion\.of\(\s*)?([^)\n,}]+)"
)
# Nível 4 (medium): compatibilidade fonte/alvo.
COMPATIBILITY = re.compile(
    r"\b(source|target)Compatibility\s*(?:=|\.set\(|\s)\s*([^\n;)}]+)",
    re.IGNORECASE,
)
# Nível 3: `options.release` do JavaCompile equivale a --release.
RELEASE_OPTION = re.compile(
    r"\b(?:options\.)?release\s*(?:=|\.set\(|\.value\()\s*([^\n;)}]+)",
)

VARIABLE_DEF = re.compile(
    r"^\s*(?:def|val|var|ext\.)\s+([A-Za-z_][\w.]*)\s*=\s*([^\n]+)", re.MULTILINE
)
EXT_ASSIGN = re.compile(r"^\s*([A-Za-z_][\w]*)\s*=\s*([^\n]+)", re.MULTILINE)
VAR_REF = re.compile(r"\$\{?([A-Za-z_][\w.]*)\}?|^([A-Za-z_][\w.]*)$")

PROPAGATING_BLOCKS = frozenset({"subprojects", "allprojects"})

# `plugins { id("x") }`, `plugins { id 'x' }`, `apply plugin: 'x'`
APPLIED_PLUGIN = re.compile(r"""(?:\bid\s*[(\s]\s*["'`]|apply\s+plugin:\s*["'])([\w.\-]+)""")

CONVENTION_SUFFIXES = (".gradle.kts", ".gradle")


@dataclass(frozen=True)
class GradleDeclaration:
    source: str
    raw_value: str
    version: int | None
    scope: str  # own | subprojects | allprojects
    line: int


def strip_comments(text: str) -> str:
    """Remove comentários preservando posições de linha.

    Strings NÃO são removidas: `sourceCompatibility = '1.8'` depende delas.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        two = text[i : i + 2]
        if two == "//":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
        elif two == "/*":
            while i < n and text[i : i + 2] != "*/":
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def block_stack_at(text: str, pos: int) -> list[str]:
    """Nomes dos blocos que contêm a posição dada, do mais externo ao mais interno."""
    stack: list[str] = []
    for i in range(min(pos, len(text))):
        char = text[i]
        if char == "{":
            j = i - 1
            while j >= 0 and text[j] in " \t\r\n":
                j -= 1
            end = j + 1
            while j >= 0 and (text[j].isalnum() or text[j] in "._"):
                j -= 1
            stack.append(text[j + 1 : end])
        elif char == "}" and stack:
            stack.pop()
    return stack


def _scope_for(stack: list[str]) -> str:
    for name in stack:
        if name in PROPAGATING_BLOCKS:
            return name
    return "own"


def collect_variables(text: str, external: dict[str, str] | None = None) -> dict[str, str]:
    """Variáveis resolvíveis: `gradle.properties`, `def`/`val`/`var`, e atribuições em `ext {}`."""
    variables: dict[str, str] = dict(external or {})
    for name, value in VARIABLE_DEF.findall(text):
        variables[name.rsplit(".", 1)[-1]] = value.strip()
    for match in re.finditer(r"\bext\s*\{", text):
        end = _matching_brace(text, match.end() - 1)
        for name, value in EXT_ASSIGN.findall(text[match.end() : end]):
            variables[name] = value.strip()
    return variables


def _matching_brace(text: str, open_pos: int) -> int:
    depth = 0
    for i in range(open_pos, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def resolve_value(raw: str, variables: dict[str, str], depth: int = 0) -> str:
    """Substitui referências simples de variável. Devolve o texto original se não souber."""
    if depth > 5:
        return raw
    text = raw.strip().rstrip("),;").strip()
    match = VAR_REF.fullmatch(text)
    if match is not None:
        name = (match.group(1) or match.group(2) or "").rsplit(".", 1)[-1]
        if name in variables:
            return resolve_value(variables[name], variables, depth + 1)
        return text
    if "$" in text:

        def sub(m: re.Match[str]) -> str:
            key = (m.group(1) or m.group(2) or "").rsplit(".", 1)[-1]
            return variables.get(key, m.group(0))

        replaced = re.sub(r"\$\{?([A-Za-z_][\w.]*)\}?", sub, text)
        if replaced != text:
            return resolve_value(replaced, variables, depth + 1)
    return text


def parse_gradle(
    content: bytes, variables: dict[str, str] | None = None
) -> list[GradleDeclaration]:
    """Declarações de versão num arquivo de build Gradle."""
    text = strip_comments(content.decode("utf-8", errors="replace"))
    variables = collect_variables(text, variables)
    found: list[GradleDeclaration] = []

    def emit(source: str, raw: str, pos: int) -> None:
        resolved = resolve_value(raw, variables)
        found.append(
            GradleDeclaration(
                source=source,
                raw_value=raw.strip(),
                version=parse_java_version(resolved),
                scope=_scope_for(block_stack_at(text, pos)),
                line=text.count("\n", 0, pos) + 1,
            )
        )

    for m in TOOLCHAIN.finditer(text):
        emit("gradle_toolchain", m.group(1), m.start())
    for m in COMPATIBILITY.finditer(text):
        emit(f"gradle_{m.group(1).lower()}_compatibility", m.group(2), m.start())
    for m in RELEASE_OPTION.finditer(text):
        emit("gradle_release_option", m.group(1), m.start())

    return found


def parse_gradle_properties(content: bytes) -> dict[str, str]:
    """`gradle.properties`: pares chave=valor, ignorando comentários."""
    out: dict[str, str] = {}
    for line in content.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        key, sep, value = stripped.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def convention_plugin_id(path: str) -> str | None:
    """Id do plugin de convenção a partir do caminho do arquivo em `buildSrc/`.

    O Gradle deriva o id do nome do arquivo: `qupath.java-conventions.gradle.kts` é
    aplicado como `id("qupath.java-conventions")`. Esse vínculo é determinístico, o que
    permite ligar a declaração ao módulo que a aplica sem executar nada.
    """
    if not path.startswith("buildSrc/"):
        return None
    name = path.rsplit("/", 1)[-1]
    for suffix in CONVENTION_SUFFIXES:
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            # `build.gradle` do próprio buildSrc não é plugin de convenção.
            return stem if stem and stem != "build" else None
    return None


def applied_plugins(content: bytes) -> set[str]:
    """Ids de plugin aplicados num arquivo de build."""
    text = strip_comments(content.decode("utf-8", errors="replace"))
    return set(APPLIED_PLUGIN.findall(text))
