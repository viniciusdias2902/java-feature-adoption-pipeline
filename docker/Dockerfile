# Imagem única para a pipeline e a análise.
#
# O host só precisa de Docker: nem Python, nem uv. (A pipeline não usa JDK: a
# sintaxe Java é lida por tree-sitter e a baseline sai dos arquivos de build.)
#
# Binário externo de que o código depende: git, para o histórico dos repositórios
# minerados (jmod/repo/git.py).

ARG UV_VERSION=0.13.5
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uvbin

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git ca-certificates curl sqlite3 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uvbin /uv /usr/local/bin/uv

# Data fixa (o freeze do estudo) para que o matplotlib não grave o instante da
# execução nas figuras: reexecutar sem mudar nada produz bytes idênticos, e o git
# deixa de acusar diferença a cada run.
ENV SOURCE_DATE_EPOCH=1788220800 \
    TZ=UTC

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_SYNC=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/work/src \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    UV_CACHE_DIR=/opt/home/uv-cache \
    MPLCONFIGDIR=/opt/home/mpl \
    HOME=/opt/home

WORKDIR /work

# Dependências antes do código: a camada só reconstrói quando o lock muda.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --extra analysis --group dev

# O repositório entra por bind mount, com o uid do host; tudo que o container
# escreve (bytecode, matplotlib) tem de aceitar outro uid.
RUN rm -rf /opt/home \
 && mkdir -p /opt/home/mpl /opt/home/uv-cache \
 && chmod -R a+rwX /opt/venv /opt/home \
 && git config --system --add safe.directory /work \
 && printf '#!/bin/sh\nexec python -m jmod.cli "$@"\n' > /usr/local/bin/jmod \
 && chmod +x /usr/local/bin/jmod

CMD ["bash"]
