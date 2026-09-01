"""Sorteio estratificado determinístico do piloto.

DESVIO REGISTRADO em relação ao desenho original
----------------------------------
O desenho original lista "trajetória de baseline (≥1 upgrade grande · sem upgrade observado)" como
dimensão de estrato. Essa dimensão **não é observável no momento do sorteio**: conhecê-la
exige o histórico completo, que só existe a partir da Fase 4 — e o sorteio precisa
acontecer antes, porque o piloto valida justamente as fases 5–6.

Substituída pela **faixa de baseline no HEAD**, observável no shallow probe:

    legacy  <= 11   (ficou para trás; se o projeto é antigo, quase certamente não migrou)
    mid     12..16
    modern  >= 17   (projeto antigo em baseline moderna ⇒ migrou em algum ponto)

É um proxy da trajetória, não a trajetória. A cobertura real de eventos de upgrade só
pode ser conferida depois da Fase 7; se os 15 sorteados não contiverem nenhum upgrade
grande, o piloto precisa ser aumentado — e isso deve ser reportado, não escondido.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

BASELINE_BANDS = ("legacy", "mid", "modern", "unknown")


def baseline_band(version: int | None) -> str:
    if version is None:
        return "unknown"
    if version <= 11:
        return "legacy"
    if version >= 17:
        return "modern"
    return "mid"


def stratum_key(row: Any) -> str:
    build = row["build_system"] or "none"
    structure = "multi" if (row["module_count"] or 0) > 1 else "mono"
    parent = "external_parent" if row["has_external_parent"] else "internal_parent"
    return f"{build}|{structure}|{parent}|{baseline_band(row['head_baseline'])}"


def _rank(seed: int, project_id: str) -> str:
    return hashlib.sha256(f"{seed}:{project_id}".encode()).hexdigest()


@dataclass(frozen=True)
class Draw:
    project_id: str
    stratum: str
    rank: int


def stratified_draw(rows: Sequence[Any], seed: int, n: int) -> list[Draw]:
    """Cobre toda célula não-vazia antes de repetir qualquer estrato.

    Totalmente determinístico: mesma entrada e mesma seed produzem a mesma saída,
    independentemente da ordem em que as linhas chegam.
    """
    buckets: dict[str, list[str]] = {}
    for row in rows:
        buckets.setdefault(stratum_key(row), []).append(row["project_id"])
    for key in buckets:
        buckets[key].sort(key=lambda pid: _rank(seed, pid))

    order = sorted(buckets, key=lambda k: (_rank(seed, k), k))
    picked: list[Draw] = []
    depth = 0
    while len(picked) < n and any(len(v) > depth for v in buckets.values()):
        for key in order:
            if len(picked) >= n:
                break
            if len(buckets[key]) > depth:
                picked.append(Draw(buckets[key][depth], key, len(picked) + 1))
        depth += 1
    return picked


def stratum_matrix(rows: Sequence[Any]) -> dict[str, int]:
    matrix: dict[str, int] = {}
    for row in rows:
        key = stratum_key(row)
        matrix[key] = matrix.get(key, 0) + 1
    return dict(sorted(matrix.items()))
