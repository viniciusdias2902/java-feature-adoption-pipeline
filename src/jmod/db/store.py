"""Acesso ao SQLite.

Duas garantias sustentam a retomada de execuções interrompidas:

1. **Idempotência** — todo `upsert` usa a chave natural da tabela. Reprocessar um
   projeto sobrescreve suas linhas em vez de duplicá-las.
2. **Nenhum descarte silencioso** — `exclude()` é o único caminho para tirar um
   projeto da população, e ele exige um motivo.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jmod.provenance import PIPELINE_VERSION, now

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    # ------------------------------------------------------------------ schema
    def init_schema(self) -> None:
        """Aplica o esquema. Reexecutar é inócuo (todo DDL é IF NOT EXISTS)."""
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('pipeline_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (PIPELINE_VERSION,),
        )
        self.conn.commit()

    def tables(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    # ------------------------------------------------------------------ escrita
    def upsert(self, table: str, rows: Iterable[Mapping[str, Any]]) -> int:
        """Insere ou atualiza pela chave natural da tabela. Retorna linhas afetadas."""
        rows = list(rows)
        if not rows:
            return 0
        cols = list(rows[0])
        if any(list(r) != cols for r in rows):
            raise ValueError("todas as linhas do lote precisam ter as mesmas colunas")
        keys = self._primary_key(table)
        updatable = [c for c in cols if c not in keys]
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"  # noqa: S608
        if keys and updatable:
            assign = ", ".join(f"{c} = excluded.{c}" for c in updatable)
            sql += f" ON CONFLICT({', '.join(keys)}) DO UPDATE SET {assign}"
        elif keys:
            sql += f" ON CONFLICT({', '.join(keys)}) DO NOTHING"
        with self.conn:
            self.conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        return len(rows)

    def exclude(self, project_id: str, phase: str, reason: str, detail: str | None = None) -> None:
        """Único caminho para remover um projeto da população."""
        if not reason:
            raise ValueError("exclusão exige motivo")
        self.upsert(
            "exclusions",
            [
                {
                    "project_id": project_id,
                    "phase": phase,
                    "reason": reason,
                    "detail": detail,
                    "pipeline_version": PIPELINE_VERSION,
                    "collected_at": now(),
                }
            ],
        )

    # ------------------------------------------------------------------ leitura
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def count(self, table: str) -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {table}") or 0)  # noqa: S608

    # ------------------------------------------------------------------ run log
    @contextmanager
    def phase_run(self, run_id: str, phase: str, project_id: str = "") -> Iterator[None]:
        """Marca início/fim de uma fase. Falha é registrada e repropagada."""
        base = {
            "run_id": run_id,
            "phase": phase,
            "project_id": project_id,
            "started_at": now(),
            "pipeline_version": PIPELINE_VERSION,
        }
        self.upsert("run_log", [{**base, "status": "started", "ended_at": None, "error": None}])
        try:
            yield
        except Exception as exc:
            self.upsert(
                "run_log",
                [
                    {
                        **base,
                        "status": "failed",
                        "ended_at": now(),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                ],
            )
            raise
        self.upsert("run_log", [{**base, "status": "ok", "ended_at": now(), "error": None}])

    def completed(self, phase: str, project_id: str = "") -> bool:
        """Retomada: esta fase já terminou com sucesso para este projeto?"""
        return bool(
            self.scalar(
                "SELECT 1 FROM run_log WHERE phase = ? AND project_id = ? "
                "AND status = 'ok' LIMIT 1",
                (phase, project_id),
            )
        )

    # ------------------------------------------------------------------ interno
    def _primary_key(self, table: str) -> list[str]:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            raise ValueError(f"tabela desconhecida: {table}")
        keyed = [(r["pk"], r["name"]) for r in rows if r["pk"]]
        return [name for _, name in sorted(keyed)]

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
