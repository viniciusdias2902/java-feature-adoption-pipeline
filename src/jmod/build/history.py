"""Fase 4: reduz o histórico ao que pode mudar a baseline.

Um projeto com 50.000 commits costuma ter algumas centenas que tocam arquivos de build.
Toda fase de baseline trabalha sobre esse subconjunto, nunca sobre o histórico inteiro.

Também produz o mapa **arquivo → módulo**, que é o que torna a elegibilidade por módulo
computável: o módulo dono de um `.java` é o ancestral mais próximo que
tem arquivo de build.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from jmod.repo.git import FileChange, Repo

# Fontes de nível 1–5 (definem baseline).
PRIMARY_BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "gradle.properties")

# Fontes de nível 6–7: corroboram, nunca definem sozinhas.
AUXILIARY_BUILD_FILES = (".sdkmanrc", ".java-version", ".tool-versions")

MODULE_MARKERS = ("pom.xml", "build.gradle", "build.gradle.kts")

PATHSPECS: tuple[str, ...] = (
    *(f"**/{name}" for name in PRIMARY_BUILD_FILES),
    *PRIMARY_BUILD_FILES,
    *(f"**/{name}" for name in AUXILIARY_BUILD_FILES),
    *AUXILIARY_BUILD_FILES,
    # Plugins de convenção declaram a versão fora de build.gradle — ver limitação L-03.
    "buildSrc/**",
    ".github/workflows/**",
    "**/Dockerfile*",
    "Dockerfile*",
)


def is_primary(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in PRIMARY_BUILD_FILES


def build_file_history(repo: Repo, ref: str) -> Iterator[FileChange]:
    """Commits que tocam qualquer arquivo capaz de influenciar a baseline."""
    yield from repo.log_changes(ref, *PATHSPECS)


@dataclass(frozen=True)
class ModuleMap:
    """Raízes de módulo num commit, ordenadas da mais profunda para a mais rasa."""

    roots: tuple[str, ...]

    @classmethod
    def at(cls, repo: Repo, sha: str) -> ModuleMap:
        # `git ls-tree` trata pathspec como prefixo literal — não aceita curinga, ao
        # contrário de `git log`. Listar a árvore e filtrar por nome-base é o caminho
        # correto. Custa uma listagem de nomes por commit consultado, não por commit
        # do histórico: o mapa só é construído onde um arquivo de build mudou.
        markers = repo.ls_files(sha)
        roots = set()
        for path in markers:
            if path.rsplit("/", 1)[-1] not in MODULE_MARKERS:
                continue
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            roots.add(parent)
        return cls(tuple(sorted(roots, key=lambda p: (-p.count("/"), -len(p), p))))

    def owner_of(self, path: str) -> str | None:
        """Módulo dono de um arquivo: a raiz de módulo mais próxima acima dele.

        Retorna `""` para a raiz do repositório, `None` quando não há módulo algum.
        """
        norm = path.lstrip("./")
        for root in self.roots:
            if root == "":
                continue
            if norm.startswith(f"{root}/"):
                return root
        return "" if "" in self.roots else None

    def __len__(self) -> int:
        return len(self.roots)
