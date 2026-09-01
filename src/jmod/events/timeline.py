"""Integração das Fases 4-7: de um repositório clonado a intervalos e eventos por módulo.

Percurso: histórico dos arquivos de build (Fase 4) -> nos commits que os tocam, parsing
Maven (Fase 5) e Gradle (Fase 6) -> amostras por módulo -> intervalos e eventos (Fase 7).

O custo é dominado por `git show` dos arquivos de build em cada commit relevante — num
clone blobless, cada blob ausente custa uma ida à rede na primeira leitura. O número de
commits relevantes costuma ser ordens de magnitude menor que o histórico.
"""

from __future__ import annotations

from dataclasses import dataclass

from jmod.build.baseline import Observation, gradle_baselines, maven_baselines
from jmod.build.gradle import parse_gradle_properties
from jmod.build.history import build_file_history
from jmod.events.upgrades import Interval, Sample, VersionEvent, build_intervals, derive_events
from jmod.repo.git import FileChange, GitError, Repo

_GRADLE_NAMES = ("build.gradle", "build.gradle.kts")

# O que o backfill pré-busca: as fontes primárias e o buildSrc, em estilo gitignore.
BACKFILL_PATTERNS = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "gradle.properties",
    ".sdkmanrc",
    ".java-version",
    ".tool-versions",
    "buildSrc/",
)


@dataclass(frozen=True)
class ModuleTimeline:
    module: str
    samples: list[Sample]
    intervals: list[Interval]
    events: list[VersionEvent]


def observations_at(repo: Repo, sha: str) -> dict[str, Observation]:
    """Baseline por módulo num commit: Maven e Gradle, com o Maven prevalecendo.

    A precedência segue o nível de evidência: as fontes Maven resolvidas são 1-2, mais fortes que
    qualquer fonte Gradle. Um módulo só usa a observação Gradle quando o Maven não
    resolveu — cobre projetos mistos e migrações de build system.
    """
    paths = repo.ls_files(sha)

    poms: dict[str, bytes] = {}
    gradle_files: dict[str, bytes] = {}
    properties: dict[str, str] = {}
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        try:
            if name == "pom.xml":
                poms[path] = repo.show(sha, path)
            elif name in _GRADLE_NAMES or (
                path.startswith("buildSrc/") and name.endswith((".gradle", ".gradle.kts"))
            ):
                gradle_files[path] = repo.show(sha, path)
            elif path == "gradle.properties":
                properties = parse_gradle_properties(repo.show(sha, path))
        except GitError:
            continue  # blob irrecuperável: segue com o que há

    merged: dict[str, Observation] = {}
    if gradle_files:
        merged.update(gradle_baselines(gradle_files, properties))
    if poms:
        for module, obs in maven_baselines(poms).items():
            existing = merged.get(module)
            if (
                existing is None
                or obs.resolution == "resolved"
                or existing.resolution != "resolved"
            ):
                merged[module] = obs
    return merged


def module_timelines(
    repo: Repo, ref: str, *, persistence_days: int, end_of_observation: str
) -> dict[str, ModuleTimeline]:
    """Linha do tempo de baseline para cada módulo de um repositório.

    Incremental: mantém o estado corrente dos arquivos de build e, a cada commit
    relevante, lê apenas os que mudaram. O custo em `git show` é O(alterações), não
    O(commits × arquivos) — a diferença entre minutos e horas num projeto que versiona
    o POM a cada release.
    """
    repo.backfill(list(BACKFILL_PATTERNS))

    # Mudanças agrupadas por commit, na ordem do log (mais novo primeiro) -> invertida.
    per_commit: dict[str, list[FileChange]] = {}
    dates: dict[str, str] = {}
    order: list[str] = []
    for change in build_file_history(repo, ref):
        if change.sha not in per_commit:
            per_commit[change.sha] = []
            dates[change.sha] = change.committed_at
            order.append(change.sha)
        per_commit[change.sha].append(change)
    order.reverse()  # ordem de aplicação: do mais antigo ao mais novo

    current: dict[str, bytes] = {}  # estado corrente dos arquivos relevantes
    samples: dict[str, list[Sample]] = {}

    for sha in order:
        relevant = False
        for change in per_commit[sha]:
            path = change.path
            name = path.rsplit("/", 1)[-1]
            interesting = (
                name == "pom.xml"
                or name in _GRADLE_NAMES
                or path == "gradle.properties"
                or (path.startswith("buildSrc/") and name.endswith((".gradle", ".gradle.kts")))
            )
            if change.old_path and change.old_path in current:
                current.pop(change.old_path, None)
                relevant = True
            if not interesting:
                continue
            relevant = True
            if change.change_type == "D":
                current.pop(path, None)
                continue
            try:
                current[path] = repo.show(sha, path)
            except GitError:
                current.pop(path, None)  # blob irrecuperável: trata como ausente

        if not relevant:
            continue  # só arquivos auxiliares mudaram; baseline intacta

        for module, obs in _observations_from(current).items():
            samples.setdefault(module, []).append(Sample(sha, dates[sha], obs.java_version))

    out: dict[str, ModuleTimeline] = {}
    for module, module_samples in samples.items():
        intervals = build_intervals(module_samples)
        events = derive_events(
            intervals, persistence_days=persistence_days, end_of_observation=end_of_observation
        )
        out[module] = ModuleTimeline(module, module_samples, intervals, events)
    return out


def _observations_from(files: dict[str, bytes]) -> dict[str, Observation]:
    """Observações por módulo a partir de um estado de arquivos já materializado."""
    poms: dict[str, bytes] = {}
    gradle_files: dict[str, bytes] = {}
    properties: dict[str, str] = {}
    for path, content in files.items():
        name = path.rsplit("/", 1)[-1]
        if name == "pom.xml":
            poms[path] = content
        elif path == "gradle.properties":
            properties = parse_gradle_properties(content)
        else:
            gradle_files[path] = content

    merged: dict[str, Observation] = {}
    if gradle_files:
        merged.update(gradle_baselines(gradle_files, properties))
    if poms:
        for module, obs in maven_baselines(poms).items():
            existing = merged.get(module)
            if (
                existing is None
                or obs.resolution == "resolved"
                or existing.resolution != "resolved"
            ):
                merged[module] = obs
    return merged
