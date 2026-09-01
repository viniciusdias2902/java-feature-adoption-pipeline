"""Cadeia TLS do SEART.

O servidor do SEART GHS envia apenas o certificado folha, sem o intermediário que o
assina. Navegadores contornam isso buscando o intermediário no endereço publicado na
extensão AIA do próprio certificado; `curl`, `httpx` e `requests` não fazem isso, e a
conexão falha com `unable to get local issuer certificate`.

A correção é buscar esse intermediário e anexá-lo ao trust store do sistema. A
verificação continua **inteiramente ativa** — nada é desabilitado, apenas se fornece o
elo que o servidor omite.
"""

from __future__ import annotations

import ssl
import urllib.request
from pathlib import Path

# Endereço publicado na extensão Authority Information Access do certificado do SEART.
SEART_INTERMEDIATE_URL = "http://yr1.i.lencr.org/"


class TrustChainError(RuntimeError):
    pass


def build_bundle(cache_dir: Path, url: str = SEART_INTERMEDIATE_URL) -> Path:
    """Devolve um bundle PEM = CAs do sistema + intermediário faltante.

    Idempotente: reaproveita o bundle já em cache.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    bundle = cache_dir / "seart-ca-bundle.pem"
    if bundle.exists() and bundle.stat().st_size > 0:
        return bundle

    system = ssl.get_default_verify_paths().cafile
    if not system or not Path(system).exists():
        raise TrustChainError("trust store do sistema não localizado")

    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            der = resp.read()
    except OSError as exc:
        raise TrustChainError(f"não foi possível obter o intermediário em {url}: {exc}") from exc

    try:
        pem = ssl.DER_cert_to_PEM_cert(der)
    except ValueError as exc:
        raise TrustChainError(f"intermediário em {url} não é um DER válido") from exc

    bundle.write_text(Path(system).read_text(encoding="utf-8") + "\n" + pem, encoding="utf-8")
    return bundle
