"""Idempotência e proveniência: a pipeline precisa poder ser reexecutada sem dano."""

from __future__ import annotations

from pathlib import Path

import pytest

from jmod.db import Store
from jmod.provenance import PIPELINE_VERSION, new_run_id, now


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "t.sqlite")
    s.init_schema()
    return s


def _project(pid: str = "acme/lib") -> dict[str, object]:
    return {
        "project_id": pid,
        "full_name": pid,
        "clone_url": f"https://github.com/{pid}.git",
        "default_branch": "main",
        "frame_source": "seart",
        "seart_stars": 500,
        "seart_commits": 2000,
        "seart_loc_java": 50000,
        "seart_created_at": "2015-01-01",
        "seart_last_commit_at": "2026-01-01",
        "gh_archived": None,
        "gh_disabled": None,
        "gh_fork": None,
        "gh_pushed_at": None,
        "gh_size_kb": None,
        "gh_validated_at": None,
        "pipeline_version": PIPELINE_VERSION,
        "collected_at": now(),
    }


def test_schema_e_idempotente(store: Store) -> None:
    antes = store.tables()
    store.init_schema()
    store.init_schema()
    assert store.tables() == antes
    assert "projects" in antes and "exclusions" in antes


def test_upsert_nao_duplica(store: Store) -> None:
    store.upsert("projects", [_project()])
    store.upsert("projects", [_project()])
    assert store.count("projects") == 1


def test_upsert_atualiza_campos_nao_chave(store: Store) -> None:
    store.upsert("projects", [_project()])
    row = dict(_project())
    row["seart_stars"] = 999
    store.upsert("projects", [row])
    assert store.count("projects") == 1
    assert store.scalar("SELECT seart_stars FROM projects") == 999


def test_exclusao_exige_motivo(store: Store) -> None:
    with pytest.raises(ValueError, match="motivo"):
        store.exclude("acme/lib", "ingest", "")


def test_exclusao_e_idempotente(store: Store) -> None:
    store.exclude("acme/lib", "ingest", "android")
    store.exclude("acme/lib", "ingest", "android")
    assert store.count("exclusions") == 1


def test_run_log_registra_sucesso_e_permite_retomada(store: Store) -> None:
    run = new_run_id()
    assert not store.completed("ingest")
    with store.phase_run(run, "ingest"):
        pass
    assert store.completed("ingest")


def test_run_log_registra_falha_e_repropaga(store: Store) -> None:
    run = new_run_id()
    with pytest.raises(RuntimeError), store.phase_run(run, "probe", "acme/lib"):
        raise RuntimeError("boom")
    assert not store.completed("probe", "acme/lib")
    row = store.query("SELECT status, error FROM run_log WHERE phase='probe'")[0]
    assert row["status"] == "failed"
    assert "boom" in row["error"]


def test_lote_com_colunas_divergentes_e_rejeitado(store: Store) -> None:
    bad = dict(_project("acme/other"))
    bad.pop("gh_size_kb")
    with pytest.raises(ValueError, match="mesmas colunas"):
        store.upsert("projects", [_project(), bad])


def test_tabela_desconhecida_falha_alto(store: Store) -> None:
    with pytest.raises(ValueError, match="desconhecida"):
        store.upsert("nao_existe", [{"a": 1}])
