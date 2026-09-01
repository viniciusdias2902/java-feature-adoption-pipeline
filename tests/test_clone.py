"""Workspace, idempotência de clone e teto rígido de disco."""

from __future__ import annotations

from pathlib import Path

import pytest

from jmod.repo.clone import DiskBudget, DiskBudgetExceeded, Workspace


def test_clone_local_e_idempotente(synthetic, tmp_path: Path) -> None:
    origem = synthetic("origem")
    origem.commit({"pom.xml": "<project/>"})
    ws = Workspace(tmp_path / "ws")

    r1 = ws.clone("acme/lib", str(origem.path))
    assert ws.has("acme/lib")
    sha1 = r1.head_sha()

    r2 = ws.clone("acme/lib", str(origem.path))  # segunda vez reaproveita
    assert r2.head_sha() == sha1


def test_release_descarta_o_clone(synthetic, tmp_path: Path) -> None:
    origem = synthetic("origem")
    origem.commit({"a": "x"})
    ws = Workspace(tmp_path / "ws")
    ws.clone("acme/lib", str(origem.path))
    ws.release("acme/lib")
    assert not ws.has("acme/lib")


def test_id_com_barra_vira_diretorio_plano(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    assert ws.path_for("acme/lib").name == "acme__lib"


def test_clone_de_url_invalida_nao_deixa_lixo(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(RuntimeError, match="clone falhou"):
        ws.clone("acme/lib", str(tmp_path / "nao-existe"), timeout=30)
    assert not ws.path_for("acme/lib").exists(), "clone falho não pode deixar diretório parcial"


def test_orcamento_aborta_ao_estourar(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "grande.bin").write_bytes(b"x" * 4096)
    budget = DiskBudget(root=root, limit_bytes=1024)
    with pytest.raises(DiskBudgetExceeded, match="teto"):
        budget.check()


def test_orcamento_passa_dentro_do_limite(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "pequeno.bin").write_bytes(b"x" * 10)
    DiskBudget.from_gb(root, 1.0).check()


def test_clone_verifica_orcamento_antes_de_baixar(synthetic, tmp_path: Path) -> None:
    origem = synthetic("origem")
    origem.commit({"a": "x"})
    root = tmp_path / "ws"
    root.mkdir()
    (root / "enche.bin").write_bytes(b"x" * 4096)
    ws = Workspace(root, budget=DiskBudget(root=root, limit_bytes=1024))
    with pytest.raises(DiskBudgetExceeded):
        ws.clone("acme/lib", str(origem.path))
