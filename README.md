# djinni-market

Daily snapshot of the Ukrainian IT job market from the public
[djinni.co/salaries](https://djinni.co/salaries/) dashboard. Successor of
`solohubsi/testprHR1` (Selenium, 4 numbers per page): same page, plain HTTP, every
number on it, at a finer grain.

## What is collected

One row per slice per day in `data/snapshot/<date>.parquet`:

| group | fields |
|---|---|
| candidates | `candidates_online`, `candidates_delta_30d`, `cand_salary_p25/p75`, `cand_expect_min/max`, `offers_per_candidate` |
| vacancies | `jobs_online`, `jobs_delta_30d`, `job_fork_min/max`, `applies_per_job_online` |
| Djinni index (30d) | `djinni_index_30d`, `djinni_index_delta`, `offers_30d`, `applies_30d` |
| hiring (30d) | `vacancy_fork_30d_min/max` (median vacancy bounds), `hires_median_30d` (confirmed hires) |
| applications (30d) | `jobs_with_applies_30d` (+delta), `applies_per_job_30d` (+delta) |
| meta | `calculated_at` (Djinni's own aggregation timestamp), `level` |

For category x experience slices (level 1) also:
- `data/histogram/<date>.parquet` - candidate salary histogram (12 bins)
- `data/monthly/<date>.parquet` - 12-month series: hires median, vacancy fork, jobs, applies/job

Slice keys: `category, exp, english_level, region, work_format`; empty string = "any".
Zero salaries on the page mean "no data" and are stored as null.

## Grain

See `djinni_market/config.py`. A slice is only split further if it has
>= 10 candidates or >= 10 vacancies.

| level | adds | values |
|---|---|---|
| 1 | category x exp | all 146 categories (+all) x 0/1/2/3/5 (+any) |
| 2 | english_level | upper, fluent, proficient, native (exclusive levels on Djinni) |
| 3 | region | UKR (all = level 2) |
| 4 | work_format | office, full_remote (total = parent), on levels 2 and 3 |

## Branches / where data lives

| branch | content |
|---|---|
| `main` | code + `legacy/salaries.parquet` (frozen old-scraper history, 2025-04-28..2026-10-01) |
| `data` | rolling **7-day** buffer: `snapshot/`, `histogram/`, `monthly/` parquet per day, `categories.csv`, `filters.json`, `runs.csv`. One orphan commit, force-pushed daily - no history growth. The (planned) server-side DB loader pulls from here; 7 days covers server downtime. |
| `gh-pages` | published files, see below |

Long-term storage is meant to be a DB on the server (TBD), not git.

## Published files (gh-pages)

`python -m djinni_market.export` builds:
- `djinni_structured_tqdm.xlsx` / `salaries_compat.csv` - drop-in for the old file
  (sheet `Salaries`, same 8 columns). Full history carried forward run to run
  (seeded from `legacy/`), plus new level-1 rows. Zeros blanked.
- `snapshot_latest.csv` (latest day, all slices), `snapshot_7d.parquet`

Raw URL pattern: `https://raw.githubusercontent.com/<owner>/djinni-market/gh-pages/<file>`

## Run locally

```
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python -m djinni_market.run --categories python,java --out tmp_data   # smoke test
python -m pytest -q
```

## Politeness / blocking

robots.txt allows `/salaries/`. 5 workers x 0.5 s pause (~7 req/s), retries with
backoff, a circuit breaker stops the run after 20 consecutive 403/429, and a page
missing expected anchors counts as an error (run is not written above 2% errors).
