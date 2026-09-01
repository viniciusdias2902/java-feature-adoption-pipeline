"""Orquestração das fases 2 e P. Toda etapa é retomável e registra exclusões."""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from jmod.config import Study
from jmod.db import Store
from jmod.ingest import seart
from jmod.ingest.github import GitHubClient
from jmod.ingest.probe import probe_repo
from jmod.ingest.sampling import stratified_draw, stratum_matrix
from jmod.ingest.seart_api import SeartClient, query_params, to_row
from jmod.ingest.tls import build_bundle
from jmod.provenance import PIPELINE_VERSION, new_run_id, now

PHASE_INGEST, PHASE_VALIDATE, PHASE_PROBE, PHASE_PILOT = "ingest", "validate", "probe", "pilot"


def _pid(full_name: str) -> str:
    return full_name.strip().lower()


def run_ingest(
    store: Store,
    study: Study,
    *,
    csv: Path | None,
    from_seart_api: bool,
    from_github_search: bool,
    limit: int,
) -> int:
    run = new_run_id()
    if csv is None and not from_github_search and not from_seart_api:
        print(
            "informe --from-seart-api (recomendado), --csv <export> ou --from-github-search",
            file=sys.stderr,
        )
        return 2

    with store.phase_run(run, PHASE_INGEST):
        if from_seart_api:
            kept, dropped = _ingest_seart_api(store, study, limit)
            source = "seart"
        elif csv is not None:
            if not csv.exists():
                print(f"CSV não encontrado: {csv}", file=sys.stderr)
                return 2
            kept, dropped = _ingest_seart(store, study, csv, limit)
            source = "seart"
        else:
            kept, dropped = _ingest_github_search(store, study, limit)
            source = "github_search"

    print(f"quadro amostral ({source}): {kept} aceitos, {dropped} excluídos")
    return 0


def _ingest_seart(store: Store, study: Study, csv: Path, limit: int) -> tuple[int, int]:
    kept = dropped = 0
    batch = []
    for row in seart.read_csv(csv):
        if limit and kept >= limit:
            break
        pid = _pid(row.full_name)
        reason = seart.screen(row, study)
        if reason:
            store.exclude(pid, PHASE_INGEST, reason)
            dropped += 1
            continue
        batch.append(
            {
                "project_id": pid,
                "full_name": row.full_name,
                "clone_url": f"https://github.com/{row.full_name}.git",
                "default_branch": row.default_branch,
                "frame_source": "seart",
                "seart_stars": row.stars,
                "seart_commits": row.commits,
                "seart_loc_java": row.loc_java,
                "seart_created_at": row.created_at,
                "seart_last_commit_at": row.last_commit_at,
                "gh_archived": None,
                "gh_disabled": None,
                "gh_fork": None,
                "gh_pushed_at": None,
                "gh_size_kb": None,
                "gh_validated_at": None,
                "pipeline_version": PIPELINE_VERSION,
                "collected_at": now(),
            }
        )
        kept += 1
        if len(batch) >= 500:
            store.upsert("projects", batch)
            batch = []
    store.upsert("projects", batch)
    return kept, dropped


def _ingest_seart_api(store: Store, study: Study, limit: int) -> tuple[int, int]:
    """Ingestão pela API REST do SEART. Ver seart_api.py para a armadilha dos filtros."""
    bundle = build_bundle(Path("data/cache"))
    client = SeartClient(verify=bundle)
    params = query_params(study)
    try:
        total = client.total(params)
        ignored = client.verify_filters(params)
        if ignored:
            print(
                f"aviso: filtros sem efeito no servidor: {ignored} — reaplicados no cliente",
                file=sys.stderr,
            )
        print(f"SEART: {total} projetos casam os filtros de seleção")

        kept = dropped = served = 0
        batch: list[dict[str, object]] = []
        for item in client.iter_items(params, limit):
            served += 1
            row = to_row(item)
            if not row.full_name:
                continue
            pid = _pid(row.full_name)
            reason = seart.screen(row, study)
            if reason:
                store.exclude(pid, PHASE_INGEST, reason, "reaplicado no cliente")
                dropped += 1
                continue
            batch.append(
                {
                    "project_id": pid,
                    "full_name": row.full_name,
                    "clone_url": f"https://github.com/{row.full_name}.git",
                    "default_branch": row.default_branch,
                    "frame_source": "seart",
                    "seart_stars": row.stars,
                    "seart_commits": row.commits,
                    "seart_loc_java": row.loc_java,
                    "seart_created_at": row.created_at,
                    "seart_last_commit_at": row.last_commit_at,
                    "gh_archived": None,
                    "gh_disabled": None,
                    "gh_fork": None,
                    "gh_pushed_at": None,
                    "gh_size_kb": None,
                    "gh_validated_at": None,
                    "pipeline_version": PIPELINE_VERSION,
                    "collected_at": now(),
                }
            )
            kept += 1
            if len(batch) >= 500:
                store.upsert("projects", batch)
                batch = []
        store.upsert("projects", batch)

        # O SEART pode informar um totalItems maior do que efetivamente serve nas páginas.
        # A diferença é do servidor, não perda nossa — mas precisa ficar registrada, e não
        # pode ser confundida com descarte silencioso da pipeline.
        if not limit and served != total:
            print(
                f"aviso: SEART informou totalItems={total} mas serviu {served} itens "
                f"({total - served} nunca retornados em nenhuma página)",
                file=sys.stderr,
            )
            store.exclude(
                "__frame__",
                PHASE_INGEST,
                "seart_total_mismatch",
                f"reported={total} served={served}",
            )
        return kept, dropped
    finally:
        client.close()


def _ingest_github_search(store: Store, study: Study, limit: int) -> tuple[int, int]:
    """Quadro de CONTINGÊNCIA, usado só quando o export do SEART não está disponível.

    Não substitui o SEART: a Search API corta em 1.000 resultados e ordena por estrelas,
    o que introduz viés de popularidade que o quadro do SEART não tem. Todo projeto
    entra marcado com `frame_source='github_search'` para que a análise possa segregá-lo.
    """
    s = study.sampling
    query = (
        f"language:java stars:>={s['min_stars']} "
        f"created:<{s['created_before']} pushed:>={s['active_since']} "
        f"fork:false archived:false"
    )
    client = GitHubClient()
    if not client.authenticated:
        print("aviso: sem GITHUB_TOKEN — 60 req/h. Suficiente só para o piloto.", file=sys.stderr)

    kept = dropped = 0
    batch = []
    try:
        for item in client.search_repositories(query, limit or 100):
            pid = _pid(item["full_name"])
            if (item.get("language") or "").lower() != "java":
                store.exclude(pid, PHASE_INGEST, "not_java")
                dropped += 1
                continue
            batch.append(
                {
                    "project_id": pid,
                    "full_name": item["full_name"],
                    "clone_url": item["clone_url"],
                    "default_branch": item.get("default_branch"),
                    "frame_source": "github_search",
                    "seart_stars": item.get("stargazers_count"),
                    "seart_commits": None,
                    "seart_loc_java": None,
                    "seart_created_at": item.get("created_at"),
                    "seart_last_commit_at": item.get("pushed_at"),
                    "gh_archived": int(bool(item.get("archived"))),
                    "gh_disabled": int(bool(item.get("disabled"))),
                    "gh_fork": int(bool(item.get("fork"))),
                    "gh_pushed_at": None,
                    "gh_size_kb": item.get("size"),
                    "gh_validated_at": now(),
                    "pipeline_version": PIPELINE_VERSION,
                    "collected_at": now(),
                }
            )
            kept += 1
    finally:
        client.close()
    store.upsert("projects", batch)
    return kept, dropped


def run_validate(store: Store, study: Study, *, limit: int) -> int:
    run = new_run_id()
    client = GitHubClient()
    if not client.authenticated:
        print("aviso: sem GITHUB_TOKEN — 60 req/h.", file=sys.stderr)

    rows = store.query(
        "SELECT project_id, full_name FROM projects WHERE gh_validated_at IS NULL"
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    ok = gone = 0
    try:
        with store.phase_run(run, PHASE_VALIDATE):
            for row in rows:
                info = client.repo(row["full_name"])
                if info is None:
                    store.exclude(row["project_id"], PHASE_VALIDATE, "repo_gone")
                    gone += 1
                    continue
                reason = None
                if info.archived:
                    reason = "archived"
                elif info.disabled:
                    reason = "disabled"
                elif info.fork and study.sampling["exclude_forks"]:
                    reason = "fork"
                if reason:
                    store.exclude(row["project_id"], PHASE_VALIDATE, reason)
                    gone += 1
                    continue
                store.upsert(
                    "projects",
                    [
                        {
                            "project_id": row["project_id"],
                            "full_name": info.full_name,
                            "clone_url": info.clone_url,
                            "default_branch": info.default_branch,
                            "gh_archived": int(info.archived),
                            "gh_disabled": int(info.disabled),
                            "gh_fork": int(info.fork),
                            "gh_size_kb": info.size_kb,
                            "gh_validated_at": now(),
                        }
                    ],
                )
                ok += 1
    finally:
        client.close()
    print(f"validação: {ok} confirmados, {gone} excluídos")
    return 0


def run_probe(store: Store, study: Study, *, limit: int) -> int:
    """Probe paralelo. As clonagens são dominadas por rede, então threads bastam;
    a escrita no SQLite fica na thread principal, que é onde a conexão vive."""
    run = new_run_id()
    rows = store.query(
        "SELECT p.project_id, p.clone_url, p.default_branch FROM projects p "
        "LEFT JOIN probe pr USING (project_id) "
        "LEFT JOIN exclusions e ON e.project_id = p.project_id "
        "WHERE pr.project_id IS NULL AND e.project_id IS NULL"
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    total = len(rows)
    if not total:
        print("nada a fazer: todos os projetos já têm probe ou exclusão")
        return 0

    workers = int(study.storage["workers"])
    print(f"probe de {total} projetos com {workers} workers")

    done = failed = android = 0
    started = time.monotonic()

    with store.phase_run(run, PHASE_PROBE), ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(probe_repo, r["clone_url"], r["default_branch"]): r["project_id"]
            for r in rows
        }
        for i, future in enumerate(as_completed(futures), 1):
            pid = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 — falha de um repo não derruba a fila
                store.exclude(pid, PHASE_PROBE, "probe_crashed", f"{type(exc).__name__}: {exc}")
                failed += 1
                continue

            if result.error:
                store.exclude(pid, PHASE_PROBE, result.error)
                failed += 1
            elif result.is_android and study.sampling["exclude_android"]:
                store.exclude(pid, PHASE_PROBE, "android")
                android += 1
            elif result.build_system == "none":
                store.exclude(pid, PHASE_PROBE, "no_build_system")
                failed += 1
            else:
                store.upsert(
                    "probe",
                    [
                        {
                            "project_id": pid,
                            "head_sha": result.head_sha,
                            "build_system": result.build_system,
                            "module_count": result.module_count,
                            "has_external_parent": int(result.has_external_parent),
                            "head_baseline": result.head_baseline,
                            "head_baseline_source": result.head_baseline_source,
                            "is_android": int(result.is_android),
                            "pipeline_version": PIPELINE_VERSION,
                            "collected_at": now(),
                        }
                    ],
                )
                done += 1

            if i % 25 == 0 or i == total:
                rate = i / max(1e-6, time.monotonic() - started)
                eta = (total - i) / rate / 60
                print(
                    f"  [{i}/{total}] ok={done} android={android} falhas={failed} "
                    f"· {rate * 60:.0f}/min · ETA {eta:.0f}min",
                    flush=True,
                )

    print(f"probe: {done} ok, {failed} falhas, {android} excluídos por Android")
    return 0


def run_sample_pilot(store: Store, study: Study, *, n: int) -> int:
    run = new_run_id()
    rows = store.query(
        "SELECT pr.project_id, pr.build_system, pr.module_count, pr.has_external_parent, "
        "       pr.head_baseline FROM probe pr "
        "LEFT JOIN exclusions e ON e.project_id = pr.project_id "
        "WHERE e.project_id IS NULL"
    )
    if not rows:
        print("nenhum projeto com probe. Rode `jmod probe` antes.", file=sys.stderr)
        return 2

    matrix = stratum_matrix(rows)
    draws = stratified_draw(rows, study.seed, n)

    with store.phase_run(run, PHASE_PILOT):
        store.upsert(
            "pilot_sample",
            [
                {
                    "project_id": d.project_id,
                    "stratum": d.stratum,
                    "draw_rank": d.rank,
                    "seed": study.seed,
                    "pipeline_version": PIPELINE_VERSION,
                    "collected_at": now(),
                }
                for d in draws
            ],
        )

    print(f"população elegível: {len(rows)} · células de estrato preenchidas: {len(matrix)}")
    print(f"sorteados: {len(draws)} (seed {study.seed})\n")
    for d in draws:
        print(f"  {d.rank:>2}. {d.project_id:<44} {d.stratum}")
    print("\nmatriz de estratos (população):")
    for key, count in matrix.items():
        print(f"  {key:<52} {count:>5}")
    return 0
