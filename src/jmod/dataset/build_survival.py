"""Fase 13: dataset longitudinal survival-ready.

Uma linha por (projeto, feature) elegível, com:
* `duration_days` — EXPOSIÇÃO elegível de t0 até o first use ou o freeze;
* `event` — 1 adotou, 0 censurado à direita;
* `entry_days` — truncamento à esquerda (elegível antes da janela analítica);
* covariáveis disponíveis nesta fase;
* `cluster` = projeto (um projeto contribui até 8 linhas correlacionadas).

Anomalias (`pre_eligibility_use`, `never_eligible`) ficam FORA das linhas de análise e
DENTRO do relatório de exclusões — contadas, nunca silenciadas.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from jmod import MIN_VERSION_BY_FEATURE
from jmod.config import Study
from jmod.db import Store
from jmod.provenance import PIPELINE_VERSION, new_run_id

PHASE = "dataset"
OUT_DIR = Path("data/dataset")


def _dt(iso: str) -> datetime:
    parsed = datetime.fromisoformat(iso)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


PROXY_OF = {"pattern_instanceof": "instanceof_cast", "switch_expression": "attributive_switch"}


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def mined_versions(store: Store) -> dict[str, str]:
    """Versão da pipeline que minerou cada projeto — a execução `ok` mais recente."""
    return {
        r["project_id"]: r["pipeline_version"]
        for r in store.query(
            "SELECT project_id, pipeline_version FROM run_log "
            "WHERE phase='mine' AND status='ok' AND project_id <> '' "
            "ORDER BY ended_at"
        )
    }


def feature_is_current(versions: dict[str, str], project_id: str, feature_id: str) -> bool:
    """A detecção desta feature neste projeto vale na versão corrente da pipeline?

    Reprocessar a população inteira leva horas, e interromper no meio não pode produzir um
    dataset inválido. A saída é por linha, não por banco: uma feature cuja detecção mudou
    só entra vinda de projetos já reminerados; as demais sete não são afetadas e entram
    sempre. Assim cada projeto reprocessado só acrescenta linhas, e parar a qualquer
    momento deixa um dataset menor porém correto (D-018).
    """
    minimo = MIN_VERSION_BY_FEATURE.get(feature_id)
    if minimo is None:
        return True
    atual = versions.get(project_id)
    if atual is None:
        return False
    return _version_tuple(atual) >= _version_tuple(minimo)


def opportunity_head(
    counts: dict[tuple[str, str], int],
    measured: set[str],
    project_id: str,
    feature_id: str,
) -> int | None:
    """Denominador de oportunidade no HEAD, distinguindo zero de ausente.

    O desenho define proxy só para as duas features de alta confiabilidade; para as outras seis
    o denominador não existe e é `None`. Para essas duas, um projeto medido cujo proxy não
    ocorre no HEAD tem denominador **zero** — a Fase 12 grava uma linha por (proxy, escopo)
    observado, então a ausência da chave não separava "sem oportunidade" de "não medido", e
    158 zeros legítimos viravam nulos (D-017).
    """
    proxy = PROXY_OF.get(feature_id)
    if proxy is None:
        return None
    if (project_id, proxy) in counts:
        return counts[(project_id, proxy)]
    return 0 if project_id in measured else None


def run_dataset(store: Store, study: Study) -> int:
    run = new_run_id()
    window_start = _dt(f"{study.window_start}T00:00:00+00:00")
    freeze = _dt(f"{study.freeze_date}T00:00:00+00:00")

    covariates: dict[str, dict[str, object]] = {}
    for r in store.query(
        "SELECT p.project_id, p.seart_stars stars, p.seart_loc_java loc, "
        "       p.seart_commits commits, p.seart_created_at created, "
        "       pr.build_system, pr.module_count, pr.has_external_parent "
        "FROM projects p JOIN probe pr USING (project_id)"
    ):
        age_years = None
        if r["created"]:
            try:
                age_years = (freeze - _dt(str(r["created"])[:19])).days / 365.25
            except ValueError:
                age_years = None
        covariates[r["project_id"]] = {
            "stars": r["stars"],
            "loc_java": r["loc"],
            "commits": r["commits"],
            "age_years": round(age_years, 2) if age_years else None,
            "build_system": r["build_system"],
            "module_count": r["module_count"],
            "has_external_parent": r["has_external_parent"],
        }

    opportunity: dict[tuple[str, str], int] = {}
    for r in store.query(
        "SELECT project_id, proxy_id, count FROM opportunity_series WHERE scope='production'"
    ):
        opportunity[(r["project_id"], r["proxy_id"])] = r["count"]
    # Projeto medido cujo proxy não ocorre no HEAD tem denominador **zero**, não ausente:
    # a Fase 12 grava uma linha por (proxy, escopo) observado, então a ausência da chave
    # não distingue "sem oportunidade" de "não medido". Qualquer linha de oportunidade,
    # em qualquer escopo, marca o projeto como medido (D-017).
    measured = {
        r["project_id"] for r in store.query("SELECT DISTINCT project_id FROM opportunity_series")
    }

    rows = []
    versions = mined_versions(store)
    excluded = {"pre_eligibility_use": 0, "never_eligible": 0}
    stale: dict[str, int] = {}
    with store.phase_run(run, PHASE):
        for r in store.query(
            "SELECT * FROM adoption_outcomes WHERE scope=?",
            (study.outcomes["primary_scope"],),
        ):
            reason = r["censor_reason"]
            if reason in excluded:
                excluded[reason] += 1
                continue
            if not feature_is_current(versions, r["project_id"], r["feature_id"]):
                stale[r["feature_id"]] = stale.get(r["feature_id"], 0) + 1
                continue
            t0 = _dt(r["eligibility_date"])
            entry = max(0.0, (window_start - t0).days) if t0 < window_start else 0.0
            duration = float(r["time_to_first_use_days"] or 0)
            cov = covariates.get(r["project_id"], {})
            rows.append(
                {
                    "row_id": f"{r['project_id']}::{r['feature_id']}",
                    "project_id": r["project_id"],
                    "feature_id": r["feature_id"],
                    "scope": r["scope"],
                    "entry_days": entry,
                    "duration_days": max(duration, 0.5),  # lifelines exige > 0
                    "event": int(r["event"]),
                    "covariates_json": json.dumps(
                        {
                            **cov,
                            "eligibility_date": r["eligibility_date"][:10],
                            "sustained": r["sustained"],
                            "sustained_simple": r["sustained_simple"],
                            "abandoned": r["abandoned"],
                            "opportunity_head": opportunity_head(
                                opportunity, measured, r["project_id"], r["feature_id"]
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    "pipeline_version": PIPELINE_VERSION,
                }
            )
        # A Fase 13 é uma reconstrução total, não um acréscimo: `upsert` casa por
        # `row_id` e nunca remove, então linhas de execuções anteriores cujos projetos
        # saíram do dataset (purgados, reprocessados, excluídos) sobreviveriam e o banco
        # divergiria do parquet publicado. Em 2026-09-06 eram 28 linhas órfãs (D-021).
        store.conn.execute("DELETE FROM survival_rows")
        store.upsert("survival_rows", rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    import pandas as pd

    flat = []
    for row in rows:
        cov = json.loads(row["covariates_json"])
        flat.append({k: v for k, v in row.items() if k != "covariates_json"} | cov)
    frame = pd.DataFrame(flat)
    frame.to_parquet(OUT_DIR / "survival.parquet", index=False)
    frame.to_csv(OUT_DIR / "survival.csv", index=False)

    print(
        f"dataset: {len(rows)} linhas de análise "
        f"({frame['event'].sum()} eventos, {(frame['event'] == 0).sum()} censuras)"
    )
    print(f"excluídas do denominador (reportadas): {excluded}")
    if stale:
        total_stale = sum(stale.values())
        pendentes = sorted(stale.items(), key=lambda kv: -kv[1])
        print(
            f"OMITIDAS por detecção desatualizada: {total_stale} linhas "
            + ", ".join(f"{f} ({n})" for f, n in pendentes)
        )
        print(
            "  -> essas features exigem projeto minerado em "
            f"{ {f: MIN_VERSION_BY_FEATURE[f] for f, _ in pendentes} }; "
            "reminere-os e reexecute `jmod dataset` para recuperá-las"
        )
    print(f"projetos distintos: {frame['project_id'].nunique()}")
    print(f"salvo em {OUT_DIR}/survival.parquet e .csv")
    return 0
