"""Normalização de versão da linguagem Java.

Requisito vindo de D-007: a detecção **tem de rejeitar** valores que não correspondem a
um release real. No probe, uma regex frouxa produziu `java 2` e outros valores impossíveis
— lá era só ruído de estratificação, aqui seria corrupção do dataset.

Formas aceitas, e só elas:

* `1.1` … `1.10` — a notação antiga (`-source 1.8`), normalizada para o número após o ponto
* `5` … `MAX_KNOWN_RELEASE` — a notação moderna
* variações com espaço em volta e aspas simples/duplas (Gradle: `sourceCompatibility = '1.8'`)

`1.0`, `0`, negativos, versões acima do teto e qualquer coisa não-numérica retornam `None`.
"""

from __future__ import annotations

import re

# Teto deliberadamente à frente do release atual, para não rejeitar projetos na ponta.
# Elevar quando a linguagem passar daqui — é um limite de sanidade, não um catálogo.
MAX_KNOWN_RELEASE = 35

# `1.x` só existiu até 1.10; acima disso a notação moderna é a única válida.
_LEGACY = re.compile(r"^1\.(\d{1,2})$")
_MODERN = re.compile(r"^(\d{1,2})$")

# Rótulos do Gradle: JavaVersion.VERSION_1_8, VERSION_11, JavaLanguageVersion.of(17)
_GRADLE_ENUM = re.compile(r"VERSION_(?:1_)?(\d{1,2})$")
_GRADLE_OF = re.compile(r"\bof\(\s*['\"]?(\d{1,2})['\"]?\s*\)")


def parse_java_version(raw: str | None) -> int | None:
    """Versão normalizada, ou None se o texto não for um release Java reconhecível."""
    if raw is None:
        return None
    text = raw.strip().strip("'\"").strip()
    if not text:
        return None

    if (m := _LEGACY.match(text)) is not None:
        value = int(m.group(1))
        # `1.0` não é release de linguagem utilizável; `1.1`–`1.10` são.
        return value if 1 <= value <= 10 else None

    if (m := _MODERN.match(text)) is not None:
        value = int(m.group(1))
        # A notação moderna começa no 5. `1`–`4` só existem como `1.x`, e um `2` solto
        # é quase sempre um falso casamento — foi exatamente o caso de D-007.
        return value if 5 <= value <= MAX_KNOWN_RELEASE else None

    if (m := _GRADLE_ENUM.search(text)) is not None:
        return parse_java_version(m.group(1))

    if (m := _GRADLE_OF.search(text)) is not None:
        return parse_java_version(m.group(1))

    return None


def is_known_release(value: int | None) -> bool:
    return value is not None and 1 <= value <= MAX_KNOWN_RELEASE
