"""Fase 7: intervalos de baseline e eventos de versão.

Duas regras metodológicas governam este módulo:

* **Carry-forward só sobre o conhecido**. A baseline observada num commit
  vale até o próximo commit que toca arquivo de build. Se uma observação vem `None`
  (parent externo, Gradle irresolvível), abre-se um intervalo explícito de
  desconhecimento — e uma versão igual dos dois lados do gap NÃO é emendada: são dois
  intervalos, com o buraco registrado entre eles.

* **Persistência mínima de 30 dias**. Uma transição X→Y só é upgrade ou
  downgrade se Y persistir 30 dias ou até o fim da observação; revertida antes disso, é
  `transient` e não gera elegibilidade. Transições encadeadas dentro da janela
  (8→17→21 em dias) colapsam: o evento real é do estável antigo para o novo estável.

Transição através de gap desconhecido (8 → ? → 17) é emitida no primeiro commit que
OBSERVA a nova versão — a data real da mudança está em algum ponto do gap. Isso adia a
elegibilidade para a última data defensável, um viés conservador declarado.
O gap em si fica persistido nos intervalos, então esses eventos são identificáveis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Sample:
    """Baseline observada num commit que toca arquivo de build."""

    sha: str
    committed_at: str  # ISO-8601 com fuso
    java_version: int | None  # None = desconhecida naquele commit


@dataclass(frozen=True)
class Interval:
    start_sha: str
    start_date: str
    end_sha: str | None  # None = vigente no fim da observação
    end_date: str | None
    java_version: int | None


@dataclass(frozen=True)
class VersionEvent:
    sha: str
    committed_at: str
    from_version: int | None
    to_version: int
    kind: str  # upgrade | downgrade | transient
    persisted_days: int


def _days_between(start: str, end: str) -> int:
    a = datetime.fromisoformat(start)
    b = datetime.fromisoformat(end)
    return max(0, int((b - a).total_seconds() // 86400))


def build_intervals(samples: list[Sample]) -> list[Interval]:
    """Colapsa observações consecutivas iguais; gaps de `None` viram intervalos próprios."""
    ordered = sorted(samples, key=lambda s: (s.committed_at, s.sha))
    intervals: list[Interval] = []
    for sample in ordered:
        if intervals and intervals[-1].java_version == sample.java_version:
            continue
        if intervals:
            previous = intervals[-1]
            intervals[-1] = Interval(
                previous.start_sha,
                previous.start_date,
                sample.sha,
                sample.committed_at,
                previous.java_version,
            )
        intervals.append(Interval(sample.sha, sample.committed_at, None, None, sample.java_version))
    return intervals


def derive_events(
    intervals: list[Interval], *, persistence_days: int, end_of_observation: str
) -> list[VersionEvent]:
    """Eventos de versão a partir dos intervalos.

    Um intervalo conhecido é *estável* se durar >= persistence_days ou se for o último
    (persiste "até o HEAD"). Eventos ligam estáveis consecutivos; intervalos curtos
    no meio viram `transient`.
    """
    events: list[VersionEvent] = []
    last_stable: int | None = None

    known = [i for i in intervals if i.java_version is not None]
    for interval in known:
        end = interval.end_date or end_of_observation
        duration = _days_between(interval.start_date, end)
        is_last = interval.end_date is None
        stable = is_last or duration >= persistence_days
        version = interval.java_version
        assert version is not None

        if last_stable is None:
            # A persistência qualifica a versão NOVA de uma transição, não o
            # ponto de partida: o primeiro estado conhecido é referência, dure o que
            # durar. Sem isso, um histórico que começa às vésperas do primeiro upgrade
            # perderia o evento.
            last_stable = version
            continue

        if not stable:
            if version != last_stable:
                events.append(
                    VersionEvent(
                        interval.start_sha,
                        interval.start_date,
                        last_stable,
                        version,
                        "transient",
                        duration,
                    )
                )
            continue

        if version != last_stable:
            kind = "upgrade" if version > last_stable else "downgrade"
            events.append(
                VersionEvent(
                    interval.start_sha,
                    interval.start_date,
                    last_stable,
                    version,
                    kind,
                    duration,
                )
            )
        last_stable = version
    return events
