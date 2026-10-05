-- djinni_market migration 0002: mart read views (what djinni_ro / any downstream consumer sees).

CREATE OR REPLACE VIEW mart.v_snapshot AS
SELECT
    s.scrape_date, s.category, m.display_name, m.role, m."group" AS role_group, m.role_hr,
    s.exp, s.english_level, s.region, s.work_format, s.level,
    s.candidates_online, s.candidates_delta_30d, s.cand_expect_min, s.cand_expect_max,
    s.offers_per_candidate, s.calculated_at, s.jobs_online, s.jobs_delta_30d,
    s.job_fork_min, s.job_fork_max, s.applies_per_job_online, s.djinni_index_30d,
    s.djinni_index_delta, s.offers_30d, s.applies_30d, s.cand_salary_p25, s.cand_salary_p75,
    s.vacancy_fork_30d_min, s.vacancy_fork_30d_max, s.hires_median_30d,
    s.jobs_with_applies_30d, s.jobs_with_applies_delta, s.applies_per_job_30d,
    s.applies_per_job_delta
FROM raw.snapshot s
LEFT JOIN raw.category_role_mapping m USING (category);

CREATE OR REPLACE VIEW mart.v_monthly_latest AS
SELECT * FROM raw.monthly;

CREATE OR REPLACE VIEW mart.v_histogram AS
SELECT * FROM raw.histogram;

-- Best-effort reconstruction of the legacy "8 compat columns" shape for L1-only snapshot
-- rows, so a future consumer has a single place to look across old + new history.
-- ASSUMPTION (not confirmed against the old scraper's exact field semantics — flagged in
-- the coder report; no current consumer depends on this view since Power BI is out of
-- scope for this slice): salary_min/max <- cand_salary_p25/p75, candidates <-
-- candidates_online, vacancies <- jobs_online, experience_label <- exp, level <- level::text.
CREATE OR REPLACE VIEW mart.v_salaries_compat AS
SELECT category, salary_min, salary_max, experience_label, level, candidates, vacancies, scrape_date
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
    scrape_date
FROM raw.snapshot
WHERE level = 1;

GRANT SELECT ON mart.v_snapshot, mart.v_monthly_latest, mart.v_histogram, mart.v_salaries_compat TO djinni_ro;
