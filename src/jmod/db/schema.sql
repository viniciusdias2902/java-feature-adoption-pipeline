-- Esquema canônico. Toda tabela carrega proveniência (pipeline_version, collected_at).
-- Chaves naturais em PRIMARY KEY: reexecutar uma fase faz UPSERT, nunca duplica.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------- proveniência
CREATE TABLE IF NOT EXISTS run_log (
    run_id           TEXT NOT NULL,
    phase            TEXT NOT NULL,
    project_id       TEXT NOT NULL DEFAULT '',   -- '' = execução global
    status           TEXT NOT NULL CHECK (status IN ('started','ok','failed','skipped')),
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    error            TEXT,
    pipeline_version TEXT NOT NULL,
    PRIMARY KEY (run_id, phase, project_id)
);
CREATE INDEX IF NOT EXISTS ix_run_log_phase ON run_log(phase, status);

-- Nunca descarte silencioso: todo projeto está em projects OU em exclusions.
CREATE TABLE IF NOT EXISTS exclusions (
    project_id       TEXT NOT NULL,
    phase            TEXT NOT NULL,
    reason           TEXT NOT NULL,
    detail           TEXT,
    pipeline_version TEXT NOT NULL,
    collected_at     TEXT NOT NULL,
    PRIMARY KEY (project_id, phase, reason)
);
CREATE INDEX IF NOT EXISTS ix_exclusions_reason ON exclusions(reason);

-- ---------------------------------------------------------------- população
CREATE TABLE IF NOT EXISTS projects (
    project_id            TEXT PRIMARY KEY,      -- owner/name, minúsculo
    full_name             TEXT NOT NULL,
    clone_url             TEXT NOT NULL,
    default_branch        TEXT,
    frame_source          TEXT NOT NULL,         -- 'seart' | 'github_search'
    seart_stars           INTEGER,
    seart_commits         INTEGER,
    seart_loc_java        INTEGER,
    seart_created_at      TEXT,
    seart_last_commit_at  TEXT,
    gh_archived           INTEGER,
    gh_disabled           INTEGER,
    gh_fork               INTEGER,
    gh_pushed_at          INTEGER,
    gh_size_kb            INTEGER,
    gh_validated_at       TEXT,
    pipeline_version      TEXT NOT NULL,
    collected_at          TEXT NOT NULL
);

-- Shallow probe do HEAD: atributos de estratificação da Fase P.
CREATE TABLE IF NOT EXISTS probe (
    project_id           TEXT PRIMARY KEY REFERENCES projects(project_id),
    head_sha             TEXT,
    build_system         TEXT,      -- maven | gradle_groovy | gradle_kts | mixed | none
    module_count         INTEGER,
    has_external_parent  INTEGER,
    head_baseline        INTEGER,
    head_baseline_source TEXT,
    is_android           INTEGER,
    pipeline_version     TEXT NOT NULL,
    collected_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pilot_sample (
    project_id       TEXT PRIMARY KEY REFERENCES projects(project_id),
    stratum          TEXT NOT NULL,
    draw_rank        INTEGER NOT NULL,
    seed             INTEGER NOT NULL,
    pipeline_version TEXT NOT NULL,
    collected_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------- baseline
CREATE TABLE IF NOT EXISTS build_file_changes (
    project_id  TEXT NOT NULL,
    sha         TEXT NOT NULL,
    path        TEXT NOT NULL,
    change_type TEXT NOT NULL,           -- A | M | D | R
    old_path    TEXT,
    committed_at TEXT NOT NULL,
    PRIMARY KEY (project_id, sha, path)
);

CREATE TABLE IF NOT EXISTS baseline_observations (
    project_id   TEXT NOT NULL,
    sha          TEXT NOT NULL,
    module_path  TEXT NOT NULL,
    java_version INTEGER,                -- NULL = desconhecida
    source       TEXT NOT NULL,          -- maven_release | gradle_toolchain | ...
    evidence_level INTEGER NOT NULL,     -- 1..7, menor é evidência mais forte
    confidence   TEXT NOT NULL CHECK (confidence IN ('strong','medium','weak','corroborating')),
    conflict     INTEGER NOT NULL DEFAULT 0,
    resolution   TEXT,                   -- resolved | unresolved_parent | unparseable
    raw_value    TEXT,
    committed_at TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (project_id, sha, module_path, source)
);

CREATE TABLE IF NOT EXISTS baseline_intervals (
    project_id   TEXT NOT NULL,
    module_path  TEXT NOT NULL,
    start_sha    TEXT NOT NULL,
    start_date   TEXT NOT NULL,
    end_sha      TEXT,
    end_date     TEXT,
    java_version INTEGER,                -- NULL = gap explícito de desconhecimento
    PRIMARY KEY (project_id, module_path, start_sha)
);

CREATE TABLE IF NOT EXISTS version_events (
    project_id    TEXT NOT NULL,
    module_path   TEXT NOT NULL,
    sha           TEXT NOT NULL,
    committed_at  TEXT NOT NULL,
    from_version  INTEGER,
    to_version    INTEGER NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('upgrade','downgrade','transient')),
    persisted_days INTEGER,
    PRIMARY KEY (project_id, module_path, sha)
);

CREATE TABLE IF NOT EXISTS eligibility (
    project_id   TEXT NOT NULL,
    module_path  TEXT NOT NULL,
    feature_id   TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('stable','preview')),
    start_date   TEXT NOT NULL,
    start_sha    TEXT NOT NULL,
    end_date     TEXT,                   -- NULL = ainda elegível no freeze
    PRIMARY KEY (project_id, module_path, feature_id, kind, start_date)
);

-- ---------------------------------------------------------------- features
CREATE TABLE IF NOT EXISTS feature_candidates (
    project_id  TEXT NOT NULL,
    feature_id  TEXT NOT NULL,
    sha         TEXT NOT NULL,
    path        TEXT NOT NULL,
    strategy    TEXT NOT NULL,           -- pickaxe_S | pickaxe_G | head_scan
    committed_at TEXT NOT NULL,
    PRIMARY KEY (project_id, feature_id, sha, path)
);

CREATE TABLE IF NOT EXISTS feature_occurrences (
    project_id   TEXT NOT NULL,
    feature_id   TEXT NOT NULL,
    sha          TEXT NOT NULL,
    path         TEXT NOT NULL,
    module_path  TEXT,
    node_subtype TEXT,                   -- ex.: arrow_statement | expression_position
    line         INTEGER NOT NULL,
    byte_start   INTEGER NOT NULL,
    scope        TEXT NOT NULL,          -- production | test | example | generated
    committed_at TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    PRIMARY KEY (project_id, feature_id, sha, path, byte_start)
);

CREATE TABLE IF NOT EXISTS feature_series (
    project_id       TEXT NOT NULL,
    feature_id       TEXT NOT NULL,
    scope            TEXT NOT NULL,
    sha              TEXT NOT NULL,
    committed_at     TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL,
    file_count       INTEGER NOT NULL,
    PRIMARY KEY (project_id, feature_id, scope, sha)
);

CREATE TABLE IF NOT EXISTS opportunity_series (
    project_id   TEXT NOT NULL,
    proxy_id     TEXT NOT NULL,
    scope        TEXT NOT NULL,
    sha          TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    count        INTEGER NOT NULL,
    PRIMARY KEY (project_id, proxy_id, scope, sha)
);

-- ---------------------------------------------------------------- desfechos
CREATE TABLE IF NOT EXISTS adoption_outcomes (
    project_id        TEXT NOT NULL,
    module_path       TEXT NOT NULL,
    feature_id        TEXT NOT NULL,
    scope             TEXT NOT NULL,
    eligibility_date  TEXT NOT NULL,
    first_use_date    TEXT,
    first_use_sha     TEXT,
    time_to_first_use_days INTEGER,
    event             INTEGER NOT NULL,      -- 1 = adotou, 0 = censurado
    sustained         INTEGER,               -- definição composta (C2-iii)
    sustained_simple  INTEGER,               -- definição (C2-i), sensibilidade
    sustained_date    TEXT,
    abandoned         INTEGER,
    abandoned_date    TEXT,
    censor_reason     TEXT,
    PRIMARY KEY (project_id, module_path, feature_id, scope)
);

CREATE TABLE IF NOT EXISTS survival_rows (
    row_id       TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    feature_id   TEXT NOT NULL,
    scope        TEXT NOT NULL,
    entry_days   REAL NOT NULL,      -- truncamento à esquerda
    duration_days REAL NOT NULL,
    event        INTEGER NOT NULL,
    covariates_json TEXT NOT NULL,
    pipeline_version TEXT NOT NULL
);

-- Invariante de recall (PLAN.md, global 4): divergência entre a série derivada do
-- pickaxe e uma varredura completa do HEAD é sempre um defeito, nunca um dado.
CREATE TABLE IF NOT EXISTS recall_checks (
    project_id    TEXT NOT NULL,
    feature_id    TEXT NOT NULL,
    scope         TEXT NOT NULL,
    series_count  INTEGER NOT NULL,
    headscan_count INTEGER NOT NULL,
    ok            INTEGER NOT NULL,
    checked_at    TEXT NOT NULL,
    PRIMARY KEY (project_id, feature_id, scope)
);
