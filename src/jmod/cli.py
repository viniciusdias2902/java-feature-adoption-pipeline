"""Ponto de entrada. Uma subfase por comando, todas retomáveis."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jmod import PIPELINE_VERSION
from jmod.config import ConfigError, load_study
from jmod.db import Store

DEFAULT_DB = Path("data/db/study.sqlite")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jmod", description="Pipeline do estudo longitudinal Java")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="caminho do SQLite")
    p.add_argument("--version", action="version", version=PIPELINE_VERSION)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="cria o banco e valida a configuração")
    sub.add_parser("status", help="resumo do estado atual do banco")

    ing = sub.add_parser("ingest", help="fase 2: carrega o quadro amostral")
    ing.add_argument("--csv", type=Path, help="export do SEART")
    ing.add_argument(
        "--from-github-search",
        action="store_true",
        help="quadro provisório via GitHub Search (fallback documentado)",
    )
    ing.add_argument("--limit", type=int, default=0)

    val = sub.add_parser("validate", help="fase 2: valida repositórios via GitHub API")
    val.add_argument("--limit", type=int, default=0)

    pr = sub.add_parser("probe", help="fase 2: shallow probe do HEAD")
    pr.add_argument("--limit", type=int, default=0)

    sam = sub.add_parser("sample-pilot", help="fase P: sorteio estratificado determinístico")
    sam.add_argument("-n", type=int, default=15)
    return p


def _cmd_status(store: Store) -> int:
    interesting = [
        "projects",
        "exclusions",
        "probe",
        "pilot_sample",
        "baseline_observations",
        "feature_occurrences",
        "adoption_outcomes",
    ]
    print(f"banco: {store.path}")
    for t in interesting:
        if t in store.tables():
            print(f"  {t:24} {store.count(t):>8}")
    rows = store.query(
        "SELECT reason, COUNT(*) n FROM exclusions GROUP BY reason ORDER BY n DESC LIMIT 10"
    )
    if rows:
        print("exclusões por motivo:")
        for r in rows:
            print(f"  {r['reason']:24} {r['n']:>8}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        study = load_study()
    except (ConfigError, FileNotFoundError) as exc:
        print(f"configuração inválida: {exc}", file=sys.stderr)
        return 2

    with Store(args.db) as store:
        store.init_schema()

        if args.command == "init":
            print(f"banco em {store.path} · {len(store.tables())} tabelas")
            print(
                f"freeze {study.freeze_date} · janela desde {study.window_start} "
                f"· {len(study.features)} features · seed {study.seed}"
            )
            return 0

        if args.command == "status":
            return _cmd_status(store)

        from jmod.ingest import pipeline as ingest_pipeline

        if args.command == "ingest":
            return ingest_pipeline.run_ingest(
                store,
                study,
                csv=args.csv,
                from_github_search=args.from_github_search,
                limit=args.limit,
            )
        if args.command == "validate":
            return ingest_pipeline.run_validate(store, study, limit=args.limit)
        if args.command == "probe":
            return ingest_pipeline.run_probe(store, study, limit=args.limit)
        if args.command == "sample-pilot":
            return ingest_pipeline.run_sample_pilot(store, study, n=args.n)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
