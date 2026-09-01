"""Carga e validação da configuração externa.

Falha alto e cedo: uma configuração inconsistente invalida todos os resultados
derivados, então é melhor abortar na carga do que descobrir na análise.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class ConfigError(ValueError):
    """Configuração inconsistente. Sempre fatal."""


@dataclass(frozen=True)
class Feature:
    id: str
    name: str
    jep: int
    stable: int
    preview: tuple[int, ...]
    node: str
    recall_note: str
    candidate_literal: str | None = None
    candidate_regex: str | None = None

    def eligible_at(self, java_version: int, *, preview_counts: bool) -> bool:
        """A feature está disponível para código compilado nesta baseline?"""
        if java_version >= self.stable:
            return True
        return preview_counts and java_version in self.preview


@dataclass(frozen=True)
class Study:
    freeze_date: date
    window_start: date
    seed: int
    sampling: dict[str, Any]
    history: dict[str, Any]
    eligibility: dict[str, Any]
    outcomes: dict[str, Any]
    pilot: dict[str, Any]
    opportunity: dict[str, Any]
    paths: dict[str, Any]
    baseline: dict[str, Any]
    storage: dict[str, Any]
    features: tuple[Feature, ...] = field(default=())

    def feature(self, feature_id: str) -> Feature:
        for f in self.features:
            if f.id == feature_id:
                return f
        raise KeyError(feature_id)


def _as_date(raw: Any, label: str) -> date:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise ConfigError(f"{label}: data inválida {raw!r}") from exc


def load_features(path: Path | None = None) -> tuple[Feature, ...]:
    path = path or CONFIG_DIR / "features.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    features: list[Feature] = []
    for entry in raw.get("feature", []):
        f = Feature(
            id=entry["id"],
            name=entry["name"],
            jep=int(entry["jep"]),
            stable=int(entry["stable"]),
            preview=tuple(int(v) for v in entry.get("preview", [])),
            node=entry["node"],
            recall_note=entry.get("recall_note", ""),
            candidate_literal=entry.get("candidate_literal"),
            candidate_regex=entry.get("candidate_regex"),
        )
        if not f.candidate_literal and not f.candidate_regex:
            raise ConfigError(f"feature {f.id}: precisa de candidate_literal ou candidate_regex")
        if f.preview and max(f.preview) >= f.stable:
            raise ConfigError(f"feature {f.id}: versão preview >= estável")
        features.append(f)
    if not features:
        raise ConfigError("catálogo de features vazio")
    ids = [f.id for f in features]
    if len(set(ids)) != len(ids):
        raise ConfigError("ids de feature duplicados no catálogo")
    return tuple(features)


def load_study(path: Path | None = None, features_path: Path | None = None) -> Study:
    path = path or CONFIG_DIR / "study.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    freeze = _as_date(raw["study"]["freeze_date"], "freeze_date")
    start = _as_date(raw["study"]["window_start"], "window_start")
    if start >= freeze:
        raise ConfigError("window_start deve preceder freeze_date")

    outcomes = raw["outcomes"]
    if outcomes["sustained_min_files"] > outcomes["sustained_min_occurrences"]:
        raise ConfigError("sustained_min_files não pode exceder sustained_min_occurrences")
    if outcomes["primary_scope"] not in outcomes["tracked_scopes"]:
        raise ConfigError("primary_scope precisa estar em tracked_scopes")

    for key in ("sustained_window_days", "abandonment_window_days"):
        if outcomes[key] <= 0:
            raise ConfigError(f"{key} deve ser positivo")
    if raw["eligibility"]["upgrade_min_days"] < 0:
        raise ConfigError("upgrade_min_days não pode ser negativo")

    study = Study(
        freeze_date=freeze,
        window_start=start,
        seed=int(raw["study"]["seed"]),
        sampling=raw["sampling"],
        history=raw["history"],
        eligibility=raw["eligibility"],
        outcomes=outcomes,
        pilot=raw["pilot"],
        opportunity=raw["opportunity"],
        paths=raw["paths"],
        baseline=raw["baseline"],
        storage=raw["storage"],
        features=load_features(features_path),
    )

    known = {f.id for f in study.features}
    unknown_pilot = set(study.pilot["labeled_features"]) - known
    if unknown_pilot:
        raise ConfigError(f"pilot.labeled_features desconhecidas: {sorted(unknown_pilot)}")
    unknown = set(study.opportunity["proxies"]) - {"instanceof_cast", "attributive_switch"}
    if unknown:
        raise ConfigError(f"proxies de oportunidade desconhecidos: {sorted(unknown)}")
    if not known:
        raise ConfigError("nenhuma feature carregada")
    return study
