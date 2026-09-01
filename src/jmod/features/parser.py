"""Camada tree-sitter: parsing e classificação de escopo.

A gramática cobre Java 8–25 sem produzir nós `ERROR` para nenhum construto do catálogo
(cada uma verificada contra a gramática real). Arquivos com sintaxe futura desconhecida
degradam para nós `ERROR` locais em vez de falhar por inteiro — por isso um arquivo nunca
derruba a mineração.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import tree_sitter_java as tsj
from tree_sitter import Language, Node, Parser, Tree

PRODUCTION = "production"
TEST = "test"
EXAMPLE = "example"
GENERATED = "generated"


@lru_cache(maxsize=1)
def _parser() -> Parser:
    return Parser(Language(tsj.language()))


def parse(source: bytes) -> Tree:
    return _parser().parse(source)


def walk(node: Node) -> Iterator[Node]:
    """Percurso em profundidade, incluindo o próprio nó."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def text_of(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def has_errors(tree: Tree) -> bool:
    return tree.root_node.has_error


@dataclass(frozen=True)
class ScopeRules:
    test_globs: tuple[str, ...]
    generated_globs: tuple[str, ...]
    vendored_globs: tuple[str, ...]
    example_globs: tuple[str, ...]
    header_pattern: re.Pattern[str]
    header_lines: int

    @classmethod
    def from_config(cls, paths: dict[str, Any]) -> ScopeRules:
        return cls(
            test_globs=tuple(paths["test_globs"]),
            generated_globs=tuple(paths["generated_globs"]),
            vendored_globs=tuple(paths["vendored_globs"]),
            example_globs=tuple(paths["example_globs"]),
            header_pattern=re.compile(paths["generated_header_pattern"]),
            header_lines=int(paths["generated_header_lines"]),
        )

    def classify(self, path: str, source: bytes | None = None) -> str:
        """Escopo de um arquivo.

        Ordem importa: `generated` vence `test`, porque um arquivo gerado dentro de
        `src/test` continua sendo código que ninguém escreveu.
        """
        norm = path.replace("\\", "/").lstrip("./")
        if _matches(norm, self.generated_globs) or _matches(norm, self.vendored_globs):
            return GENERATED
        if source is not None and self._generated_header(source):
            return GENERATED
        if _matches(norm, self.test_globs):
            return TEST
        if _matches(norm, self.example_globs):
            return EXAMPLE
        return PRODUCTION

    def _generated_header(self, source: bytes) -> bool:
        head = source[:8192].decode("utf-8", errors="replace").splitlines()[: self.header_lines]
        return any(self.header_pattern.search(line) for line in head)


def _matches(path: str, globs: tuple[str, ...]) -> bool:
    candidates = (path, f"/{path}")
    return any(fnmatch.fnmatch(c, g) for g in globs for c in candidates)
