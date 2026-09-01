"""Leitura do export CSV do SEART GHS e aplicação dos filtros de seleção.

O SEART não tem API estável para export em lote: o CSV é baixado da interface web e
entra versionado em `data/raw/`. Os nomes de coluna variam entre versões da ferramenta,
então o mapeamento é por alias e **falha alto** listando o cabeçalho real quando não
reconhece uma coluna obrigatória — melhor abortar do que silenciosamente ler NULL.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from jmod.config import Study

# Cada campo lógico -> aliases aceitos, em ordem de preferência.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "full_name": ("name", "full_name", "nameWithOwner", "repository"),
    "is_fork": ("isFork", "is_fork", "fork"),
    "stars": ("stargazers", "stargazerCount", "stars", "starsCount"),
    "commits": ("commits", "commitsCount", "totalCommits"),
    "created_at": ("createdAt", "created_at"),
    "last_commit_at": ("lastCommit", "pushedAt", "last_commit", "lastCommitDate"),
    "default_branch": ("defaultBranch", "default_branch"),
    "loc_java": ("totalLinesOfCode", "linesOfCode", "loc", "codeLines", "size"),
    "is_archived": ("isArchived", "archived"),
    "main_language": ("mainLanguage", "language", "primaryLanguage"),
}

REQUIRED = ("full_name", "stars", "commits", "created_at", "last_commit_at")


class SeartFormatError(ValueError):
    """Cabeçalho do CSV não reconhecido."""


@dataclass(frozen=True)
class SeartRow:
    full_name: str
    stars: int
    commits: int
    loc_java: int | None
    created_at: str | None
    last_commit_at: str | None
    default_branch: str | None
    is_fork: bool
    is_archived: bool
    main_language: str | None


def _resolve(header: list[str]) -> dict[str, str]:
    lower = {h.lower().strip(): h for h in header}
    mapping: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in lower:
                mapping[field] = lower[alias.lower()]
                break
    missing = [f for f in REQUIRED if f not in mapping]
    if missing:
        raise SeartFormatError(
            f"colunas obrigatórias ausentes: {missing}. Cabeçalho recebido: {header}"
        )
    return mapping


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "t"}


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for cut in (10, 19):
        try:
            return date.fromisoformat(text[:cut].replace("T", " ")[:10])
        except ValueError:
            continue
    return None


def read_csv(path: Path) -> Iterator[SeartRow]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise SeartFormatError("CSV sem cabeçalho")
        m = _resolve(list(reader.fieldnames))
        for raw in reader:
            yield SeartRow(
                full_name=str(raw[m["full_name"]]).strip(),
                stars=_int(raw.get(m.get("stars", ""))) or 0,
                commits=_int(raw.get(m.get("commits", ""))) or 0,
                loc_java=_int(raw.get(m.get("loc_java", ""))) if "loc_java" in m else None,
                created_at=str(raw.get(m.get("created_at", ""), "")).strip() or None,
                last_commit_at=str(raw.get(m.get("last_commit_at", ""), "")).strip() or None,
                default_branch=str(raw.get(m.get("default_branch", ""), "")).strip() or None,
                is_fork=_bool(raw.get(m.get("is_fork", ""))),
                is_archived=_bool(raw.get(m.get("is_archived", ""))),
                main_language=str(raw.get(m.get("main_language", ""), "")).strip() or None,
            )


def screen(row: SeartRow, study: Study) -> str | None:
    """Retorna o motivo de exclusão, ou None se o projeto passa nos filtros de seleção.

    Nunca levanta: um projeto rejeitado precisa ir para `exclusions` com motivo.
    """
    s = study.sampling
    if row.is_fork and s["exclude_forks"]:
        return "fork"
    if row.is_archived:
        return "archived"
    if row.main_language and row.main_language.lower() != "java":
        return "not_java"
    if row.stars < s["min_stars"]:
        return "below_min_stars"
    if row.commits < s["min_commits"]:
        return "below_min_commits"
    if row.loc_java is not None and row.loc_java < s["min_loc_java"]:
        return "below_min_loc"

    created = _date(row.created_at)
    if created is None:
        return "missing_created_at"
    if created > date.fromisoformat(str(s["created_before"])):
        return "too_young"

    last = _date(row.last_commit_at)
    if last is None:
        return "missing_last_commit"
    if last < date.fromisoformat(str(s["active_since"])):
        return "inactive"
    return None
