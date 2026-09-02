"""Fase 12: proxies de oportunidade.

"% de elegíveis que adotam" ignora quem não tinha o que modernizar. Estes proxies contam
os SÍTIOS onde a feature caberia:

* `instanceof_cast` — `x instanceof T` SEM binding, com cast para o mesmo `T` no bloco
  consequente do `if`. É o complemento exato do detector de pattern matching, e o par
  instanceof+cast é precisamente o que o JEP 394 substitui.
* `attributive_switch` — switch clássico (dois-pontos) em que todo braço não-default é
  uma única atribuição/return/throw (+ break). É a forma que o JEP 361 converte
  mecanicamente em expressão.

Heurísticas declaradas como tal: a precisão delas é medida na validação (Fase 14), e
ambas são contadas no HEAD — denominador aproximado, não série histórica (decisão em
registro de decisões).
"""

from __future__ import annotations

from collections.abc import Iterator

from tree_sitter import Node, Tree

from jmod.features.parser import text_of, walk

PROXIES = ("instanceof_cast", "attributive_switch")


def count_proxy(proxy_id: str, tree: Tree, source: bytes) -> int:
    if proxy_id == "instanceof_cast":
        return sum(1 for _ in _instanceof_cast_sites(tree, source))
    if proxy_id == "attributive_switch":
        return sum(1 for _ in _attributive_switch_sites(tree))
    raise KeyError(proxy_id)


def _instanceof_cast_sites(tree: Tree, source: bytes) -> Iterator[Node]:
    for node in walk(tree.root_node):
        if node.type != "instanceof_expression":
            continue
        if node.child_by_field_name("name") is not None:
            continue  # já é pattern matching
        right = node.child_by_field_name("right")
        if right is None:
            continue
        type_text = text_of(source, right)

        current: Node | None = node
        while current is not None and current.type != "if_statement":
            current = current.parent
        if current is None:
            continue
        consequence = current.child_by_field_name("consequence")
        if consequence is None:
            continue
        for inner in walk(consequence):
            if inner.type == "cast_expression":
                cast_type = inner.child_by_field_name("type")
                if cast_type is not None and text_of(source, cast_type) == type_text:
                    yield node
                    break


def _attributive_switch_sites(tree: Tree) -> Iterator[Node]:
    for node in walk(tree.root_node):
        if node.type != "switch_expression":
            continue
        body = node.child_by_field_name("body")
        if body is None:
            continue
        groups = [c for c in body.children if c.type == "switch_block_statement_group"]
        if not groups or any(c.type == "switch_rule" for c in body.children):
            continue  # já é (ou mistura) forma de seta
        if all(_is_attributive(group) for group in groups):
            yield node


def _is_attributive(group: Node) -> bool:
    statements = [c for c in group.children if c.type not in ("switch_label", ":", "{", "}", ";")]
    if not statements:
        return True  # fallthrough vazio: agrupável
    if len(statements) == 1:
        first = statements[0]
        if first.type in ("return_statement", "throw_statement"):
            return True
        # atribuição única sem break: comum no último grupo, onde o break é dispensável
        return first.type == "expression_statement" and any(
            c.type == "assignment_expression" for c in first.children
        )
    if len(statements) == 2 and statements[1].type == "break_statement":
        first = statements[0]
        if first.type != "expression_statement":
            return False
        return any(c.type == "assignment_expression" for c in first.children)
    return False
