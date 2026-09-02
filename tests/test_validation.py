"""Fase 14: o verificador independente contra repositórios sintéticos com verdade conhecida."""

from __future__ import annotations

from jmod.config import load_study
from jmod.features.parser import ScopeRules
from jmod.repo.git import Repo
from jmod.validation.independent import (
    check_first_use,
    check_version_event,
    independent_first_use,
)

STUDY = load_study()
RULES = ScopeRules.from_config(STUDY.paths)
RECORD = STUDY.feature("record")


def test_encontra_o_primeiro_uso_no_grafo_completo(synthetic) -> None:
    r = synthetic()
    r.commit({"src/main/java/A.java": "class A {}"})
    alvo = r.commit({"src/main/java/A.java": "class A { record P(int x) {} }"})
    found = independent_first_use(Repo(r.path), "main", RECORD, RULES)
    assert found is not None
    when, sha, path = found
    assert sha == alvo
    assert path == "src/main/java/A.java"


def test_uso_so_em_teste_nao_conta_como_producao(synthetic) -> None:
    r = synthetic()
    r.commit({"src/test/java/T.java": "class T { record F(int a) {} }"})
    assert independent_first_use(Repo(r.path), "main", RECORD, RULES) is None


def test_concordancia_gera_lag_zero(synthetic) -> None:
    r = synthetic()
    sha = r.commit({"src/main/java/A.java": "class A { record P(int x) {} }"})
    repo = Repo(r.path)
    when = repo.run("log", "-1", "--format=%cI", sha).strip()
    chk = check_first_use(repo, "main", RECORD, RULES, when, sha, "src/main/java/A.java")
    assert chk.verdict == "both_present"
    assert chk.lag_days == 0.0


def test_pipeline_missed_quando_ha_uso_e_a_pipeline_nao_viu(synthetic) -> None:
    r = synthetic()
    r.commit({"src/main/java/A.java": "class A { record P(int x) {} }"})
    chk = check_first_use(Repo(r.path), "main", RECORD, RULES, None, None, None)
    assert chk.verdict == "pipeline_missed"


def test_falso_positivo_quando_a_evidencia_nao_reconfirma(synthetic) -> None:
    r = synthetic()
    sha = r.commit({"src/main/java/A.java": "class A { int record = 1; }"})
    chk = check_first_use(
        Repo(r.path),
        "main",
        RECORD,
        RULES,
        "2020-01-01T12:00:00+00:00",
        sha,
        "src/main/java/A.java",
    )
    assert chk.verdict == "pipeline_false_positive"


def test_ambos_ausentes(synthetic) -> None:
    r = synthetic()
    r.commit({"src/main/java/A.java": "class A {}"})
    chk = check_first_use(Repo(r.path), "main", RECORD, RULES, None, None, None)
    assert chk.verdict == "both_absent"


def test_evento_de_versao_confirmado_textualmente(synthetic) -> None:
    pom8 = (
        "<project><properties>\n<maven.compiler.release>8</maven.compiler.release>\n"
        "</properties></project>"
    )
    pom17 = pom8.replace(">8<", ">17<")
    r = synthetic()
    r.commit({"pom.xml": pom8})
    sha = r.commit({"pom.xml": pom17})
    chk = check_version_event(Repo(r.path), sha, 17, "upgrade")
    assert chk.confirmed is True
    assert "17" in chk.evidence


def test_evento_nao_confirmado_quando_o_texto_nao_muda(synthetic) -> None:
    pom = (
        "<project><properties>\n<maven.compiler.release>8</maven.compiler.release>\n"
        "</properties></project>"
    )
    r = synthetic()
    r.commit({"pom.xml": pom})
    sha = r.commit({"pom.xml": pom + "<!-- só comentário -->", "README.md": "x"})
    chk = check_version_event(Repo(r.path), sha, 17, "upgrade")
    assert chk.confirmed is False
