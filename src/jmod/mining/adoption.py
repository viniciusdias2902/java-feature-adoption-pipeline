"""Fase 10: mineração histórica do uso de features.

Estratégia (PLAN.md): candidatos por pickaxe -> leitura dirigida dos arquivos tocados ->
confirmação estrutural por AST -> série temporal incremental. Custo O(alterações
candidatas), nunca O(commits × arquivos).

O que se persiste, e por quê:

* `feature_series` — contagem corrente (ocorrências, arquivos) por escopo, um ponto por
  commit candidato que a altere. É dela que saem first use, sustained e abandonment.
* `feature_occurrences` — detalhe (arquivo, linha, subtipo) apenas do commit de primeiro
  uso em cada escopo: é a evidência auditável, sem explodir volume com cada ocorrência
  de cada commit da história.
* invariante de recall — a contagem final da série tem de bater com uma varredura AST
  completa do HEAD. Divergência é defeito da estratégia de candidatos, nunca dado.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jmod.config import Feature
from jmod.features.detectors import Hit, detect
from jmod.features.parser import ScopeRules, parse
from jmod.repo.git import FileChange, GitError, Repo


@dataclass(frozen=True)
class SeriesPoint:
    sha: str
    committed_at: str
    scope: str
    occurrence_count: int
    file_count: int


@dataclass(frozen=True)
class FirstUseEvidence:
    scope: str
    sha: str
    committed_at: str
    path: str
    hits: tuple[Hit, ...]


@dataclass
class FeatureMining:
    feature_id: str
    strategy: str
    candidates: list[FileChange] = field(default_factory=list)
    series: list[SeriesPoint] = field(default_factory=list)
    first_uses: list[FirstUseEvidence] = field(default_factory=list)


class _DetectCache:
    """Blobs se repetem entre commits (reverts, renames, merges retocados): o resultado
    da detecção é função pura do conteúdo, então cacheia por OID."""

    def __init__(self, limit: int = 50_000) -> None:
        self._data: dict[tuple[str, str], tuple[Hit, ...]] = {}
        self._limit = limit

    def get(self, oid: str | None, feature_id: str) -> tuple[Hit, ...] | None:
        if oid is None:
            return None
        return self._data.get((oid, feature_id))

    def put(self, oid: str | None, feature_id: str, hits: tuple[Hit, ...]) -> None:
        if oid is None or len(self._data) >= self._limit:
            return
        self._data[(oid, feature_id)] = hits


def mine_feature(
    repo: Repo,
    ref: str,
    feature: Feature,
    rules: ScopeRules,
    cache: _DetectCache | None = None,
) -> FeatureMining:
    strategy = "pickaxe_S" if feature.candidate_literal else "pickaxe_G"
    cache = cache or _DetectCache()
    result = FeatureMining(feature.id, strategy)

    per_commit: dict[str, list[FileChange]] = {}
    dates: dict[str, str] = {}
    order: list[str] = []
    for change in repo.pickaxe(
        ref,
        literal=feature.candidate_literal,
        regex=feature.candidate_regex,
        pathspecs=("*.java",),
    ):
        result.candidates.append(change)
        if change.sha not in per_commit:
            per_commit[change.sha] = []
            dates[change.sha] = change.committed_at
            order.append(change.sha)
        per_commit[change.sha].append(change)
    order.reverse()  # do mais antigo para o mais novo

    state: dict[str, tuple[str, int]] = {}  # path -> (scope, contagem)
    last_totals: dict[str, tuple[int, int]] = {}  # scope -> (ocorrências, arquivos)
    seen_production: dict[str, bool] = {}  # scope -> já teve uso?

    for sha in order:
        touched_hits: dict[str, tuple[str, tuple[Hit, ...]]] = {}
        for change in per_commit[sha]:
            if change.old_path:
                state.pop(change.old_path, None)
            if change.change_type == "D":
                state.pop(change.path, None)
                continue
            hits = cache.get(change.blob_oid, feature.id)
            scope: str | None = None
            if hits is None:
                try:
                    content = repo.show(sha, change.path)
                except GitError:
                    state.pop(change.path, None)
                    continue
                scope = rules.classify(change.path, content)
                hits = tuple(detect(feature.id, parse(content), content))
                cache.put(change.blob_oid, feature.id, hits)
            if scope is None:
                scope = rules.classify(change.path)
            if hits:
                state[change.path] = (scope, len(hits))
                touched_hits[change.path] = (scope, hits)
            else:
                state.pop(change.path, None)

        totals: dict[str, tuple[int, int]] = {}
        for _path, (scope, count) in state.items():
            occ, files = totals.get(scope, (0, 0))
            totals[scope] = (occ + count, files + 1)

        for scope in set(totals) | set(last_totals):
            now = totals.get(scope, (0, 0))
            if now == last_totals.get(scope, (0, 0)):
                continue
            result.series.append(SeriesPoint(sha, dates[sha], scope, now[0], now[1]))
            if now[0] > 0 and not seen_production.get(scope):
                seen_production[scope] = True
                for path, (path_scope, hits) in sorted(touched_hits.items()):
                    if path_scope == scope:
                        result.first_uses.append(
                            FirstUseEvidence(scope, sha, dates[sha], path, hits)
                        )
        last_totals = {k: v for k, v in totals.items()}

    return result


def scan_head(
    repo: Repo, ref: str, features: tuple[Feature, ...], rules: ScopeRules
) -> dict[tuple[str, str], int]:
    """Varredura AST completa do HEAD: contagem por (feature, escopo).

    É o lado direito da invariante de recall. Cada arquivo é parseado uma única vez e
    todos os detectores rodam sobre a mesma árvore.
    """
    counts: dict[tuple[str, str], int] = {}
    head = repo.head_sha(ref)
    for path in repo.ls_files(head):
        if not path.endswith(".java"):
            continue
        try:
            content = repo.show(head, path)
        except GitError:
            continue
        scope = rules.classify(path, content)
        tree = parse(content)
        for feature in features:
            found = detect(feature.id, tree, content)
            if found:
                key = (feature.id, scope)
                counts[key] = counts.get(key, 0) + len(found)
    return counts
