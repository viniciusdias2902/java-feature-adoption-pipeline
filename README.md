# Upgrade de baseline Java e modernização efetiva do código

Pipeline de mineração e análise para um estudo longitudinal sobre a relação entre o
upgrade da versão mínima do Java em projetos open source e a adoção efetiva dos recursos
modernos da linguagem.

O diferencial metodológico: o relógio de adoção de cada feature começa na **data em que o
projeto se tornou elegível** para usá-la (sua baseline passou a permitir), não na data de
lançamento global da feature.

## Licença

MIT, em `LICENSE`. Os artefatos de `data/` (CSV, parquet, dossiês) acompanham o mesmo
termo.

## Reprodução com Docker

Requisito no host: apenas Docker. A imagem leva Python 3.12, `uv`, `git` e as
dependências de análise. **Não é preciso JDK**: a pipeline lê sintaxe Java com tree-sitter
e extrai a baseline dos arquivos de build, sem compilar nada.

```sh
docker compose build                    # ~3 min na primeira vez
docker compose run --rm teste           # 403 testes
docker compose run --rm analise         # jmod dataset + jmod analyze
docker compose run --rm lint            # ruff + mypy
docker compose run --rm shell           # shell interativo em /work
```

O repositório entra no container por bind mount em `/work`, com o uid do host, então os
artefatos de `data/` aparecem direto na árvore de trabalho. O banco
`data/db/study.sqlite` **não** está no git (1,4 GB) e precisa ser reconstruído pelas
fases de mineração antes de rodar `analise`.

As fases de mineração precisam de rede e de token:

```sh
GITHUB_TOKEN=ghp_... docker compose run --rm shell jmod ingest --from-seart-api
```

`SOURCE_DATE_EPOCH` está fixado na data de congelamento do estudo, então reexecutar a
análise sem mudar nada produz figuras byte a byte idênticas.

## Reprodução sem Docker

Requisitos: Python ≥ 3.12, `uv`, `git` ≥ 2.51 (para `git backfill`), rede.

```sh
uv sync --extra analysis
uv run pytest                     # 403 testes

uv run jmod init                  # cria data/db/study.sqlite
uv run jmod ingest --from-seart-api   # quadro amostral (SEART GHS, filtros de seleção)
uv run jmod probe                 # atributos de estratificação (HEAD raso)
uv run jmod sample-pilot          # sorteio determinístico do piloto (seed 20260901)
uv run jmod mine                  # baseline + elegibilidade + adoção (horas; retomável)
uv run jmod outcomes              # first use / sustained / abandonment
uv run jmod opportunity           # denominadores de oportunidade no HEAD
uv run jmod dataset               # data/dataset/survival.{parquet,csv}
uv run jmod verify                # verificação independente (piloto + 20 cegos)
uv run jmod analyze               # Kaplan-Meier, Cox, tabelas e figuras
```

Todas as etapas são idempotentes e retomáveis: reexecutar pula o que já foi persistido;
interrupções (rede, energia) não custam retrabalho. Nenhum projeto é descartado sem
motivo registrado (`exclusions`).

## Saídas

- `data/db/study.sqlite` — todas as observações, com proveniência (SHA, fonte, confiança)
- `data/dataset/survival.parquet` — uma linha por (projeto, feature) elegível
- `data/analysis/` — figuras e tabelas da análise
- `data/validation/dossiers.json` — verificação independente, projeto a projeto

## Desenho em uma frase por camada

Quadro amostral do SEART GHS reconferido no cliente; clones bare blobless com
`git backfill --sparse`; baseline por módulo com hierarquia de evidência e parents
internos resolvidos; eventos de versão com janela de persistência de 30 dias; candidatos
por pickaxe (`-S`/`-G` POSIX ERE) confirmados por AST tree-sitter; invariante de recall
contra varredura completa do HEAD; desfechos com censura explícita; validação por
releitura do grafo completo com candidatos ingênuos; sobrevivência com truncamento à
esquerda e variância agrupada por projeto.
