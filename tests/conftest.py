"""Construtor de repositórios git sintéticos com histórico controlado."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


class SyntheticRepo:
    """Repositório real, pequeno, com histórico determinístico."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.invalid")
        self._n = 0

    def _git(self, *args: str) -> str:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            check=True,
            errors="replace",
        )
        return proc.stdout

    def commit(self, files: dict[str, str | None], message: str | None = None) -> str:
        """`None` como conteúdo remove o arquivo."""
        self._n += 1
        for rel, body in files.items():
            target = self.path / rel
            if body is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
        self._git("add", "-A")
        # datas fixas: o histórico precisa ser reprodutível entre execuções
        stamp = f"2020-01-{self._n:02d}T12:00:00+00:00"
        env_args = ["-c", "user.name=Test"]
        subprocess.run(  # noqa: S603
            [
                "git",
                "-C",
                str(self.path),
                *env_args,
                "commit",
                "-q",
                "-m",
                message or f"commit {self._n}",
                "--date",
                stamp,
            ],
            check=True,
            capture_output=True,
            env={
                "GIT_COMMITTER_DATE": stamp,
                "PATH": "/usr/bin:/bin",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
                "HOME": str(self.path),
            },
        )
        return self._git("rev-parse", "HEAD").strip()

    def rename(self, old: str, new: str) -> str:
        self._n += 1
        (self.path / new).parent.mkdir(parents=True, exist_ok=True)
        self._git("mv", old, new)
        stamp = f"2020-01-{self._n:02d}T12:00:00+00:00"
        subprocess.run(  # noqa: S603
            ["git", "-C", str(self.path), "commit", "-q", "-m", f"rename {old}", "--date", stamp],
            check=True,
            capture_output=True,
            env={
                "GIT_COMMITTER_DATE": stamp,
                "PATH": "/usr/bin:/bin",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
                "HOME": str(self.path),
            },
        )
        return self._git("rev-parse", "HEAD").strip()


@pytest.fixture
def synthetic(tmp_path: Path):
    def make(name: str = "repo") -> SyntheticRepo:
        return SyntheticRepo(tmp_path / name)

    return make
