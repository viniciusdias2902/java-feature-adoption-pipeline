"""Integração 4->7 contra repositórios git sintéticos: a história completa de um upgrade."""

from __future__ import annotations

from jmod.events.timeline import module_timelines, observations_at
from jmod.repo.git import Repo

FIM = "2026-09-01T00:00:00+00:00"


def _pom(v: object) -> str:
    return (
        "<project><properties>"
        f"<maven.compiler.release>{v}</maven.compiler.release>"
        "</properties></project>"
    )


def timelines(repo_builder):
    return module_timelines(
        Repo(repo_builder.path), "main", persistence_days=30, end_of_observation=FIM
    )


def test_historia_de_um_upgrade_maven(synthetic) -> None:
    r = synthetic()
    r.commit({"pom.xml": _pom(8), "src/A.java": "class A {}"})
    r.commit({"src/A.java": "class A { int x; }"})  # ruído: não toca build
    sha_upgrade = r.commit({"pom.xml": _pom(17)})

    tl = timelines(r)[""]
    assert [i.java_version for i in tl.intervals] == [8, 17]
    assert len(tl.events) == 1
    ev = tl.events[0]
    assert (ev.kind, ev.from_version, ev.to_version) == ("upgrade", 8, 17)
    assert ev.sha == sha_upgrade, "o evento carrega o SHA exato da transição"


def test_commits_que_nao_tocam_build_nao_geram_amostra(synthetic) -> None:
    r = synthetic()
    r.commit({"pom.xml": _pom(11)})
    for i in range(5):
        r.commit({"src/A.java": f"class A {{ int x{i}; }}"})
    tl = timelines(r)[""]
    assert len(tl.samples) == 1, "só o commit de build vira amostra"


def test_upgrade_por_modulo_em_multi_modulo(synthetic) -> None:
    r = synthetic()
    r.commit(
        {
            "pom.xml": _pom(8),
            "core/pom.xml": "<project><parent><artifactId>r</artifactId></parent></project>",
            "api/pom.xml": "<project><parent><artifactId>r</artifactId></parent></project>",
        }
    )
    r.commit({"api/pom.xml": _pom(21)})  # só o api avança

    tls = timelines(r)
    assert tls["api"].events and tls["api"].events[0].to_version == 21
    assert tls["core"].events == [], "o core herdou 8 o tempo todo; sem evento"
    assert tls[""].events == []


def test_migracao_de_maven_para_gradle(synthetic) -> None:
    """Troca de build system com upgrade junto: o evento atravessa os dois mundos."""
    r = synthetic()
    r.commit({"pom.xml": _pom(8)})
    r.commit({"pom.xml": None, "build.gradle": "sourceCompatibility = 17"})
    tl = timelines(r)[""]
    assert [(e.from_version, e.to_version) for e in tl.events] == [(8, 17)]


def test_parent_externo_abre_gap_e_nao_emenda(synthetic) -> None:
    """8 -> (parent externo) -> 8: o gap fica registrado, nada de evento."""
    r = synthetic()
    r.commit({"pom.xml": _pom(8)})
    r.commit(
        {
            "pom.xml": (
                "<project><parent><groupId>x</groupId><artifactId>ext</artifactId>"
                "<relativePath/></parent></project>"
            )
        }
    )
    r.commit({"pom.xml": _pom(8)})
    tl = timelines(r)[""]
    assert [i.java_version for i in tl.intervals] == [8, None, 8]
    assert tl.events == []


def test_observations_at_prefere_maven_resolvido(synthetic) -> None:
    r = synthetic()
    sha = r.commit({"pom.xml": _pom(11), "build.gradle": "sourceCompatibility = 8"})
    obs = observations_at(Repo(r.path), sha)[""]
    assert obs.java_version == 11
    assert obs.source == "maven_compiler_release"


def test_observations_at_usa_gradle_quando_maven_nao_resolve(synthetic) -> None:
    r = synthetic()
    sha = r.commit(
        {
            "pom.xml": (
                "<project><parent><artifactId>ext</artifactId><relativePath/></parent></project>"
            ),
            "build.gradle": "sourceCompatibility = 17",
        }
    )
    obs = observations_at(Repo(r.path), sha)[""]
    assert obs.java_version == 17
