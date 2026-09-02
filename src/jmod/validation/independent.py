"""Fases P e 14: verificação independente dos resultados da pipeline.

Independência METODOLÓGICA, deliberada em três eixos:

1. **Grafo completo, não `--first-parent`** — enxerga o commit original em branch,
   não só a chegada na linha principal. O primeiro uso da pipeline nunca pode ser
   ANTERIOR ao do grafo completo; a diferença esperada é o atraso de merge, que fica
   registrado (`lag_days`).
2. **Candidatos maximamente ingênuos** — um único token por feature (`record`, `var`,
   aspas triplas, `switch`), sem nenhuma das regexes da pipeline. Custa mais AST e não
   herda os pontos cegos delas.
3. **Caminhada do passado para frente com parada** — confirma na AST cada candidato do
   mais antigo ao mais novo e para na primeira confirmação; não mantém estado.

Veredictos por (projeto, feature):
* `both_absent` / `both_present`         — concordância;
* `pipeline_missed`                       — o grafo completo tem uso que a pipeline não viu;
* `pipeline_false_positive`               — a pipeline alegou uso que a releitura da
                                            evidência (arquivo@sha) não confirma;
* `pipeline_earlier_than_truth`           — impossível por construção; se aparecer, é bug.

Eventos de baseline: confirmação TEXTUAL — as linhas cruas de declaração de versão no
commit do evento e no seu pai, sem passar pelos parsers da pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jmod.config import Feature
from jmod.features.detectors import detect
from jmod.features.parser import PRODUCTION, ScopeRules, parse
from jmod.repo.git import GitError, Repo

# Token único e ingênuo por feature — de propósito, nada das regexes da pipeline.
NAIVE_TOKEN = {
    "record": "record",
    "var": "var",
    "text_block": '"""',
    "switch_expression": "switch",
    "pattern_instanceof": "instanceof",
    "sealed": "sealed",
    "pattern_switch": "case",
    "record_pattern": "instanceof",
}

VERSION_LINE = re.compile(
    rb"maven\.compiler|<release>|<source>|<target>|<java\.version>|sourceCompatibility"
    rb"|targetCompatibility|languageVersion|JavaVersion",
)


@dataclass
class FirstUseCheck:
    feature_id: str
    verdict: str
    independent_date: str | None = None
    independent_sha: str | None = None
    independent_path: str | None = None
    pipeline_date: str | None = None
    pipeline_sha: str | None = None
    lag_days: float | None = None
    note: str = ""


@dataclass
class EventCheck:
    sha: str
    kind: str
    to_version: int
    confirmed: bool
    evidence: str = ""


@dataclass
class ProjectDossier:
    project_id: str
    first_uses: list[FirstUseCheck] = field(default_factory=list)
    events: list[EventCheck] = field(default_factory=list)


def independent_first_use(
    repo: Repo, ref: str, feature: Feature, rules: ScopeRules
) -> tuple[str, str, str] | None:
    """(data, sha, arquivo) do primeiro uso em produção no GRAFO COMPLETO, ou None."""
    token = NAIVE_TOKEN[feature.id]
    out = repo.run(
        "log",
        ref,
        "--reverse",
        "--date-order",
        "--format=\x1e%H %cI",
        "--raw",
        "--no-abbrev",
        "--no-renames",
        "--diff-filter=AM",
        f"-S{token}",
        "--",
        "*.java",
        timeout=3600,
    )
    for block in out.split("\x1e"):
        if not block.strip():
            continue
        lines = block.strip("\n").split("\n")
        sha, _, when = lines[0].partition(" ")
        for line in lines[1:]:
            if not line.startswith(":"):
                continue
            meta, _, path = line.partition("\t")
            if "\t" in path:
                path = path.split("\t")[-1]
            try:
                content = repo.show(sha, path)
            except GitError:
                continue
            if rules.classify(path, content) != PRODUCTION:
                continue
            if detect(feature.id, parse(content), content):
                return when, sha, path
    return None


def check_first_use(
    repo: Repo,
    ref: str,
    feature: Feature,
    rules: ScopeRules,
    pipeline_date: str | None,
    pipeline_sha: str | None,
    pipeline_path: str | None,
) -> FirstUseCheck:
    from datetime import datetime

    independent = independent_first_use(repo, ref, feature, rules)

    if independent is None and pipeline_date is None:
        return FirstUseCheck(feature.id, "both_absent")

    if independent is not None and pipeline_date is None:
        when, sha, path = independent
        return FirstUseCheck(
            feature.id,
            "pipeline_missed",
            when,
            sha,
            path,
            note="grafo completo tem uso em produção; pipeline não",
        )

    if independent is None and pipeline_date is not None:
        confirmed = _reconfirm(repo, feature, rules, pipeline_sha, pipeline_path)
        verdict = "both_present" if confirmed else "pipeline_false_positive"
        note = (
            "evidência da pipeline confirmada na releitura; token ingênuo não a "
            "alcançou (limitação do método independente)"
            if confirmed
            else "releitura do arquivo@sha não confirma a alegação"
        )
        return FirstUseCheck(
            feature.id, verdict, None, None, None, pipeline_date, pipeline_sha, None, note
        )

    assert independent is not None and pipeline_date is not None
    when, sha, path = independent
    lag = (
        datetime.fromisoformat(pipeline_date) - datetime.fromisoformat(when)
    ).total_seconds() / 86400
    if lag < -0.01:
        return FirstUseCheck(
            feature.id,
            "pipeline_earlier_than_truth",
            when,
            sha,
            path,
            pipeline_date,
            pipeline_sha,
            round(lag, 1),
            note="IMPOSSÍVEL por construção — investigar",
        )
    return FirstUseCheck(
        feature.id, "both_present", when, sha, path, pipeline_date, pipeline_sha, round(lag, 1)
    )


def _reconfirm(
    repo: Repo, feature: Feature, rules: ScopeRules, sha: str | None, path: str | None
) -> bool:
    if not sha or not path:
        return False
    try:
        content = repo.show(sha, path)
    except GitError:
        return False
    if rules.classify(path, content) != PRODUCTION:
        return False
    return bool(detect(feature.id, parse(content), content))


def check_version_event(repo: Repo, sha: str, to_version: int, kind: str) -> EventCheck:
    """Confirmação textual: alguma linha de declaração de versão no commit menciona a
    versão-destino, e o estado do pai era diferente. Sem os parsers da pipeline."""
    needles = _version_needles(to_version)
    try:
        now_lines = _version_lines(repo, sha)
        before_lines = _version_lines(repo, f"{sha}^")
    except GitError:
        return EventCheck(sha, kind, to_version, False, "commit/pai ilegível")

    hits_now = [ln for ln in now_lines if any(n in ln for n in needles)]
    confirmed = bool(hits_now) and now_lines != before_lines
    evidence = hits_now[0][:160] if hits_now else "; ".join(now_lines[:2])[:160]
    return EventCheck(sha, kind, to_version, confirmed, evidence)


def _version_needles(version: int) -> list[str]:
    forms = [
        f">{version}<",
        f"_{version}",
        f"of({version})",
        f"= {version}",
        f"'{version}'",
        f'"{version}"',
        f"={version}",
    ]
    if version <= 10:
        forms += [f"1.{version}", f"1_{version}"]
    return forms


def _version_lines(repo: Repo, rev: str) -> list[str]:
    lines: list[str] = []
    for path in repo.ls_files(repo.head_sha(rev)):
        name = path.rsplit("/", 1)[-1]
        if name not in ("pom.xml", "build.gradle", "build.gradle.kts", "gradle.properties"):
            continue
        try:
            content = repo.show(rev, path)
        except GitError:
            continue
        for raw in content.splitlines():
            if VERSION_LINE.search(raw):
                lines.append(f"{path}: {raw.decode(errors='replace').strip()[:120]}")
    return sorted(set(lines))
