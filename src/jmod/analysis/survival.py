"""Fase 15: análise de sobrevivência sobre o dataset longitudinal.

Origem do relógio: a DATA DE ELEGIBILIDADE de cada (projeto, feature) — o diferencial
metodológico do estudo. Kaplan–Meier por feature; Cox PH com variância robusta agrupada
por projeto; truncamento à esquerda registrado; BH sobre os contrastes principais;
`var` sempre reportado à parte; sensibilidade sustained-(i) e só-Maven.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DATASET = Path("data/dataset/survival.parquet")
OUT = Path("data/analysis")

FEATURE_ORDER = [
    "var",
    "switch_expression",
    "text_block",
    "record",
    "pattern_instanceof",
    "sealed",
    "pattern_switch",
    "record_pattern",
]


def _load() -> pd.DataFrame:
    frame = pd.read_parquet(DATASET)
    frame["log_stars"] = np.log1p(frame["stars"].fillna(0))
    frame["log_loc"] = np.log1p(frame["loc_java"].fillna(0))
    frame["log_commits"] = np.log1p(frame["commits"].fillna(0))
    frame["log_modules"] = np.log1p(frame["module_count"].fillna(1))
    frame["age_years"] = frame["age_years"].fillna(frame["age_years"].median())
    frame["gradle"] = frame["build_system"].str.startswith("gradle").astype(int)
    frame["external_parent"] = frame["has_external_parent"].fillna(0).astype(int)
    return frame


def km_by_feature(frame: pd.DataFrame) -> tuple[pd.DataFrame, Any]:
    import matplotlib
    from lifelines import KaplanMeierFitter

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 6))
    rows = []
    for feature in FEATURE_ORDER:
        sub = frame[frame.feature_id == feature]
        if len(sub) < 10:
            continue
        km = KaplanMeierFitter(label=feature)
        km.fit(sub.duration_days, sub.event, entry=sub.entry_days)
        style = {"linestyle": "--"} if feature == "var" else {}
        km.plot_survival_function(ax=ax, ci_show=False, **style)
        median = km.median_survival_time_
        rows.append(
            {
                "feature": feature,
                "n_eligible": len(sub),
                "n_adopted": int(sub.event.sum()),
                "adoption_pct": round(100 * sub.event.mean(), 1),
                "median_days_km": None if np.isinf(median) else round(float(median)),
                "adopted_1y_pct": _cum_incidence(km, 365),
                "adopted_2y_pct": _cum_incidence(km, 730),
            }
        )
    ax.set_xlabel("dias de exposição desde a elegibilidade")
    ax.set_ylabel("fração ainda sem adotar")
    ax.set_title("Tempo até o primeiro uso, por feature (relógio = elegibilidade)")
    ax.set_xlim(0, 2600)
    fig.tight_layout()
    fig.savefig(OUT / "km_features.png", dpi=140)
    return pd.DataFrame(rows), fig


def _cum_incidence(km: Any, days: int) -> float:
    try:
        return round(100 * (1 - float(km.survival_function_at_times(days).iloc[0])), 1)
    except Exception:  # noqa: BLE001
        return float("nan")


def cox_model(frame: pd.DataFrame, *, maven_only: bool = False) -> pd.DataFrame:
    from lifelines import CoxPHFitter

    sub = frame[frame.feature_id != "var"].copy()  # var à parte
    if maven_only:
        sub = sub[sub.build_system.isin(["maven", "mixed"])]
    covs = [
        "log_stars",
        "log_loc",
        "log_commits",
        "age_years",
        "log_modules",
        "gradle",
        "external_parent",
    ]
    if maven_only:
        covs.remove("gradle")
    data = sub[["duration_days", "event", "entry_days", "project_id", *covs]].dropna()
    cph = CoxPHFitter(penalizer=0.01)
    cph.fit(
        data,
        duration_col="duration_days",
        event_col="event",
        entry_col="entry_days",
        cluster_col="project_id",
        robust=True,
    )
    summary = cph.summary[["exp(coef)", "exp(coef) lower 95%", "exp(coef) upper 95%", "p"]]
    summary = summary.rename(
        columns={
            "exp(coef)": "HR",
            "exp(coef) lower 95%": "IC95_inf",
            "exp(coef) upper 95%": "IC95_sup",
            "p": "p_valor",
        }
    )
    summary["p_BH"] = _benjamini_hochberg(summary["p_valor"].to_numpy())
    return summary.round(4)


def _benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    order = np.argsort(p)
    ranked = np.empty_like(p)
    adjusted = p[order] * len(p) / (np.arange(len(p)) + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    ranked[order] = np.minimum(adjusted, 1.0)
    return ranked


def persistence_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for feature in FEATURE_ORDER:
        sub = frame[(frame.feature_id == feature) & (frame.event == 1)]
        classified = sub[sub.sustained.notna()]
        rows.append(
            {
                "feature": feature,
                "adopters": len(sub),
                "sustained_pct": round(100 * classified.sustained.mean(), 1)
                if len(classified)
                else float("nan"),
                "sustained_simple_pct": round(100 * sub.sustained_simple.mean(), 1)
                if len(sub)
                else float("nan"),
                "abandoned_pct": round(100 * sub.abandoned.fillna(0).mean(), 1)
                if len(sub)
                else float("nan"),
                "window_open": int((sub.sustained.isna()).sum()),
            }
        )
    return pd.DataFrame(rows)


def run_analysis() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = _load()

    km_table, _ = km_by_feature(frame)
    persistence = persistence_table(frame)
    cox = cox_model(frame)
    cox_maven = cox_model(frame, maven_only=True)

    km_table.to_csv(OUT / "adoption_by_feature.csv", index=False)
    persistence.to_csv(OUT / "persistence_by_feature.csv", index=False)
    cox.to_csv(OUT / "cox_full.csv")
    cox_maven.to_csv(OUT / "cox_maven_only.csv")

    summary = {
        "rows": int(len(frame)),
        "projects": int(frame.project_id.nunique()),
        "events": int(frame.event.sum()),
        "adoption": km_table.to_dict(orient="records"),
        "persistence": persistence.to_dict(orient="records"),
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("=== adoção por feature (relógio = elegibilidade) ===")
    print(km_table.to_string(index=False))
    print("\n=== persistência ===")
    print(persistence.to_string(index=False))
    print("\n=== Cox (HR>1 = adoção mais rápida) — todas exceto var ===")
    print(cox.to_string())
    print(f"\nfiguras e tabelas em {OUT}/")
    return 0
