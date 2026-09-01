"""Clonagem e workspace.

Estratégia: `--filter=blob:none --bare`. Bare porque nenhuma fase precisa de árvore de
trabalho — tudo é lido via `git show`. Blobless porque a maior parte dos blobs nunca é
tocada: só os arquivos de build e os `.java` dos commits candidatos.

O clone é **descartável**; os SHAs, não. Um projeto já processado tem seu clone removido,
e reprocessá-lo custa uma nova clonagem — nunca perda de dado.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jmod.repo.git import Repo


class DiskBudgetExceeded(RuntimeError):
    pass


@dataclass
class DiskBudget:
    """Teto rígido de disco. Aborta a fila em vez de encher o disco."""

    root: Path
    limit_bytes: int

    @classmethod
    def from_gb(cls, root: Path, gb: float) -> DiskBudget:
        return cls(root, int(gb * 1024**3))

    def used_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())

    def free_bytes(self) -> int:
        usage = shutil.disk_usage(self.root if self.root.exists() else self.root.parent)
        return usage.free

    def check(self, headroom_bytes: int = 2 * 1024**3) -> None:
        used = self.used_bytes()
        if used >= self.limit_bytes:
            raise DiskBudgetExceeded(
                f"workspace em {used / 1024**3:.1f} GB, teto {self.limit_bytes / 1024**3:.0f} GB"
            )
        if self.free_bytes() < headroom_bytes:
            raise DiskBudgetExceeded(
                f"disco com apenas {self.free_bytes() / 1024**3:.1f} GB livres"
            )


class Workspace:
    def __init__(self, root: Path, budget: DiskBudget | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = budget

    def path_for(self, project_id: str) -> Path:
        return self.root / project_id.replace("/", "__")

    def has(self, project_id: str) -> bool:
        p = self.path_for(project_id)
        return (p / "HEAD").exists()

    def clone(self, project_id: str, clone_url: str, timeout: int = 1800) -> Repo:
        """Idempotente: um clone já presente é reaproveitado."""
        dest = self.path_for(project_id)
        if self.has(project_id):
            return Repo(dest)
        if self.budget is not None:
            self.budget.check()

        shutil.rmtree(dest, ignore_errors=True)
        proc = subprocess.run(  # noqa: S603
            [
                "git",
                "clone",
                "--bare",
                "--filter=blob:none",
                "--no-tags",
                "--quiet",
                clone_url,
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            errors="replace",
        )
        if proc.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(f"clone falhou ({proc.returncode}): {proc.stderr.strip()[:300]}")
        return Repo(dest)

    def release(self, project_id: str) -> None:
        """Descarta o clone. Chamado só depois de o projeto estar persistido."""
        shutil.rmtree(self.path_for(project_id), ignore_errors=True)

    def size_gb(self) -> float:
        total = sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
        return total / 1024**3
