-- djinni_market migration 0001: schemas, raw tables, meta tables, grants.
-- Applied by db/apply_migrations.py, which runs each file inside `SET ROLE djinni_owner`
-- (connected as the postgres superuser) so every object ends up owned by djinni_owner.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS meta;

-- ---------------------------------------------------------------- meta ----

CREATE TABLE IF NOT EXISTS meta.schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per scrape_date the loader has attempted. Safe-to-rerun bookkeeping:
-- the loader looks here first to decide which dates still need work.
CREATE TABLE IF NOT EXISTS meta.load_log (
    scrape_date     DATE PRIMARY KEY,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'skipped_bad_run', 'error')),
    rows_by_table   JSONB,
    loader_git_sha  TEXT,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    error           TEXT
);

-- --------------------------------------------------------------- raw.* ----
-- Append-only daily history, keyed by scrape_date + the scraper's natural slice keys
-- (category, exp, english_level, region, work_format — '' means "any/all" for that
-- dimension, matching djinni_market.slices.KEYS in the scraper repo).

CREATE TABLE IF NOT EXISTS raw.snapshot (
    scrape_date             DATE NOT NULL,
    category                TEXT NOT NULL,
    exp                     TEXT NOT NULL,
    english_level           TEXT NOT NULL,
    region                  TEXT NOT NULL,
    work_format             TEXT NOT NULL,
    level                   SMALLINT NOT NULL,
    candidates_online       INTEGER,
    candidates_delta_30d    INTEGER,
    cand_expect_min         DOUBLE PRECISION,
    cand_expect_max         DOUBLE PRECISION,
    offers_per_candidate    DOUBLE PRECISION,
    calculated_at           TIMESTAMPTZ,
    jobs_online             INTEGER,
    jobs_delta_30d          INTEGER,
    job_fork_min            DOUBLE PRECISION,
    job_fork_max            DOUBLE PRECISION,
    applies_per_job_online  DOUBLE PRECISION,
    djinni_index_30d        DOUBLE PRECISION,
    djinni_index_delta      DOUBLE PRECISION,
    offers_30d              INTEGER,
    applies_30d             INTEGER,
    cand_salary_p25         DOUBLE PRECISION,
    cand_salary_p75         DOUBLE PRECISION,
    vacancy_fork_30d_min    DOUBLE PRECISION,
    vacancy_fork_30d_max    DOUBLE PRECISION,
    hires_median_30d        DOUBLE PRECISION,
    jobs_with_applies_30d   INTEGER,
    jobs_with_applies_delta INTEGER,
    applies_per_job_30d     DOUBLE PRECISION,
    applies_per_job_delta   DOUBLE PRECISION,
    PRIMARY KEY (scrape_date, category, exp, english_level, region, work_format)
);

CREATE TABLE IF NOT EXISTS raw.histogram (
    scrape_date     DATE NOT NULL,
    category        TEXT NOT NULL,
    exp             TEXT NOT NULL,
    english_level   TEXT NOT NULL,
    region          TEXT NOT NULL,
    work_format     TEXT NOT NULL,
    level           SMALLINT NOT NULL,
    bin             TEXT NOT NULL,
    bin_min         INTEGER NOT NULL,
    bin_max         DOUBLE PRECISION,
    count           INTEGER,
    PRIMARY KEY (scrape_date, category, exp, english_level, region, work_format, bin_min)
);

-- Upsert-latest, not append: each day re-serves the same ~12 known months per slice.
-- scrape_date is NOT part of the key; first_seen/last_seen track the history instead.
CREATE TABLE IF NOT EXISTS raw.monthly (
    category             TEXT NOT NULL,
    exp                  TEXT NOT NULL,
    english_level        TEXT NOT NULL,
    region               TEXT NOT NULL,
    work_format          TEXT NOT NULL,
    month                DATE NOT NULL,
    level                SMALLINT NOT NULL,
    hires_median         DOUBLE PRECISION,
    vacancy_fork_low     DOUBLE PRECISION,
    vacancy_fork_high    DOUBLE PRECISION,
    salary_series_count  INTEGER,
    jobs                 INTEGER,
    applies_per_job      DOUBLE PRECISION,
    first_seen           DATE NOT NULL,
    last_seen            DATE NOT NULL,
    PRIMARY KEY (category, exp, english_level, region, work_format, month)
);

-- From the data branch's categories.csv; upsert-latest like raw.monthly.
CREATE TABLE IF NOT EXISTS raw.category (
    category      TEXT PRIMARY KEY,
    display_name  TEXT,
    first_seen    DATE NOT NULL,
    last_seen     DATE NOT NULL
);

-- From mapping/category_role_mapping.csv (main branch, not the data branch); the whole
-- file is small and is reloaded wholesale each time load_mapping() runs (full replace,
-- not append) — there is no history to keep here, only "current mapping".
CREATE TABLE IF NOT EXISTS raw.category_role_mapping (
    category      TEXT PRIMARY KEY,
    display_name  TEXT,
    on_djinni     TEXT,
    role          TEXT,
    "group"       TEXT,
    type          TEXT,
    role_hr       TEXT,
    mapped        TEXT
);

-- One-time import of legacy/salaries.parquet (old scraper history, 2025-04-28..2026-10-01).
-- Natural key assumed from the 8 compat columns; ON CONFLICT DO NOTHING makes re-import safe.
CREATE TABLE IF NOT EXISTS raw.legacy_salaries (
    category          TEXT NOT NULL,
    salary_min        INTEGER,
    salary_max        INTEGER,
    experience_label  TEXT NOT NULL,
    level             TEXT NOT NULL,
    candidates        INTEGER,
    vacancies         INTEGER,
    scrape_date       DATE NOT NULL,
    PRIMARY KEY (scrape_date, category, experience_label, level)
);

-- --------------------------------------------------------------- grants ----

GRANT USAGE ON SCHEMA raw, meta TO djinni_loader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA raw TO djinni_loader;
GRANT SELECT, INSERT, UPDATE ON meta.load_log TO djinni_loader;

GRANT USAGE ON SCHEMA mart TO djinni_ro;
-- mart tables/views get SELECT via ALTER DEFAULT PRIVILEGES set below, plus an explicit
-- grant once 0002_views.sql has created them (applier re-grants after every migration).
ALTER DEFAULT PRIVILEGES FOR ROLE djinni_owner IN SCHEMA mart GRANT SELECT ON TABLES TO djinni_ro;
