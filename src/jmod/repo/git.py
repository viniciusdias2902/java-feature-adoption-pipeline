"""Wrapper fino sobre o `git` CLI.

Fino de propósito: `git log --first-parent`, pickaxe e partial clone são exatamente o
que a pipeline precisa, e nenhuma biblioteca Python os expõe sem custo. Todo comando tem
timeout e captura de stderr — um repositório patológico não pode travar a fila.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

RECORD = "\x1e"  # separador de commit no --format


class GitError(RuntimeError):
    def __init__(self, args: Sequence[str], code: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args[:4])}… falhou ({code}): {stderr.strip()[:300]}")
        self.code = code
        self.stderr = stderr


@dataclass(frozen=True)
class Commit:
    sha: str
    committed_at: str  # ISO-8601, committer date


@dataclass(frozen=True)
class FileChange:
    sha: str
    committed_at: str
    path: str
    change_type: str  # A | M | D | R
    old_path: str | None


class Repo:
    """Um clone bare já materializado."""

    def __init__(self, path: Path, timeout: int = 600) -> None:
        self.path = Path(path)
        self.timeout = timeout

    def run(self, *args: str, timeout: int | None = None) -> str:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
            check=False,
            errors="replace",
        )
        if proc.returncode != 0:
            raise GitError(args, proc.returncode, proc.stderr)
        return proc.stdout

    def run_bytes(self, *args: str, timeout: int | None = None) -> bytes:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            timeout=timeout or self.timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(args, proc.returncode, proc.stderr.decode(errors="replace"))
        return proc.stdout

    # ------------------------------------------------------------------ básico
    def default_branch(self) -> str:
        """Lê do repositório. Nunca assuma `main` ou `master` — ver rxjava 3.x vs 4.x."""
        try:
            head = self.run("symbolic-ref", "--short", "HEAD").strip()
            if head:
                return head
        except GitError:
            pass
        for candidate in ("main", "master", "trunk", "develop"):
            try:
                self.run("rev-parse", "--verify", f"refs/heads/{candidate}")
                return candidate
            except GitError:
                continue
        raise GitError(("default_branch",), 1, "nenhuma branch default identificável")

    def head_sha(self, ref: str = "HEAD") -> str:
        return self.run("rev-parse", ref).strip()

    def show(self, sha: str, path: str) -> bytes:
        """Conteúdo de um arquivo num commit. Em blobless clone, busca o blob sob demanda."""
        return self.run_bytes("show", f"{sha}:{path}")

    def ls_files(self, sha: str, *pathspecs: str) -> list[str]:
        out = self.run("ls-tree", "-r", "--name-only", "-z", sha, "--", *pathspecs)
        return [p for p in out.split("\0") if p]

    def backfill(self, patterns: Sequence[str], min_batch: int = 8000) -> bool:
        """Pré-busca em lote os blobs históricos dos caminhos dados (`git backfill --sparse`).

        Num clone blobless, cada blob ausente custa uma ida à rede na primeira leitura —
        1.166 commits tocando `pom.xml` (caso real: yegor256/rultor, maven-release-plugin
        versiona o POM a cada release) viram 1.166 round-trips. O backfill esparso baixa
        todos numa única sessão. Retorna False se indisponível; a leitura preguiçosa
        continua funcionando como fallback, apenas lenta.
        """
        info = self.path / "info"
        info.mkdir(exist_ok=True)
        (info / "sparse-checkout").write_text("\n".join(patterns) + "\n", encoding="utf-8")
        try:
            self.run("config", "core.sparseCheckout", "true")
            self.run("backfill", "--sparse", f"--min-batch-size={min_batch}", timeout=1800)
            return True
        except GitError:
            return False

    def commit_count(self, ref: str) -> int:
        return int(self.run("rev-list", "--count", ref).strip() or 0)

    # -------------------------------------------------------------------- log
    def log_changes(
        self, ref: str, *pathspecs: str, first_parent: bool = True, follow_renames: bool = True
    ) -> Iterator[FileChange]:
        """Commits que alteram os caminhos dados, com o tipo de alteração.

        `--diff-filter=AMDR` inclui deleções deliberadamente: sem elas não há como
        detectar abandono de feature nem remoção de arquivo de build.
        """
        args = ["log", ref, f"--format={RECORD}%H %cI", "--name-status", "--diff-filter=AMDR"]
        if first_parent:
            args.append("--first-parent")
        if follow_renames:
            args.append("-M")
        if pathspecs:
            args += ["--", *pathspecs]

        out = self.run(*args)
        for block in out.split(RECORD):
            if not block.strip():
                continue
            lines = block.strip("\n").split("\n")
            sha, _, when = lines[0].partition(" ")
            for line in lines[1:]:
                if not line.strip():
                    continue
                parts = line.split("\t")
                status = parts[0]
                if status.startswith("R") and len(parts) >= 3:
                    yield FileChange(sha, when, parts[2], "R", parts[1])
                elif len(parts) >= 2:
                    yield FileChange(sha, when, parts[1], status[0], None)

    def pickaxe(
        self,
        ref: str,
        *,
        literal: str | None = None,
        regex: str | None = None,
        pathspecs: Sequence[str] = (),
        first_parent: bool = True,
    ) -> Iterator[FileChange]:
        """Commits candidatos.

        `-S` (literal) casa commits onde a CONTAGEM do texto muda — é barato, mas não vê
        transformações que preservam a contagem. `-G` (regex) casa qualquer linha de diff
        que bata, é mais caro e mais abrangente. A escolha é por feature, e o argumento é
        de recall (ver config/features.toml e PLAN.md Fase 10).
        """
        if (literal is None) == (regex is None):
            raise ValueError("informe exatamente um entre literal e regex")
        args = ["log", ref, f"--format={RECORD}%H %cI", "--name-status", "--diff-filter=AMDR", "-M"]
        if first_parent:
            args.append("--first-parent")
        # -G já interpreta o padrão como regex; --pickaxe-regex só se aplica a -S.
        args.append(f"-S{literal}" if literal is not None else f"-G{regex}")
        if pathspecs:
            args += ["--", *pathspecs]

        out = self.run(*args)
        for block in out.split(RECORD):
            if not block.strip():
                continue
            lines = block.strip("\n").split("\n")
            sha, _, when = lines[0].partition(" ")
            for line in lines[1:]:
                parts = line.split("\t")
                if len(parts) >= 2:
                    path = parts[2] if parts[0].startswith("R") and len(parts) >= 3 else parts[1]
                    old = parts[1] if parts[0].startswith("R") and len(parts) >= 3 else None
                    yield FileChange(sha, when, path, parts[0][0], old)
