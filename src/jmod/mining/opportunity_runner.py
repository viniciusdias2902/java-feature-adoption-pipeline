"""Fase 12: contagem de oportunidades no HEAD de cada projeto minerado.

Denominador aproximado por decisão registrada: uma medição no HEAD, não a série
histórica. Clone raso (como o probe), varredura AST única por arquivo com os dois
proxies, escopo por caminho, e descarte imediato do clone.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from jmod.config import Study
from jmod.db import Store
from jmod.features.opportunity import PROXIES, count_proxy
from jmod.features.parser import ScopeRules, parse
from jmod.provenance import new_run_id, now

PHASE = "opportunity"


def _scan_head(clone_url: str, rules: ScopeRules) -> tuple[str, dict[tuple[str, str], int]]:
    tmp = Path(tempfile.mkdtemp(prefix="jmod-opp-"))
    dest = tmp / "r"
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "clone", "--depth", "1", "--quiet", "--no-tags", clone_url, str(dest)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"clone falhou: {proc.stderr.strip()[:200]}")
        sha = subprocess.run(  # noqa: S603
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout.strip()

        counts: dict[tuple[str, str], int] = {}
        for java in dest.rglob("*.java"):
            if ".git" in java.parts:
                continue
            content = java.read_bytes()
            rel = str(java.relative_to(dest))
            scope = rules.classify(rel, content)
            tree = parse(content)
            for proxy in PROXIES:
                found = count_proxy(proxy, tree, content)
                if found:
                    key = (proxy, scope)
                    counts[key] = counts.get(key, 0) + found
        return sha, counts
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_opportunity(store: Store, study: Study, *, limit: int = 0) -> int:
    run = new_run_id()
    rules = ScopeRules.from_config(study.paths)
    workers = int(study.storage["workers"])
    db_path = store.path

    rows = store.query(
        "SELECT r.project_id, p.clone_url FROM run_log r "
        "JOIN projects p USING (project_id) "
        "LEFT JOIN opportunity_series o ON o.project_id = r.project_id "
        "WHERE r.phase='mine' AND r.status='ok' AND o.project_id IS NULL "
        "GROUP BY r.project_id" + (f" LIMIT {int(limit)}" if limit else "")
    )
    total = len(rows)
    if not total:
        print("oportunidade: nada a fazer")
        return 0
    print(f"oportunidade no HEAD de {total} projetos com {workers} workers", flush=True)

    done = failed = 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_head, r["clone_url"], rules): r["project_id"] for r in rows}
        for i, future in enumerate(as_completed(futures), 1):
            pid = futures[future]
            with Store(db_path) as tstore:
                tstore.conn.execute("PRAGMA busy_timeout = 60000")
                try:
                    sha, counts = future.result()
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    tstore.upsert(
                        "run_log",
                        [
                            {
                                "run_id": run,
                                "phase": PHASE,
                                "project_id": pid,
                                "status": "failed",
                                "started_at": now(),
                                "ended_at": now(),
                                "error": f"{type(exc).__name__}: {exc}"[:300],
                                "pipeline_version": __import__("jmod").PIPELINE_VERSION,
                            }
                        ],
                    )
                else:
                    done += 1
                    batch = [
                        {
                            "project_id": pid,
                            "proxy_id": key[0],
                            "scope": key[1],
                            "sha": sha,
                            "committed_at": now(),
                            "count": value,
                        }
                        for key, value in counts.items()
                    ] or [
                        {
                            "project_id": pid,
                            "proxy_id": "instanceof_cast",
                            "scope": "production",
                            "sha": sha,
                            "committed_at": now(),
                            "count": 0,
                        }
                    ]
                    tstore.upsert("opportunity_series", batch)
            if i % 50 == 0 or i == total:
                rate = i / max(1e-6, time.monotonic() - started)
                print(
                    f"  --- [{i}/{total}] ok={done} falhas={failed} "
                    f"· {rate * 3600:.0f}/h · ETA {(total - i) / rate / 60:.0f}min",
                    flush=True,
                )
    print(f"oportunidade: {done} ok, {failed} falhas")
    return 0
