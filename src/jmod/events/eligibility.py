"""Fase 8: elegibilidade por (módulo, feature) ao longo do tempo.

A elegibilidade estável segue a **linha do tempo efetiva**, não a baseline crua:

* blips `transient` não contam — nem para criar elegibilidade (8 → 17 por dez
  dias → 8 não torna o módulo elegível a records) nem para interrompê-la (17 → 8 por
  dez dias → 17 não pausa a elegibilidade existente);
* um `downgrade` real fecha o período; um upgrade posterior abre outro — o relógio
  pausa e retoma;
* um gap de baseline desconhecida **sempre** interrompe: manter elegibilidade através
  do desconhecido seria interpolar. Depois do gap, o próximo estado conhecido
  vale imediatamente, curto ou não — reset conservador, sem ponte com o passado.

A elegibilidade preview é outra coisa: segue os intervalos CRUS, exigindo a versão
em janela de preview E `--enable-preview` ligado naquele momento. Não há semântica de
persistência — preview é desfecho secundário sobre configuração literal.

`start_date` é o committer date do commit de transição, inclusive.
"""

from __future__ import annotations

from dataclasses import dataclass

from jmod.config import Feature
from jmod.events.upgrades import Interval, _days_between


@dataclass(frozen=True)
class EligibilityPeriod:
    feature_id: str
    kind: str  # stable | preview
    start_sha: str
    start_date: str
    end_date: str | None  # None = vigente no fim da observação


@dataclass(frozen=True)
class _State:
    sha: str
    date: str
    version: int | None


def effective_states(
    intervals: list[Interval], *, persistence_days: int, end_of_observation: str
) -> list[_State]:
    """Linha do tempo efetiva: transients absorvidos, gaps preservados, reset após gap."""
    states: list[_State] = []
    current: int | None = None
    after_reset = True

    for interval in intervals:
        version = interval.java_version
        if version is None:
            if not after_reset or not states:
                states.append(_State(interval.start_sha, interval.start_date, None))
            current = None
            after_reset = True
            continue

        end = interval.end_date or end_of_observation
        stable = interval.end_date is None or _days_between(interval.start_date, end) >= (
            persistence_days
        )
        if after_reset or stable:
            if version != current or after_reset:
                states.append(_State(interval.start_sha, interval.start_date, version))
            current = version
            after_reset = False
        # transient: o estado corrente segue valendo através do blip

    return states


def stable_eligibility(
    intervals: list[Interval],
    feature: Feature,
    *,
    persistence_days: int,
    end_of_observation: str,
) -> list[EligibilityPeriod]:
    """Períodos em que o módulo pôde usar a feature estável."""
    periods: list[EligibilityPeriod] = []
    open_period: tuple[str, str] | None = None

    for state in effective_states(
        intervals, persistence_days=persistence_days, end_of_observation=end_of_observation
    ):
        eligible = state.version is not None and state.version >= feature.stable
        if eligible and open_period is None:
            open_period = (state.sha, state.date)
        elif not eligible and open_period is not None:
            periods.append(
                EligibilityPeriod(feature.id, "stable", open_period[0], open_period[1], state.date)
            )
            open_period = None

    if open_period is not None:
        periods.append(
            EligibilityPeriod(feature.id, "stable", open_period[0], open_period[1], None)
        )
    return periods


def preview_eligibility(intervals: list[Interval], feature: Feature) -> list[EligibilityPeriod]:
    """Períodos com a versão em janela de preview E `--enable-preview` ligado."""
    if not feature.preview:
        return []
    periods: list[EligibilityPeriod] = []
    open_period: tuple[str, str] | None = None
    last_end: str | None = None

    for interval in intervals:
        active = (
            interval.java_version is not None
            and interval.java_version in feature.preview
            and interval.preview
        )
        if active and open_period is None:
            open_period = (interval.start_sha, interval.start_date)
        elif not active and open_period is not None:
            periods.append(
                EligibilityPeriod(
                    feature.id, "preview", open_period[0], open_period[1], interval.start_date
                )
            )
            open_period = None
        last_end = interval.end_date

    if open_period is not None:
        periods.append(
            EligibilityPeriod(feature.id, "preview", open_period[0], open_period[1], last_end)
        )
    return periods


def module_eligibility(
    intervals: list[Interval],
    features: tuple[Feature, ...],
    *,
    persistence_days: int,
    end_of_observation: str,
) -> list[EligibilityPeriod]:
    """Todos os períodos de elegibilidade de um módulo, estáveis e preview."""
    out: list[EligibilityPeriod] = []
    for feature in features:
        out.extend(
            stable_eligibility(
                intervals,
                feature,
                persistence_days=persistence_days,
                end_of_observation=end_of_observation,
            )
        )
        out.extend(preview_eligibility(intervals, feature))
    return out
