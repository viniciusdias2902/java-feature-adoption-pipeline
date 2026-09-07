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
_STATEMENT_CONTAINERS = frozenset(
    {
        "block",
        "constructor_body",
        "switch_block_statement_group",
        "switch_rule",
        "labeled_statement",
        "expression_statement",
        "program",
    }
)

# Nós que hospedam um statement num campo e uma expressão noutro. As condições de
# `if`/`while`/`do`/`for` nunca caem aqui — a gramática as embrulha em
# `parenthesized_expression` —, mas o corpo sem chaves cai, e o iterável do for-each
# (`for (T x : switch (...) {...})`) é expressão no campo `value`.
_STATEMENT_FIELDS = {
    "if_statement": frozenset({"consequence", "alternative"}),
    "while_statement": frozenset({"body"}),
    "do_statement": frozenset({"body"}),
    "for_statement": frozenset({"body"}),
    "enhanced_for_statement": frozenset({"body"}),
}


def _field_name(parent: Node, child: Node) -> str | None:
    for i in range(parent.child_count):
        if parent.child(i) == child:
            return parent.field_name_for_child(i)
    return None


def _in_expression_position(node: Node) -> bool:
    """O switch produz um valor, em vez de ser executado como statement?

    A gramática chama todo switch de `switch_expression`, então a posição é o que separa
    a feature do switch clássico. A regra é uma *lista de contextos de statement*: tudo
    que não é statement é expressão. A lista precisa ser exaustiva — `constructor_body`
    faltava e fazia todo switch clássico dentro de um construtor virar falso positivo
    (D-016; caso real: mikera/vectorz, `ZeroArray.java:35`).
    """
    parent = node.parent
    if parent is None:
        return False
    if parent.type in _STATEMENT_CONTAINERS:
        return False
    fields = _STATEMENT_FIELDS.get(parent.type)
    if fields is not None:
        return _field_name(parent, node) not in fields
    return True


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

        if _in_expression_position(node):
            subtype = "expression_position"
        elif yields:
            subtype = "yield"
        elif arrow:
            subtype = "arrow_statement"
        else:
            continue  # switch clássico com dois-pontos: não é a feature
        yield _hit("switch_expression", node, subtype)


# ------------------------------------------------ variáveis e padrões anônimos
#: Onde um `identifier` de texto `_` é um *padrão* anônimo. Nesses contextos a gramática
#: não produz `underscore_pattern`; o nó continua sendo um identificador comum.
_PAIS_DE_PADRAO_ANONIMO = frozenset(
    {"type_pattern", "record_pattern_component", "inferred_parameters"}
)


@register("unnamed_pattern")
def _unnamed(tree: Tree, source: bytes) -> Iterator[Hit]:
    """JEP 456, final no Java 22. O `_` aparece sob dois nós distintos.

    Como *variável* anônima (`catch (E _)`, `for (var _ : xs)`, `var _ = f()`,
    `try (var _ = ...)`), a gramática produz `underscore_pattern`. Como *padrão* anônimo
    (`case Integer _ ->`, `Ponto(int x, int _)`, `(k, _) -> ...`), produz um `identifier`
    comum cujo texto é `_`; só o pai o distingue.

    Cuidado herdado das contextual keywords: `int _ = 5;` era legal até o Java 8 e produz
    exatamente o mesmo nó que `var _ = f()`. A gramática não os separa, e nada aqui tenta
    separá-los --- quem faz isso é a elegibilidade (o recurso exige baseline ≥ 22, e
    um projeto com `_` legado está no Java 8) e a exclusão por `pre_eligibility_use`.
    """
    for node in walk(tree.root_node):
        if node.type == "underscore_pattern":
            yield _hit("unnamed_pattern", node, "variable")
        elif (
            node.type == "identifier"
            and text_of(source, node) == "_"
            and node.parent is not None
            and node.parent.type in _PAIS_DE_PADRAO_ANONIMO
        ):
            yield _hit("unnamed_pattern", node, "pattern")


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
