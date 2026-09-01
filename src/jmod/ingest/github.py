"""Cliente da API do GitHub: validação dos repositórios e quadro amostral de contingência.

Sem `GITHUB_TOKEN` o limite cai de 5.000 para 60 requisições por hora — suficiente para
o piloto, inviável para a população. O cliente avisa e continua, em vez de falhar.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

API = "https://api.github.com"


@dataclass(frozen=True)
class RepoInfo:
    full_name: str
    default_branch: str
    archived: bool
    disabled: bool
    fork: bool
    pushed_at: str | None
    size_kb: int
    language: str | None
    clone_url: str


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, timeout: float = 20.0) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or None
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "jmod-study"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.Client(headers=headers, timeout=timeout, follow_redirects=True)

    @property
    def authenticated(self) -> bool:
        return self.token is not None

    def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        for attempt in range(4):
            resp = self._client.get(url, params=params)
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                reset = int(resp.headers.get("x-ratelimit-reset", "0"))
                wait = max(1, min(90, reset - int(time.time())))
                time.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < 3:
                time.sleep(2**attempt)
                continue
            return resp
        return resp

    def rate_limit(self) -> tuple[int, int]:
        data = self._get(f"{API}/rate_limit").json()["resources"]["core"]
        return int(data["remaining"]), int(data["limit"])

    def repo(self, full_name: str) -> RepoInfo | None:
        """None quando o repositório sumiu (404/451). Some != erro."""
        resp = self._get(f"{API}/repos/{full_name}")
        if resp.status_code in (404, 451):
            return None
        if resp.status_code != 200:
            raise GitHubError(f"{full_name}: HTTP {resp.status_code}")
        d = resp.json()
        return RepoInfo(
            full_name=d["full_name"],
            default_branch=d.get("default_branch") or "master",
            archived=bool(d.get("archived")),
            disabled=bool(d.get("disabled")),
            fork=bool(d.get("fork")),
            pushed_at=d.get("pushed_at"),
            size_kb=int(d.get("size") or 0),
            language=d.get("language"),
            clone_url=d.get("clone_url") or f"https://github.com/{full_name}.git",
        )

    def search_repositories(self, query: str, limit: int) -> Iterator[dict[str, Any]]:
        """Quadro de contingência. A Search API corta em 1.000 resultados por consulta."""
        page, seen = 1, 0
        while seen < limit and page <= 10:
            resp = self._get(
                f"{API}/search/repositories",
                {"q": query, "sort": "stars", "order": "desc", "per_page": 100, "page": page},
            )
            if resp.status_code != 200:
                raise GitHubError(f"search: HTTP {resp.status_code} {resp.text[:200]}")
            items = resp.json().get("items", [])
            if not items:
                return
            for item in items:
                if seen >= limit:
                    return
                seen += 1
                yield item
            page += 1

    def close(self) -> None:
        self._client.close()
