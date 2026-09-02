# Upgrade de baseline Java e modernização efetiva do código

Pipeline de mineração e análise para um estudo longitudinal sobre a relação entre o
upgrade da versão mínima do Java em projetos open source e a adoção efetiva dos recursos
modernos da linguagem.

O diferencial metodológico: o relógio de adoção de cada feature começa na **data em que o
projeto se tornou elegível** para usá-la (sua baseline passou a permitir), não na data de
lançamento global da feature.

## Reprodução

Requisitos: Python ≥ 3.12, `uv`, `git` ≥ 2.51 (para `git backfill`), rede.

```sh
uv sync --extra analysis
uv run pytest                     # 350 testes

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
