"""Fases P e 14: executa a verificação independente e produz os números de acurácia."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from jmod.config import Study
from jmod.db import Store
from jmod.events.timeline import BACKFILL_PATTERNS
from jmod.features.parser import ScopeRules
from jmod.provenance import PIPELINE_VERSION, new_run_id, now
from jmod.repo.clone import Workspace
from jmod.validation.independent import ProjectDossier, check_first_use, check_version_event

OUT = Path("data/validation")
PHASE = "validate"
JSONL = OUT / "dossiers.jsonl"


def _load_done() -> dict[str, dict]:  # type: ignore[type-arg]
    """Dossiês já produzidos, por projeto. Registros com erro não contam como feitos.

    A fase escreve um JSON por linha assim que cada projeto termina; `dossiers.json` só é
    montado no fim. Sem isso, uma interrupção (a primeira execução morreu num `poweroff`
    aos 31/35) descartava todo o trabalho, e a fase era a única do pipeline não retomável.
    """
    if not JSONL.exists():
        return {}
    done: dict[str, dict] = {}  # type: ignore[type-arg]
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        done[rec["project_id"]] = rec
    return {pid: rec for pid, rec in done.items() if "error" not in rec}


def _pipeline_first_use(
    store: Store, pid: str, feature_id: str
) -> tuple[str | None, str | None, str | None]:
    row = store.query(
        "SELECT first_use_date, first_use_sha FROM adoption_outcomes "
        "WHERE project_id=? AND feature_id=? AND scope='production'",
        (pid, feature_id),
    )
    if not row or row[0]["first_use_date"] is None:
        return None, None, None
    date, sha = row[0]["first_use_date"], row[0]["first_use_sha"]
    occ = store.query(
        "SELECT path FROM feature_occurrences WHERE project_id=? AND feature_id=? "
        "AND sha=? AND scope='production' LIMIT 1",
        (pid, feature_id, sha),
    )
    return date, sha, occ[0]["path"] if occ else None


def _projects(store: Store, study: Study, blind: int) -> list[tuple[str, str, str]]:
    pilot = [
        (r["project_id"], r["clone_url"], "pilot")
        for r in store.query(
            "SELECT ps.project_id, p.clone_url FROM pilot_sample ps "
            "JOIN projects p USING (project_id) "
            "JOIN run_log r ON r.project_id = ps.project_id "
            "  AND r.phase='mine' AND r.status='ok' "
            "GROUP BY ps.project_id ORDER BY ps.draw_rank"
        )
    ]
    if not blind:
        return pilot
    mined = store.query(
        "SELECT DISTINCT r.project_id, p.clone_url FROM run_log r "
        "JOIN projects p USING (project_id) "
        "LEFT JOIN pilot_sample ps USING (project_id) "
        "WHERE r.phase='mine' AND r.status='ok' AND ps.project_id IS NULL"
    )
    ranked = sorted(
        mined,
        key=lambda r: hashlib.sha256(f"{study.seed}:blind:{r['project_id']}".encode()).hexdigest(),
    )
    return pilot + [(r["project_id"], r["clone_url"], "blind") for r in ranked[:blind]]


def run_validate(store: Store, study: Study, *, blind: int = 20, redo: bool = False) -> int:
    run = new_run_id()
    rules = ScopeRules.from_config(study.paths)
    workspace = Workspace(Path("data/work"))
    features = [study.feature(fid) for fid in study.pilot["labeled_features"]]
    targets = _projects(store, study, blind)
    OUT.mkdir(parents=True, exist_ok=True)

    if redo:
        JSONL.unlink(missing_ok=True)
    done = _load_done()
    pending = [t for t in targets if t[0] not in done]
    dossiers: list[dict] = [done[pid] for pid, _, _ in targets if pid in done]  # type: ignore[type-arg]
    print(
        f"validação independente: {len(targets)} projetos × {len(features)} features"
        + (f" ({len(done)} já feitos, {len(pending)} pendentes)" if done else "")
    )
    with store.phase_run(run, PHASE):
        for i, (pid, url, group) in enumerate(pending, 1):
            started = time.monotonic()
            dossier = ProjectDossier(pid)
            try:
                repo = workspace.clone(pid, url)
                repo.backfill(list(BACKFILL_PATTERNS) + ["*.java"])
                ref = repo.default_branch()

                for feature in features:
                    date, sha, path = _pipeline_first_use(store, pid, feature.id)
                    dossier.first_uses.append(
                        check_first_use(repo, ref, feature, rules, date, sha, path)
                    )

                for ev in store.query(
                    "SELECT sha, kind, to_version FROM version_events "
                    "WHERE project_id=? AND kind IN ('upgrade','downgrade') "
                    "ORDER BY committed_at",
                    (pid,),
                ):
                    dossier.events.append(
                        check_version_event(repo, ev["sha"], ev["to_version"], ev["kind"])
                    )
            except Exception as exc:  # noqa: BLE001
                record = {
                    "project_id": pid,
                    "group": group,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                }
                dossiers.append(record)
                _append(record)
                _log_project(store, run, pid, "failed", record["error"])
                workspace.release(pid)
                continue

            workspace.release(pid)
            record = {
                "project_id": pid,
                "group": group,
                "first_uses": [asdict(c) for c in dossier.first_uses],
                "events": [asdict(c) for c in dossier.events],
            }
            dossiers.append(record)
            _append(record)
            _log_project(store, run, pid, "ok", None)
            verdicts = [c.verdict for c in dossier.first_uses]
            confirmed = sum(e.confirmed for e in dossier.events)
            print(
                f"  [{i}/{len(pending)}] {pid:<44} {time.monotonic() - started:5.0f}s "
                f"fu={verdicts} eventos={confirmed}/{len(dossier.events)}",
                flush=True,
            )

    (OUT / "dossiers.json").write_text(
        json.dumps(dossiers, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _summarize(dossiers)
    return 0


def _append(record: dict) -> None:  # type: ignore[type-arg]
    """Persiste um dossiê assim que ele fica pronto — a fase é retomável."""
    with JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _log_project(store: Store, run: str, pid: str, status: str, error: str | None) -> None:
    store.upsert(
        "run_log",
        [
            {
                "run_id": run,
                "phase": PHASE,
                "project_id": pid,
                "status": status,
                "started_at": now(),
                "ended_at": now(),
                "error": error,
                "pipeline_version": PIPELINE_VERSION,
            }
        ],
    )


def _summarize(dossiers: list[dict]) -> None:  # type: ignore[type-arg]
    from collections import Counter, defaultdict

    per_feature: dict[str, Counter[str]] = defaultdict(Counter)
    lags: dict[str, list[float]] = defaultdict(list)
    ev_total = ev_ok = 0
    for d in dossiers:
        for chk in d.get("first_uses", []):
            per_feature[chk["feature_id"]][chk["verdict"]] += 1
            if chk["lag_days"] is not None:
                lags[chk["feature_id"]].append(chk["lag_days"])
        for ev in d.get("events", []):
            ev_total += 1
            ev_ok += bool(ev["confirmed"])

    lines = ["", "=== primeiro uso: pipeline vs grafo completo ==="]
    lines.append(
        f"{'feature':<20} {'TP':>3} {'TN':>3} {'FN':>3} {'FP':>3} "
        f"{'precisão':>9} {'recall':>7} {'lag mediano':>12}"
    )
    for fid, counts in sorted(per_feature.items()):
        tp = counts["both_present"]
        tn = counts["both_absent"]
        fn = counts["pipeline_missed"]
        fp = counts["pipeline_false_positive"]
        precision = tp / (tp + fp) if tp + fp else float("nan")
        recall = tp / (tp + fn) if tp + fn else float("nan")
        lag_list = sorted(lags[fid])
        med = lag_list[len(lag_list) // 2] if lag_list else float("nan")
        lines.append(
            f"{fid:<20} {tp:>3} {tn:>3} {fn:>3} {fp:>3} "
            f"{precision:>9.0%} {recall:>7.0%} {med:>10.1f}d"
        )
    lines.append(
        f"\neventos de baseline confirmados textualmente: {ev_ok}/{ev_total} "
        f"({100 * ev_ok / max(ev_total, 1):.0f}%)"
    )
    print("\n".join(lines))
