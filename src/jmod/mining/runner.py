"""Fase 10: orquestração da mineração sobre a população.

Por projeto, numa passada única sobre um único clone:
clone bare blobless -> backfill (build + *.java) -> linhas do tempo (F4-7) ->
elegibilidade (F8) -> mineração das 8 features (F10) -> varredura do HEAD (recall) ->
persistência -> descarte do clone.

Retomável: projetos com run_log 'mine' ok são pulados. Cada worker usa sua própria
conexão SQLite (WAL); falha de um projeto vira exclusão motivada, nunca parada da fila.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from jmod.config import Study
from jmod.db import Store
from jmod.events.eligibility import module_eligibility
from jmod.events.timeline import module_timelines
from jmod.features.parser import ScopeRules
from jmod.mining.adoption import mine_feature, scan_head
from jmod.provenance import PIPELINE_VERSION, new_run_id, now
from jmod.repo.clone import DiskBudget, DiskBudgetExceeded, Workspace

PHASE = "mine"
WORKSPACE = Path("data/work")

MINE_BACKFILL = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "gradle.properties",
    ".sdkmanrc",
    ".java-version",
    ".tool-versions",
    "buildSrc/",
    "*.java",
)


@dataclass(frozen=True)
class ProjectStats:
    project_id: str
    modules: int
    events: int
    eligibility_periods: int
    series_points: int
    first_uses: int
    recall_ok: bool
    seconds: float


def mine_project(
    project_id: str,
    clone_url: str,
    default_branch: str | None,
    study: Study,
    db_path: Path,
    workspace: Workspace,
) -> ProjectStats:
    started = time.monotonic()
    end_obs = f"{study.freeze_date}T00:00:00+00:00"
    persistence = int(study.eligibility["upgrade_min_days"])
    rules = ScopeRules.from_config(study.paths)

    repo = workspace.clone(project_id, clone_url)
    try:
        repo.backfill(list(MINE_BACKFILL))
        ref = default_branch or repo.default_branch()

        timelines = module_timelines(
            repo, ref, persistence_days=persistence, end_of_observation=end_obs
        )

        with Store(db_path) as store:
            store.conn.execute("PRAGMA busy_timeout = 60000")
            n_events = n_periods = 0
            for module, tl in timelines.items():
                store.upsert(
                    "baseline_intervals",
                    [
                        {
                            "project_id": project_id,
                            "module_path": module,
                            "start_sha": i.start_sha,
                            "start_date": i.start_date,
                            "end_sha": i.end_sha,
                            "end_date": i.end_date,
                            "java_version": i.java_version,
                        }
                        for i in tl.intervals
                    ],
                )
                store.upsert(
                    "version_events",
                    [
                        {
                            "project_id": project_id,
                            "module_path": module,
                            "sha": e.sha,
                            "committed_at": e.committed_at,
                            "from_version": e.from_version,
                            "to_version": e.to_version,
                            "kind": e.kind,
                            "persisted_days": e.persisted_days,
                        }
                        for e in tl.events
                    ],
                )
                n_events += len(tl.events)
                periods = module_eligibility(
                    tl.intervals,
                    study.features,
                    persistence_days=persistence,
                    end_of_observation=end_obs,
                )
                store.upsert(
                    "eligibility",
                    [
                        {
                            "project_id": project_id,
                            "module_path": module,
                            "feature_id": p.feature_id,
                            "kind": p.kind,
                            "start_date": p.start_date,
                            "start_sha": p.start_sha,
                            "end_date": p.end_date,
                        }
                        for p in periods
                    ],
                )
                n_periods += len(periods)

            n_series = n_first = 0
            final_counts: dict[tuple[str, str], int] = {}
            for feature in study.features:
                mined = mine_feature(repo, ref, feature, rules)
                store.upsert(
                    "feature_candidates",
                    [
                        {
                            "project_id": project_id,
                            "feature_id": feature.id,
                            "sha": c.sha,
                            "path": c.path,
                            "strategy": mined.strategy,
                            "committed_at": c.committed_at,
                        }
                        for c in mined.candidates
                    ],
                )
                store.upsert(
                    "feature_series",
                    [
                        {
                            "project_id": project_id,
                            "feature_id": feature.id,
                            "scope": p.scope,
                            "sha": p.sha,
                            "committed_at": p.committed_at,
                            "occurrence_count": p.occurrence_count,
                            "file_count": p.file_count,
                        }
                        for p in mined.series
                    ],
                )
                store.upsert(
                    "feature_occurrences",
                    [
                        {
                            "project_id": project_id,
                            "feature_id": feature.id,
                            "sha": fu.sha,
                            "path": fu.path,
                            "module_path": None,
                            "node_subtype": hit.node_subtype,
                            "line": hit.line,
                            "byte_start": hit.byte_start,
                            "scope": fu.scope,
                            "committed_at": fu.committed_at,
                            "pipeline_version": PIPELINE_VERSION,
                        }
                        for fu in mined.first_uses
                        for hit in fu.hits
                    ],
                )
                n_series += len(mined.series)
                n_first += len(mined.first_uses)
                latest: dict[str, int] = {}
                for p in mined.series:
                    latest[p.scope] = p.occurrence_count
                for scope, count in latest.items():
                    final_counts[(feature.id, scope)] = count

            head_counts = scan_head(repo, ref, study.features, rules)
            recall_ok = True
            rows = []
            for key in set(final_counts) | set(head_counts):
                series_count = final_counts.get(key, 0)
                head_count = head_counts.get(key, 0)
                ok = series_count == head_count
                recall_ok = recall_ok and ok
                rows.append(
                    {
                        "project_id": project_id,
                        "feature_id": key[0],
                        "scope": key[1],
                        "series_count": series_count,
                        "headscan_count": head_count,
                        "ok": int(ok),
                        "checked_at": now(),
                    }
                )
            store.upsert("recall_checks", rows)

        return ProjectStats(
            project_id,
            len(timelines),
            n_events,
            n_periods,
            n_series,
            n_first,
            recall_ok,
            time.monotonic() - started,
        )
    finally:
        if not study.storage.get("retain_clones", False):
            workspace.release(project_id)


def run_mine(store: Store, study: Study, *, limit: int, only: str | None = None) -> int:
    run = new_run_id()
    budget = DiskBudget.from_gb(WORKSPACE, float(study.storage["disk_budget_gb"]))
    workspace = Workspace(WORKSPACE, budget=budget)
    workers = int(study.storage["workers"])
    db_path = store.path

    rows = store.query(
        "SELECT p.project_id, p.clone_url, p.default_branch, "
        "       (ps.project_id IS NOT NULL) AS is_pilot, COALESCE(p.seart_loc_java, 0) loc "
        "FROM projects p "
        "JOIN probe pr USING (project_id) "
        "LEFT JOIN pilot_sample ps USING (project_id) "
        "LEFT JOIN exclusions e ON e.project_id = p.project_id "
        "LEFT JOIN run_log r ON r.phase = 'mine' AND r.project_id = p.project_id "
        "                     AND r.status = 'ok' "
        "WHERE e.project_id IS NULL AND r.project_id IS NULL "
        + ("AND p.project_id = ? " if only else "")
        + "ORDER BY is_pilot DESC, loc ASC"
        + (f" LIMIT {int(limit)}" if limit else ""),
        (only,) if only else (),
    )
    total = len(rows)
    if not total:
        print("nada a minerar: tudo processado ou excluído")
        return 0
    print(
        f"mineração de {total} projetos com {workers} workers "
        f"(piloto primeiro, depois por tamanho)",
        flush=True,
    )

    done = failed = recall_failures = 0
    started = time.monotonic()

    def work(row: sqlite3.Row) -> ProjectStats:
        return mine_project(
            row["project_id"],
            row["clone_url"],
            row["default_branch"],
            study,
            db_path,
            workspace,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, row): row["project_id"] for row in rows}
        for i, future in enumerate(as_completed(futures), 1):
            pid = futures[future]
            with Store(db_path) as tstore:
                tstore.conn.execute("PRAGMA busy_timeout = 60000")
                base = {
                    "run_id": run,
                    "phase": PHASE,
                    "project_id": pid,
                    "started_at": now(),
                    "pipeline_version": PIPELINE_VERSION,
                }
                try:
                    stats = future.result()
                except DiskBudgetExceeded as exc:
                    print(f"ORÇAMENTO DE DISCO: {exc} — abortando a fila", file=sys.stderr)
                    for other in futures:
                        other.cancel()
                    tstore.upsert(
                        "run_log",
                        [{**base, "status": "failed", "ended_at": now(), "error": str(exc)}],
                    )
                    return 3
                except Exception as exc:  # noqa: BLE001 — um projeto não derruba a fila
                    failed += 1
                    tstore.exclude(pid, PHASE, "mine_failed", f"{type(exc).__name__}: {exc}"[:300])
                    tstore.upsert(
                        "run_log",
                        [
                            {
                                **base,
                                "status": "failed",
                                "ended_at": now(),
                                "error": f"{type(exc).__name__}: {exc}"[:500],
                            }
                        ],
                    )
                    workspace.release(pid)
                else:
                    done += 1
                    if not stats.recall_ok:
                        recall_failures += 1
                    tstore.upsert(
                        "run_log", [{**base, "status": "ok", "ended_at": now(), "error": None}]
                    )
                    flag = "" if stats.recall_ok else "  RECALL-FALHOU"
                    print(
                        f"  [{i}/{total}] {pid:<44} {stats.seconds:6.0f}s "
                        f"mod={stats.modules:<4} ev={stats.events:<3} "
                        f"elig={stats.eligibility_periods:<4} serie={stats.series_points:<5} "
                        f"fu={stats.first_uses}{flag}",
                        flush=True,
                    )
            if i % 20 == 0 or i == total:
                rate = i / max(1e-6, time.monotonic() - started)
                eta_h = (total - i) / rate / 3600
                print(
                    f"  --- [{i}/{total}] ok={done} falhas={failed} "
                    f"recall_falhas={recall_failures} · {rate * 3600:.0f}/h · "
                    f"ETA {eta_h:.1f}h · workspace {workspace.size_gb():.1f} GB",
                    flush=True,
                )

    print(f"mineração: {done} ok, {failed} falhas, {recall_failures} falhas de recall")
    return 0
