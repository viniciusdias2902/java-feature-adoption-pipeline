"""Detectores estruturais, um por feature do catálogo.

Toda detecção é **estrutural**: casa nós concretos da AST, nunca texto. As regras vêm de
nomes verificados contra a gramática real do tree-sitter.

As três armadilhas que os detectores ingênuos erram:

* `record`, `sealed`, `permits`, `yield` e `var` são *contextual keywords* — `class record {}`
  e `int var = 5;` são Java 8 válido;
* `var` **não é distinguível pelo tipo de nó**: `var s = 1;` e `int var = 1;` produzem
  ambos um `local_variable_declaration`. Só o campo `type` separa;
* a gramática chama **todo** switch de `switch_expression`; não existe `switch_statement`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from tree_sitter import Node, Tree

from jmod.features.parser import text_of, walk


@dataclass(frozen=True)
class Hit:
    """Uma ocorrência confirmada estruturalmente."""

    feature_id: str
    node_subtype: str | None
    line: int
    byte_start: int


Detector = Callable[[Tree, bytes], Iterator[Hit]]
_REGISTRY: dict[str, Detector] = {}


def register(feature_id: str) -> Callable[[Detector], Detector]:
    def wrap(fn: Detector) -> Detector:
        _REGISTRY[feature_id] = fn
        return fn

    return wrap


def detect(feature_id: str, tree: Tree, source: bytes) -> list[Hit]:
    if feature_id not in _REGISTRY:
        raise KeyError(f"sem detector para {feature_id!r}")
    return list(_REGISTRY[feature_id](tree, source))


def registered() -> frozenset[str]:
    return frozenset(_REGISTRY)


def _hit(feature_id: str, node: Node, subtype: str | None = None) -> Hit:
    return Hit(feature_id, subtype, node.start_point[0] + 1, node.start_byte)


# ---------------------------------------------------------------------- records
@register("record")
def _records(tree: Tree, source: bytes) -> Iterator[Hit]:
    """`class record {}` é Java 8 válido e NÃO produz `record_declaration`."""
    for node in walk(tree.root_node):
        if node.type == "record_declaration":
            yield _hit("record", node)


# -------------------------------------------------------------------------- var
@register("var")
def _var(tree: Tree, source: bytes) -> Iterator[Hit]:
    """O nó é o mesmo de `int var = 1;` — a distinção está só no campo `type`."""
    for node in walk(tree.root_node):
        if node.type not in ("local_variable_declaration", "enhanced_for_statement"):
            continue
        type_node = node.child_by_field_name("type")
        if type_node is None or type_node.type != "type_identifier":
            continue
        if text_of(source, type_node) == "var":
            subtype = "for_each" if node.type == "enhanced_for_statement" else "local"
            yield _hit("var", node, subtype)


# ------------------------------------------------------------------ text blocks
@register("text_block")
def _text_blocks(tree: Tree, source: bytes) -> Iterator[Hit]:
    """Text block é `string_literal` com filho `multiline_string_fragment`.

    String comum tem `string_fragment`. Não há nó `text_block` na gramática.
    """
    for node in walk(tree.root_node):
        if node.type != "string_literal":
            continue
        if any(c.type == "multiline_string_fragment" for c in node.children):
            yield _hit("text_block", node)


# ----------------------------------------------------------------------- sealed
@register("sealed")
def _sealed(tree: Tree, source: bytes) -> Iterator[Hit]:
    """`int sealed = 1;` não gera modificador. `non-sealed` também é a feature."""
    for node in walk(tree.root_node):
        if node.type != "modifiers":
            continue
        words = {text_of(source, c) for c in node.children}
        if "sealed" in words:
            yield _hit("sealed", node, "sealed")
        elif "non-sealed" in words:
            yield _hit("sealed", node, "non_sealed")


# ------------------------------------------------- pattern matching (instanceof)
@register("pattern_instanceof")
def _pattern_instanceof(tree: Tree, source: bytes) -> Iterator[Hit]:
    """Com binding, `instanceof_expression` ganha o campo `name`; sem binding, não tem.

    O complemento exato deste detector é o proxy de oportunidade.
    """
    for node in walk(tree.root_node):
        if node.type != "instanceof_expression":
            continue
        if node.child_by_field_name("name") is not None:
            yield _hit("pattern_instanceof", node, "type_pattern")
        elif any(c.type == "record_pattern" for c in node.children):
            yield _hit("pattern_instanceof", node, "record_pattern")


# ---------------------------------------------------------- switch expressions
@register("switch_expression")
def _switch_expressions(tree: Tree, source: bytes) -> Iterator[Hit]:
    """JEP 361. A gramática não tem `switch_statement`: todo switch é `switch_expression`.

    Conta se qualquer condição valer, e o subtipo registra qual — um switch com seta usado
    como *statement* é sintaxe Java 14, mas sinal de modernização mais fraco do que um
    switch em posição de expressão, e a análise precisa poder separá-los.
    """
    for node in walk(tree.root_node):
        if node.type != "switch_expression":
            continue
        body = node.child_by_field_name("body")
        arrow = body is not None and any(c.type == "switch_rule" for c in body.children)
        yields = any(n.type == "yield_statement" for n in walk(node))
        # Posição de expressão exige pai de contexto de EXPRESSÃO. "pai != block" não
        # basta: um switch clássico aninhado em `case X:`, `if` sem chaves ou label tem
        # pai switch_block_statement_group/if_statement/... e continua sendo statement.
        # (Falso positivo real: qupath, RotatedImageServer.java L97.)
        statement_parents = {
            "block",
            "switch_block_statement_group",
            "switch_rule",
            "labeled_statement",
            "if_statement",
            "while_statement",
            "do_statement",
            "for_statement",
            "enhanced_for_statement",
            "expression_statement",
            "program",
        }
        in_expression = node.parent is not None and node.parent.type not in statement_parents

        if in_expression:
            subtype = "expression_position"
        elif yields:
            subtype = "yield"
        elif arrow:
            subtype = "arrow_statement"
        else:
            continue  # switch clássico com dois-pontos: não é a feature
        yield _hit("switch_expression", node, subtype)


# ------------------------------------------------- pattern matching (switch)
@register("pattern_switch")
def _pattern_switch(tree: Tree, source: bytes) -> Iterator[Hit]:
    """JEP 441: rótulo de `case` que é um padrão de tipo, não uma constante.

    O padrão não é filho direto do `switch_label`: a gramática interpõe um nó `pattern`
    (`switch_label -> pattern -> type_pattern`), então a busca precisa ser por descendente.
    """
    for node in walk(tree.root_node):
        if node.type != "switch_label":
            continue
        kinds = {d.type for d in walk(node)}
        if "record_pattern" in kinds:
            yield _hit("pattern_switch", node, "record_pattern")
        elif "type_pattern" in kinds:
            yield _hit("pattern_switch", node, "type_pattern")


# --------------------------------------------------------------- record patterns
@register("record_pattern")
def _record_patterns(tree: Tree, source: bytes) -> Iterator[Hit]:
    """JEP 440. Aninhados contam uma vez cada, por serem desconstruções distintas."""
    for node in walk(tree.root_node):
        if node.type == "record_pattern":
            nested = any(
                c.type == "record_pattern"
                for comp in node.children
                for c in walk(comp)
                if c is not node
            )
            yield _hit("record_pattern", node, "nested" if nested else "flat")
