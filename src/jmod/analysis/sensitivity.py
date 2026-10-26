"""Fase 15b: sensibilidade da adoção sustentada aos limiares congelados.

A definição de sustentada tem três parâmetros escolhidos por julgamento --- 180 dias, 3
ocorrências, 2 arquivos ---, declarados arbitrários por natureza.
Este módulo mede quanto o resultado depende deles.

O primeiro uso NÃO depende: `derive_outcome` decide adoção pela primeira ocorrência
confirmada, antes de olhar os limiares. Então variar os limiares reclassifica apenas quem
já adotou, os denominadores continuam os do estudo e só a fração sustentada se move ---
o que torna a comparação entre variantes direta.

A população é exatamente a do dataset longitudinal (`data/dataset/survival.parquet`), para
que as frações sejam comparáveis às da Tabela de persistência, e não uma recontagem sobre
um conjunto ligeiramente diferente de pares.

Nada é escrito no banco: as variantes são recalculadas em memória. Reexecutar não
contamina `adoption_outcomes` com desfechos sob limiares que não são os do estudo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from jmod.config import Study
from jmod.db import Store
from jmod.outcomes.adoption_events import Period, Point, derive_outcome

DATASET = Path("data/dataset/survival.parquet")
OUT = Path("data/analysis")

#: Variantes reportadas. A primeira é a do estudo; as demais afrouxam e apertam cada
#: parâmetro em separado, para que o efeito de cada um seja atribuível.
VARIANTES: list[tuple[str, dict[str, int]]] = [
    ("estudo (3 occ / 2 arq / 180 d)", {}),
    ("frouxa (2 occ / 1 arq / 180 d)", {"sustained_min_occurrences": 2, "sustained_min_files": 1}),
    ("estrita (5 occ / 3 arq / 180 d)", {"sustained_min_occurrences": 5, "sustained_min_files": 3}),
    ("janela curta (3 occ / 2 arq / 90 d)", {"sustained_window_days": 90}),
    ("janela longa (3 occ / 2 arq / 365 d)", {"sustained_window_days": 365}),
]


def _series_e_elegibilidade(
    store: Store, scope: str
) -> tuple[dict[tuple[str, str], list[Period]], dict[tuple[str, str], list[Point]]]:
    elegibilidade: dict[tuple[str, str], list[Period]] = {}
    for r in store.query(
        "SELECT project_id, feature_id, start_date, end_date FROM eligibility "
        "WHERE kind='stable' ORDER BY start_date"
    ):
        elegibilidade.setdefault((r["project_id"], r["feature_id"]), []).append(
            Period(r["start_date"], r["end_date"])
        )

    series: dict[tuple[str, str], list[Point]] = {}
    for r in store.query(
        "SELECT project_id, feature_id, sha, committed_at, occurrence_count, file_count "
        "FROM feature_series WHERE scope=? ORDER BY committed_at, sha",
        (scope,),
    ):
        series.setdefault((r["project_id"], r["feature_id"]), []).append(
            Point(r["sha"], r["committed_at"], r["occurrence_count"], r["file_count"])
        )
    return elegibilidade, series


def run_sensitivity(store: Store, study: Study) -> int:
    if not DATASET.exists():
        print(f"dataset ausente: {DATASET} — rode `jmod dataset` antes")
        return 2

    frame = pd.read_parquet(DATASET)
    pares = [
        (str(r.project_id), str(r.feature_id))
        for r in frame.itertuples()
        if int(r.event) == 1  # limiares só reclassificam quem adotou
    ]

    cfg = study.outcomes
    freeze = f"{study.freeze_date}T00:00:00+00:00"
    elegibilidade, series = _series_e_elegibilidade(store, str(cfg["primary_scope"]))

    base = {
        "sustained_window_days": int(cfg["sustained_window_days"]),
        "sustained_min_occurrences": int(cfg["sustained_min_occurrences"]),
        "sustained_min_files": int(cfg["sustained_min_files"]),
        "abandonment_window_days": int(cfg["abandonment_window_days"]),
    }

    linhas: list[dict[str, Any]] = []
    for nome, mudanca in VARIANTES:
        kw = {**base, **mudanca}
        por_feature: dict[str, list[int | None]] = {}
        for pid, fid in pares:
            out = derive_outcome(
                elegibilidade.get((pid, fid), []),
                series.get((pid, fid), []),
                freeze=freeze,
                **kw,
            )
            por_feature.setdefault(fid, []).append(out.sustained)

        for fid, vereditos in sorted(por_feature.items()):
            classificados = [v for v in vereditos if v is not None]
            linhas.append(
                {
                    "variante": nome,
                    "feature": fid,
                    "adotantes": len(vereditos),
                    # Janela ainda aberta no freeze: sem veredito, fora do denominador.
                    "janela_aberta": len(vereditos) - len(classificados),
                    "sustentada_pct": round(100 * sum(classificados) / len(classificados), 1)
                    if classificados
                    else float("nan"),
                }
            )

    tabela = pd.DataFrame(linhas)
    OUT.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(OUT / "sensitivity_sustained.csv", index=False)

    geral = (
        tabela.assign(
            sustentados=lambda t: t.sustentada_pct / 100 * (t.adotantes - t.janela_aberta),
            classificados=lambda t: t.adotantes - t.janela_aberta,
        )
        .groupby("variante", sort=False)[["sustentados", "classificados", "janela_aberta"]]
        .sum()
    )
    geral["sustentada_pct"] = (100 * geral.sustentados / geral.classificados).round(1)

    # O nível da fração se move muito com o limiar; a pergunta que importa é se a ORDEM
    # entre os recursos se move também. Spearman via pandas, para não puxar scipy.
    por_variante = tabela.pivot(index="feature", columns="variante", values="sustentada_pct")
    base_nome = VARIANTES[0][0]
    geral["rho_vs_estudo"] = [
        round(float(por_variante[base_nome].corr(por_variante[nome], method="spearman")), 3)
        for nome in geral.index
    ]
    geral.drop(columns=["sustentados"]).to_csv(OUT / "sensitivity_summary.csv")

    print(
        f"{'variante':<38} {'classificados':>13} {'jan. aberta':>11} "
        f"{'sustentada %':>12} {'rho':>6}"
    )
    for nome, r in geral.iterrows():
        print(
            f"{str(nome):<38} {int(r.classificados):>13} "
            f"{int(r.janela_aberta):>11} {r.sustentada_pct:>12} {r.rho_vs_estudo:>6}"
        )
    print(f"\npor feature em {OUT / 'sensitivity_sustained.csv'}")
    return 0
