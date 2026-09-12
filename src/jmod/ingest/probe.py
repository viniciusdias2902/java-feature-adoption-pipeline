"""Shallow probe do HEAD (`--depth 1`): atributos de estratificação para a Fase P.

Deliberadamente barato e deliberadamente *aproximado*. Não é a detecção de baseline —
isso é a Fase 5/6, sobre o histórico inteiro e com hierarquia de evidência. Aqui só se
quer o suficiente para estratificar o sorteio do piloto, e um erro aqui desbalanceia a
amostra sem corromper nenhum resultado.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAVEN_RELEASE = re.compile(
    r"<(?:maven\.compiler\.)?(?:release|source|target)>\s*(?:1\.)?(\d{1,2})\s*</", re.I
)
MAVEN_JAVA_PROP = re.compile(r"<java\.version>\s*(?:1\.)?(\d{1,2})\s*</java\.version>", re.I)
MAVEN_PARENT = re.compile(r"<parent>(.*?)</parent>", re.I | re.S)
MAVEN_RELATIVE_PATH = re.compile(r"<relativePath>\s*(.*?)\s*</relativePath>", re.I | re.S)
GRADLE_VERSION = re.compile(
    r"(?:sourceCompatibility|targetCompatibility|languageVersion)\s*"
    r"(?:=|\.set\(|\s)\s*[^\n]*?(?:JavaVersion\.VERSION_)?(?:1[._])?(\d{1,2})",
    re.I,
)
ANDROID_PLUGIN = re.compile(r"com\.android\.(application|library)", re.I)


@dataclass(frozen=True)
class ProbeResult:
    head_sha: str | None
    build_system: str
    module_count: int
    has_external_parent: bool
    head_baseline: int | None
    head_baseline_source: str | None
    is_android: bool
    error: str | None = None


def _run(
    args: list[str], cwd: Path | None = None, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def shallow_clone(
    clone_url: str, dest: Path, branch: str | None = None, timeout: int = 180
) -> str | None:
    args = ["git", "clone", "--depth", "1", "--quiet", "--filter=blob:none", "--no-tags"]
    if branch:
        args += ["--branch", branch]
    args += [clone_url, str(dest)]
    if _run(args, timeout=timeout).returncode != 0:
        return None
    head = _run(["git", "rev-parse", "HEAD"], cwd=dest)
    return head.stdout.strip() or None


def inspect_tree(root: Path) -> ProbeResult:
    """Inspeciona uma árvore já materializada. Separado do clone para poder ser testado."""
    # `rglob` casa diretório também: `jib-core/examples/build.gradle` é uma pasta no
    # googlecontainertools/jib, e `read_text` nela estourava IsADirectoryError, derrubando
    # o probe do projeto inteiro. Mesmo defeito da D-017, em segundo lugar (D-025).
    def _arquivos(padrao: str) -> list[Path]:
        return [p for p in root.rglob(padrao) if ".git" not in p.parts and p.is_file()]

    poms = _arquivos("pom.xml")
    groovy = _arquivos("build.gradle")
    kts = _arquivos("build.gradle.kts")

    if poms and (groovy or kts):
        build_system = "mixed"
    elif poms:
        build_system = "maven"
    elif kts and not groovy:
        build_system = "gradle_kts"
    elif groovy or kts:
        build_system = "gradle_groovy"
    else:
        build_system = "none"

    module_count = max(len(poms), len(groovy) + len(kts), 0)

    is_android = any(
        ANDROID_PLUGIN.search(p.read_text(encoding="utf-8", errors="replace"))
        for p in (groovy + kts)[:40]
    )

    has_external_parent = False
    for pom in poms[:40]:
        text = pom.read_text(encoding="utf-8", errors="replace")
        for parent in MAVEN_PARENT.findall(text):
            rel = MAVEN_RELATIVE_PATH.search(parent)
            # relativePath vazio é a forma canônica de dizer "parent é remoto"
            if rel is None or not rel.group(1):
                has_external_parent = True
                break
        if has_external_parent:
            break

    version, source = _head_baseline(root, poms, groovy, kts)
    return ProbeResult(
        head_sha=None,
        build_system=build_system,
        module_count=module_count,
        has_external_parent=has_external_parent,
        head_baseline=version,
        head_baseline_source=source,
        is_android=is_android,
    )


def _head_baseline(
    root: Path, poms: list[Path], groovy: list[Path], kts: list[Path]
) -> tuple[int | None, str | None]:
    """Menor versão encontrada, preferindo a raiz. Aproximação — ver docstring do módulo."""
    ordered = sorted(poms, key=lambda p: len(p.relative_to(root).parts))
    for pom in ordered[:40]:
        text = pom.read_text(encoding="utf-8", errors="replace")
        found = [int(v) for v in MAVEN_RELEASE.findall(text)]
        if found:
            return min(found), "maven_compiler"
        prop = MAVEN_JAVA_PROP.search(text)
        if prop:
            return int(prop.group(1)), "maven_java_property"

    for build in sorted(groovy + kts, key=lambda p: len(p.relative_to(root).parts))[:40]:
        text = build.read_text(encoding="utf-8", errors="replace")
        found = [int(v) for v in GRADLE_VERSION.findall(text)]
        if found:
            return min(found), "gradle"
    return None, None


def probe_repo(clone_url: str, branch: str | None = None) -> ProbeResult:
    tmp = Path(tempfile.mkdtemp(prefix="jmod-probe-"))
    dest = tmp / "repo"
    try:
        sha = shallow_clone(clone_url, dest, branch)
        if sha is None:
            return ProbeResult(None, "none", 0, False, None, None, False, error="clone_failed")
        result = inspect_tree(dest)
        return ProbeResult(
            head_sha=sha,
            build_system=result.build_system,
            module_count=result.module_count,
            has_external_parent=result.has_external_parent,
            head_baseline=result.head_baseline,
            head_baseline_source=result.head_baseline_source,
            is_android=result.is_android,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(None, "none", 0, False, None, None, False, error="timeout")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
