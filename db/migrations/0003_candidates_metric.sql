-- djinni_market migration 0003: candidates_metric.
--
-- Djinni redefined the big number on #candidates_card between the 2026-10-03 and 2026-10-05
-- runs: "Кандидатів онлайн" ("online") became "Активні кандидати" ("active in the last 4
-- weeks", roughly 0.4x the old number, no 30d-delta badge). The two series are NOT
-- comparable. The scraper already records which one a row holds
-- (djinni_market.parse.candidates_metric -> 'online' | 'active_4w' | 'unknown:<label>'); this
-- migration makes it a first-class raw.snapshot column, backfills history that was loaded by
-- an older loader before the column existed, and flags the break downstream.

ALTER TABLE raw.snapshot ADD COLUMN IF NOT EXISTS candidates_metric TEXT;

-- Backfill only pre-existing NULL rows (today's rows loaded by the pre-this-slice loader,
-- which didn't carry this column); any row loaded or re-loaded after this migration gets its
-- real value straight from the parquet's own candidates_metric field via the updated
-- SNAPSHOT_COLS, so it's never NULL going forward.
--
-- 2026-10-04 is the transition day itself and is deliberately left NULL by this backfill
-- (owner-confirmed boundary dates were <= 2026-10-03 -> 'online' and >= 2026-10-05 ->
-- 'active_4w'; this migration does not guess which definition a 2026-10-04 row used) --
-- flagged to the owner in the coder report, not silently resolved.
UPDATE raw.snapshot SET candidates_metric = 'online'
    WHERE scrape_date <= '2026-10-03' AND candidates_metric IS NULL;
UPDATE raw.snapshot SET candidates_metric = 'active_4w'
    WHERE scrape_date >= '2026-10-05' AND candidates_metric IS NULL;

-- candidates_metric is appended at the END of the SELECT list, not inlined next to the
-- candidates_* columns it logically belongs with: Postgres's CREATE OR REPLACE VIEW only
-- allows adding trailing columns, never inserting/renaming/reordering existing ones.
CREATE OR REPLACE VIEW mart.v_snapshot AS
SELECT
    s.scrape_date, s.category, m.display_name, m.role, m."group" AS role_group, m.role_hr,
    s.exp, s.english_level, s.region, s.work_format, s.level,
    s.candidates_online, s.candidates_delta_30d, s.cand_expect_min,
    s.cand_expect_max, s.offers_per_candidate, s.calculated_at, s.jobs_online, s.jobs_delta_30d,
    s.job_fork_min, s.job_fork_max, s.applies_per_job_online, s.djinni_index_30d,
    s.djinni_index_delta, s.offers_30d, s.applies_30d, s.cand_salary_p25, s.cand_salary_p75,
    s.vacancy_fork_30d_min, s.vacancy_fork_30d_max, s.hires_median_30d,
    s.jobs_with_applies_30d, s.jobs_with_applies_delta, s.applies_per_job_30d,
    s.applies_per_job_delta, s.candidates_metric
FROM raw.snapshot s
LEFT JOIN raw.category_role_mapping m USING (category);

-- series_break: true once a row's "candidates" number uses the new active-in-4-weeks
-- definition (scrape_date >= 2026-10-05) -- a consumer must never average/trend across a
-- true/false boundary without accounting for it. raw.legacy_salaries predates both Djinni
-- definitions entirely, so it's always false.
CREATE OR REPLACE VIEW mart.v_salaries_compat AS
SELECT category, salary_min, salary_max, experience_label, level, candidates, vacancies,
       scrape_date, FALSE AS series_break
FROM raw.legacy_salaries
UNION ALL
SELECT
    category,
    cand_salary_p25::INTEGER AS salary_min,
    cand_salary_p75::INTEGER AS salary_max,
    exp AS experience_label,
    level::TEXT AS level,
    candidates_online AS candidates,
    jobs_online AS vacancies,
    scrape_date,
    scrape_date >= '2026-10-05' AS series_break
FROM raw.snapshot
WHERE level = 1;

GRANT SELECT ON mart.v_snapshot, mart.v_monthly_latest, mart.v_histogram, mart.v_salaries_compat TO djinni_ro;
