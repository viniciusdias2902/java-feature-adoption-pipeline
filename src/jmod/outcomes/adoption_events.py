"""Fase 11: desfechos de adoção por (projeto, feature, escopo).

Grão do desfecho: PROJETO (projeto elegível quando ∃ módulo elegível;
a análise agrupa por projeto). `t0` é o início do primeiro período de elegibilidade
estável entre os módulos.

Relógio com pausa: o tempo até adoção é EXPOSIÇÃO — soma dos dias elegíveis —
não tempo-calendário. Downgrades e gaps de baseline desconhecida não contam.

Definições congeladas:
* first use  — primeiro ponto da série do escopo primário com contagem > 0;
* sustained  — (iii) presença contínua nos 180 dias seguintes E ≥3 ocorrências em
  ≥2 arquivos ao fim da janela; (i) simples = presente no freeze, em coluna paralela;
* abandonment — contagem cai a 0 e permanece 0 por ≥180 dias;
* censura   — no freeze; primeiros usos a <180d do freeze ficam inclassificáveis
  para sustained (None), nunca forçados.

Anomalia explícita: uso ANTES de qualquer elegibilidade (baseline irresolvível, código
que não compilaria, vendor). Vira `censor_reason='pre_eligibility_use'` e sai do
denominador primário — contado e reportado, nunca descartado em silêncio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Period:
    start: str
    end: str | None  # None = aberto até o fim da observação


@dataclass(frozen=True)
class Point:
    sha: str
    at: str
    count: int
    files: int


@dataclass(frozen=True)
class Outcome:
    eligibility_date: str | None
    first_use_date: str | None
    first_use_sha: str | None
    time_to_first_use_days: int | None  # exposição, com pausas
    event: int
    sustained: int | None
    sustained_simple: int | None
    sustained_date: str | None
    abandoned: int | None
    abandoned_date: str | None
    censor_reason: str | None


def _dt(iso: str) -> datetime:
    parsed = datetime.fromisoformat(iso)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def exposure_days(periods: list[Period], start: str, until: str) -> int:
    """Dias elegíveis entre `start` e `until`: tempo coberto pela UNIÃO dos períodos.

    Os períodos vêm de módulos distintos e se sobrepõem (projeto elegível
    quando ∃ módulo elegível). Somar sem unir contaria o mesmo dia uma vez por
    módulo — um projeto com 23 módulos teria 23× a exposição real.
    """
    lo, hi = _dt(start), _dt(until)
    clipped = []
    for p in periods:
        a = max(lo, _dt(p.start))
        b = hi if p.end is None else min(hi, _dt(p.end))
        if b > a:
            clipped.append((a, b))
    clipped.sort()
    total = 0.0
    cur_a = cur_b = None
    for a, b in clipped:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        elif b > cur_b:
            cur_b = b
    if cur_b is not None:
        total += (cur_b - cur_a).total_seconds()
    return int(total // 86400)


def derive_outcome(
    eligibility: list[Period],
    series: list[Point],
    *,
    freeze: str,
    sustained_window_days: int,
    sustained_min_occurrences: int,
    sustained_min_files: int,
    abandonment_window_days: int,
) -> Outcome:
    periods = sorted(eligibility, key=lambda p: p.start)
    points = sorted(series, key=lambda q: (q.at, q.sha))

    first = next((q for q in points if q.count > 0), None)
    t0 = periods[0].start if periods else None

    if t0 is None:
        # nunca elegível no período observado
        if first is not None:
            return Outcome(
                None,
                first.at,
                first.sha,
                None,
                0,
                None,
                None,
                None,
                None,
                None,
                "pre_eligibility_use",
            )
        return Outcome(None, None, None, None, 0, None, None, None, None, None, "never_eligible")

    if first is not None and _dt(first.at) < _dt(t0):
        return Outcome(
            t0, first.at, first.sha, None, 0, None, None, None, None, None, "pre_eligibility_use"
        )

    if first is None:
        return Outcome(
            t0,
            None,
            None,
            exposure_days(periods, t0, freeze),
            0,
            None,
            None,
            None,
            None,
            None,
            "no_use_by_freeze",
        )

    tta = exposure_days(periods, t0, first.at)
    sustained, sustained_date, sus_reason = _sustained(
        points,
        first,
        freeze,
        sustained_window_days,
        sustained_min_occurrences,
        sustained_min_files,
    )
    simple = _sustained_simple(points)
    abandoned, abandoned_at = _abandoned(points, first, freeze, abandonment_window_days)

    return Outcome(
        t0,
        first.at,
        first.sha,
        tta,
        1,
        sustained,
        simple,
        sustained_date,
        abandoned,
        abandoned_at,
        sus_reason,
    )


def _sustained(
    points: list[Point],
    first: Point,
    freeze: str,
    window_days: int,
    min_occ: int,
    min_files: int,
) -> tuple[int | None, str | None, str | None]:
    """(sustained, data, motivo_de_censura). None = inclassificável dentro da janela."""
    window_end = _dt(first.at) + timedelta(days=window_days)
    if window_end > _dt(freeze):
        return None, None, "sustained_window_open"  # censura, nunca forçado

    at_end: Point = first
    for q in points:
        when = _dt(q.at)
        if when <= _dt(first.at):
            continue
        if when <= window_end:
            if q.count == 0:
                return 0, None, None  # sumiu dentro da janela
            at_end = q
        else:
            break
    if at_end.count >= min_occ and at_end.files >= min_files:
        return 1, window_end.isoformat(), None
    return 0, None, None


def _sustained_simple(points: list[Point]) -> int:
    """Definição (i): presente no último ponto observado. Sensibilidade."""
    return int(bool(points) and points[-1].count > 0)


def _abandoned(
    points: list[Point], first: Point, freeze: str, window_days: int
) -> tuple[int | None, str | None]:
    """Abandono: contagem chega a 0 depois do primeiro uso e nunca mais sobe,
    com >= window_days de silêncio até o freeze. Janela aberta => inclassificável."""
    last_zero: Point | None = None
    for q in points:
        if _dt(q.at) <= _dt(first.at):
            continue
        last_zero = q if q.count == 0 else None
    if last_zero is None:
        return 0, None
    quiet_days = (_dt(freeze) - _dt(last_zero.at)).total_seconds() / 86400
    if quiet_days >= window_days:
        return 1, last_zero.at
    return None, None  # zerou há pouco: censura
