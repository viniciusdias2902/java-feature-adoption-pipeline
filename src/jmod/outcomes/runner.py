"""Fase 11: aplica as definições de desfecho sobre tudo o que a mineração persistiu."""

from __future__ import annotations

from jmod.config import Study
from jmod.db import Store
from jmod.outcomes.adoption_events import Period, Point, derive_outcome
from jmod.provenance import new_run_id

PHASE = "outcomes"


def run_outcomes(store: Store, study: Study) -> int:
    run = new_run_id()
    freeze = f"{study.freeze_date}T00:00:00+00:00"
    out_cfg = study.outcomes
    primary = out_cfg["primary_scope"]

    projects = [
        r["project_id"]
        for r in store.query(
            "SELECT DISTINCT project_id FROM run_log WHERE phase='mine' AND status='ok'"
        )
    ]
    print(f"desfechos para {len(projects)} projetos × {len(study.features)} features")

    eligibility: dict[tuple[str, str], list[Period]] = {}
    for r in store.query(
        "SELECT project_id, feature_id, start_date, end_date FROM eligibility "
        "WHERE kind='stable' ORDER BY start_date"
    ):
        eligibility.setdefault((r["project_id"], r["feature_id"]), []).append(
            Period(r["start_date"], r["end_date"])
        )

    series: dict[tuple[str, str], list[Point]] = {}
    for r in store.query(
        "SELECT project_id, feature_id, sha, committed_at, occurrence_count, file_count "
        "FROM feature_series WHERE scope=? ORDER BY committed_at, sha",
        (primary,),
    ):
        series.setdefault((r["project_id"], r["feature_id"]), []).append(
            Point(r["sha"], r["committed_at"], r["occurrence_count"], r["file_count"])
        )

    rows = []
    with store.phase_run(run, PHASE):
        for pid in projects:
            for feature in study.features:
                key = (pid, feature.id)
                outcome = derive_outcome(
                    eligibility.get(key, []),
                    series.get(key, []),
                    freeze=freeze,
                    sustained_window_days=int(out_cfg["sustained_window_days"]),
                    sustained_min_occurrences=int(out_cfg["sustained_min_occurrences"]),
                    sustained_min_files=int(out_cfg["sustained_min_files"]),
                    abandonment_window_days=int(out_cfg["abandonment_window_days"]),
                )
                rows.append(
                    {
                        "project_id": pid,
                        "module_path": "",  # desfecho no grão do projeto
                        "feature_id": feature.id,
                        "scope": primary,
                        "eligibility_date": outcome.eligibility_date or "",
                        "first_use_date": outcome.first_use_date,
                        "first_use_sha": outcome.first_use_sha,
                        "time_to_first_use_days": outcome.time_to_first_use_days,
                        "event": outcome.event,
                        "sustained": outcome.sustained,
                        "sustained_simple": outcome.sustained_simple,
                        "sustained_date": outcome.sustained_date,
                        "abandoned": outcome.abandoned,
                        "abandoned_date": outcome.abandoned_date,
                        "censor_reason": outcome.censor_reason,
                    }
                )
        store.upsert("adoption_outcomes", rows)

    resumo = store.query(
        "SELECT feature_id, COUNT(*) FILTER (WHERE eligibility_date != '') eleg, "
        "SUM(event) ev, SUM(sustained) sus, SUM(abandoned) ab, "
        "COUNT(*) FILTER (WHERE censor_reason='pre_eligibility_use') pre "
        "FROM adoption_outcomes GROUP BY feature_id ORDER BY feature_id"
    )
    print(
        f"{'feature':<20} {'elegíveis':>9} {'adotaram':>8} {'sustained':>9} "
        f"{'abandono':>8} {'pré-eleg':>8}"
    )
    for r in resumo:
        print(
            f"{r['feature_id']:<20} {r['eleg']:>9} {r['ev'] or 0:>8} "
            f"{r['sus'] or 0:>9} {r['ab'] or 0:>8} {r['pre']:>8}"
        )
    return 0
