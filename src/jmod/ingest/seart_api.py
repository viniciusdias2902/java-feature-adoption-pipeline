"""Cliente da API REST do SEART GHS.

Preferível ao export CSV: é reprodutível, não exige passo manual, e traz campos que o
export não tem — `codeLines`, `lastCommit`, `lastCommitSHA` e `contributors`.

ARMADILHA que motiva o desenho abaixo: **a API ignora silenciosamente parâmetros que não
reconhece**. `createdBefore=2018-03-20` retorna o mesmo total que a consulta sem filtro
algum, sem erro. Por isso:

1. `verify_filters()` confirma que cada filtro do servidor de fato reduz o total;
2. todo critério de seleção é **reaplicado no cliente** por `seart.screen()`, de modo que um
   filtro silenciosamente ignorado vire exclusão registrada, nunca dado sujo.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from jmod.config import Study
from jmod.ingest.seart import SeartRow

BASE_URL = "https://seart-ghs.si.usi.ch/api/r/search"
PAGE_SIZE = 100


class SeartApiError(RuntimeError):
    pass


def query_params(study: Study) -> dict[str, Any]:
    """Filtros de seleção traduzidos para nomes que o servidor reconhece."""
    s = study.sampling
    return {
        "language": "Java",
        "starsMin": s["min_stars"],
        "commitsMin": s["min_commits"],
        "codeLinesMin": s["min_loc_java"],
        "createdMax": str(s["created_before"]),  # NÃO é createdAtMax nem createdBefore
        "committedMin": str(s["active_since"]),
        "excludeForks": "true" if s["exclude_forks"] else "false",
    }


class SeartClient:
    def __init__(self, verify: Path | str | bool = True, timeout: float = 60.0) -> None:
        self._client = httpx.Client(
            verify=str(verify) if isinstance(verify, Path) else verify, timeout=timeout
        )

    def _get(self, params: dict[str, Any], attempts: int = 5) -> dict[str, Any]:
        """O servidor do SEART tem picos de latência: paginação longa precisa de retry."""
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = self._client.get(BASE_URL, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                time.sleep(min(30, 2**attempt))
                continue
            if resp.status_code in (429, 502, 503, 504):
                last = SeartApiError(f"HTTP {resp.status_code}")
                time.sleep(min(30, 2**attempt))
                continue
            if resp.status_code != 200:
                raise SeartApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            data: dict[str, Any] = resp.json()
            return data
        raise SeartApiError(f"esgotadas {attempts} tentativas: {last}")

    def total(self, params: dict[str, Any]) -> int:
        return int(self._get({**params, "size": 1})["totalItems"])

    def verify_filters(self, params: dict[str, Any]) -> list[str]:
        """Nomes de filtro que o servidor ignorou. Vazio = todos surtiram efeito.

        Um filtro que não altera o total, aplicado sobre a consulta completa menos ele
        mesmo, ou é redundante ou foi ignorado — em ambos os casos o cliente precisa
        saber, porque a diferença decide se `screen()` é a única defesa.
        """
        full = self.total(params)
        ignored = []
        for key in params:
            if key == "language":
                continue
            reduced = {k: v for k, v in params.items() if k != key}
            if self.total(reduced) == full:
                ignored.append(key)
        return ignored

    def iter_items(self, params: dict[str, Any], limit: int = 0) -> Iterator[dict[str, Any]]:
        page, seen = 1, 0
        while True:
            data = self._get({**params, "page": page, "size": PAGE_SIZE})
            items = data.get("items", [])
            if not items:
                return
            for item in items:
                if limit and seen >= limit:
                    return
                seen += 1
                yield item
            if page >= int(data.get("totalPages", 0)):
                return
            page += 1

    def close(self) -> None:
        self._client.close()


def to_row(item: dict[str, Any]) -> SeartRow:
    """Converte um item da API na mesma estrutura lida do CSV, para reusar `screen()`."""
    return SeartRow(
        full_name=str(item.get("name") or "").strip(),
        stars=int(item.get("stargazers") or 0),
        commits=int(item.get("commits") or 0),
        loc_java=int(item["codeLines"]) if item.get("codeLines") is not None else None,
        created_at=item.get("createdAt"),
        last_commit_at=item.get("lastCommit") or item.get("pushedAt"),
        default_branch=item.get("defaultBranch"),
        is_fork=bool(item.get("isFork")),
        is_archived=bool(item.get("isArchived")) or bool(item.get("isDisabled")),
        main_language=item.get("mainLanguage"),
    )
