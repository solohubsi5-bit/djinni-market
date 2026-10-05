"""Load the `data` branch's daily parquet snapshots into Postgres (djinni_market).

    python -m djinni_market.load --data-dir /var/lib/djinni-market/data-clone
    python -m djinni_market.load --data-dir ./data-clone --no-fetch   # use an existing checkout as-is
    python -m djinni_market.load --data-dir ./data-clone --legacy-parquet legacy/salaries.parquet \
        --mapping-csv mapping/category_role_mapping.csv   # one-time/occasional reference loads

DSN comes from the DJINNI_PG_DSN env var only — never printed, never passed on argv, never
put in a subprocess command line. Each scrape_date is loaded in ONE transaction
(delete+insert / upsert), recorded in meta.load_log, so a rerun is always safe: already-ok
dates are skipped, a half-applied date can't exist (the transaction either commits whole or
not at all).
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import psycopg

DEFAULT_CLONE_URL = "https://github.com/solohubsi5-bit/djinni-market.git"
DATA_BRANCH = "data"
# Both configurable so the `data` branch source can move (e.g. GitHub -> GitLab) without a
# code change. DATA_URL_ENV absent -> DEFAULT_CLONE_URL (today's public, anonymous GitHub
# mirror) so nothing breaks before the switch. DATA_TOKEN_ENV is optional and only needed for
# a non-anonymous remote; see _auth_url_and_env for how it's kept out of argv/logs.
DATA_URL_ENV = "DJINNI_DATA_URL"
DATA_TOKEN_ENV = "DJINNI_DATA_TOKEN"

SNAPSHOT_COLS = (
    "scrape_date", "category", "exp", "english_level", "region", "work_format", "level",
    "candidates_online", "candidates_delta_30d", "candidates_metric", "cand_expect_min", "cand_expect_max",
    "offers_per_candidate", "calculated_at", "jobs_online", "jobs_delta_30d",
    "job_fork_min", "job_fork_max", "applies_per_job_online", "djinni_index_30d",
    "djinni_index_delta", "offers_30d", "applies_30d", "cand_salary_p25", "cand_salary_p75",
    "vacancy_fork_30d_min", "vacancy_fork_30d_max", "hires_median_30d",
    "jobs_with_applies_30d", "jobs_with_applies_delta", "applies_per_job_30d",
    "applies_per_job_delta",
)
HISTOGRAM_COLS = (
    "scrape_date", "category", "exp", "english_level", "region", "work_format", "level",
    "bin", "bin_min", "bin_max", "count",
)
MONTHLY_SRC_COLS = (
    "category", "exp", "english_level", "region", "work_format", "month", "level",
    "hires_median", "vacancy_fork_low", "vacancy_fork_high", "salary_series_count",
    "jobs", "applies_per_job",
)


def _rows(df: pd.DataFrame, cols: tuple[str, ...]) -> list[tuple]:
    """DataFrame -> list of tuples in `cols` order, NaN/NaT -> None (psycopg needs None, not NaN)."""
    sub = df[list(cols)].astype(object).where(df[list(cols)].notna(), None)
    # An int column with gaps arrives as float64 (e.g. -624.0); COPY into an INTEGER column rejects "-624.0".
    # Whole-number floats go out as int; real fractions stay float.
    def _v(x):
        return int(x) if isinstance(x, float) and x.is_integer() else x
    return [tuple(_v(x) for x in r) for r in sub.itertuples(index=False, name=None)]


# --------------------------------------------------------------- git checkout ----

def _auth_url_and_env(url: str, token: str | None) -> tuple[str, dict[str, str] | None, str | None]:
    """If `token` is set: inject a generic username into `url` and return a subprocess env
    that supplies `token` as the HTTPS password via a throwaway GIT_ASKPASS script. The token
    is never placed in argv and never appears in a printed/echoed command line (unlike `git -c
    http.extraHeader=...`, which puts it straight in argv — and in subprocess.TimeoutExpired's
    own repr if the call ever times out). Returns (url, env-or-None, askpass-script-path-or-None);
    caller must delete the script path when done.
    """
    if not token:
        return url, None, None
    parts = urlsplit(url)
    netloc = parts.netloc if "@" in parts.netloc else f"oauth2@{parts.netloc}"
    fd, askpass_path = tempfile.mkstemp(prefix="djinni-askpass-", suffix=".sh")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\nprintf '%s\\n' \"$DJINNI_DATA_TOKEN\"\n")
    os.chmod(askpass_path, 0o700)
    env = dict(os.environ)
    env["DJINNI_DATA_TOKEN"] = token
    env["GIT_ASKPASS"] = askpass_path
    env["GIT_TERMINAL_PROMPT"] = "0"
    return urlunsplit(parts._replace(netloc=netloc)), env, askpass_path


def ensure_data_checkout(dest: Path, url: str | None = None, branch: str = DATA_BRANCH) -> None:
    """Clone (if `dest` is empty) or fetch+checkout (if it already is a clone) the `data`
    branch. `url` defaults to the DJINNI_DATA_URL env var, falling back to the public,
    anonymous GitHub mirror (DEFAULT_CLONE_URL). If DJINNI_DATA_TOKEN is set (needed for a
    non-anonymous remote, e.g. the GitLab mirror), it's supplied as the HTTPS password via
    GIT_ASKPASS — see _auth_url_and_env."""
    url = url or os.environ.get(DATA_URL_ENV, DEFAULT_CLONE_URL)
    token = os.environ.get(DATA_TOKEN_ENV)
    auth_url, env, askpass_path = _auth_url_and_env(url, token)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if (dest / ".git").exists():
            subprocess.run(["git", "-C", str(dest), "remote", "set-url", "origin", auth_url], check=True)
            subprocess.run(["git", "-C", str(dest), "fetch", "origin", branch, "--depth", "1"],
                            check=True, env=env)
            subprocess.run(["git", "-C", str(dest), "checkout", "-B", branch, "FETCH_HEAD"], check=True)
        else:
            subprocess.run(
                ["git", "clone", "--branch", branch, "--single-branch", "--depth", "1", auth_url, str(dest)],
                check=True, env=env,
            )
    finally:
        if askpass_path:
            Path(askpass_path).unlink(missing_ok=True)


# --------------------------------------------------------------- discovery ----

def discover_dates(data_dir: Path) -> list[date]:
    snap_dir = data_dir / "snapshot"
    if not snap_dir.is_dir():
        return []
    return sorted(date.fromisoformat(p.stem) for p in snap_dir.glob("*.parquet"))


def read_run_status(data_dir: Path, d: date) -> str | None:
    """Status of the scrape run for day `d`, from runs.csv. None if no such row."""
    runs_csv = data_dir / "runs.csv"
    if not runs_csv.exists():
        return None
    # runs.csv drifted: its header has no `status` column (date,finished_utc,pages,ok,errors,seconds,per_level)
    # while newer rows carry an extra status field at index 2. Read positionally; the LAST row for the date wins
    # (a day can be re-run). Old-format rows (no status field) count as "ok" when errors == 0 and ok > 0.
    status = None
    with runs_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if not row or row[0] != d.isoformat():
                continue
            if len(row) >= 8:
                status = row[2]
            elif len(row) == 7:
                try:
                    status = "ok" if int(row[4]) == 0 and int(row[3]) > 0 else "error"
                except ValueError:
                    status = None
    return status


def loaded_dates(cur) -> set[date]:
    cur.execute("SELECT scrape_date FROM meta.load_log WHERE status = 'ok'")
    return {row[0] for row in cur.fetchall()}


# --------------------------------------------------------------- per-date load ----

def load_date(conn, data_dir: Path, d: date, loader_sha: str) -> dict:
    """Load one scrape_date's snapshot+histogram+monthly rows in ONE transaction.
    Returns the rows-per-table dict that gets recorded in meta.load_log."""
    rows_by_table: dict[str, int] = {}
    with conn.transaction():
        with conn.cursor() as cur:
            snap_path = data_dir / "snapshot" / f"{d.isoformat()}.parquet"
            snap_df = pd.read_parquet(snap_path)
            snap_rows = _rows(snap_df, SNAPSHOT_COLS)
            cur.execute("DELETE FROM raw.snapshot WHERE scrape_date = %s", (d,))
            if snap_rows:
                with cur.copy(
                    f"COPY raw.snapshot ({', '.join(SNAPSHOT_COLS)}) FROM STDIN"
                ) as cp:
                    for row in snap_rows:
                        cp.write_row(row)
            rows_by_table["snapshot"] = len(snap_rows)

            hist_path = data_dir / "histogram" / f"{d.isoformat()}.parquet"
            hist_rows: list[tuple] = []
            if hist_path.exists():
                hist_df = pd.read_parquet(hist_path)
                hist_rows = _rows(hist_df, HISTOGRAM_COLS)
            cur.execute("DELETE FROM raw.histogram WHERE scrape_date = %s", (d,))
            if hist_rows:
                with cur.copy(
                    f"COPY raw.histogram ({', '.join(HISTOGRAM_COLS)}) FROM STDIN"
                ) as cp:
                    for row in hist_rows:
                        cp.write_row(row)
            rows_by_table["histogram"] = len(hist_rows)

            month_path = data_dir / "monthly" / f"{d.isoformat()}.parquet"
            month_rows: list[tuple] = []
            if month_path.exists():
                month_df = pd.read_parquet(month_path)
                month_rows = _rows(month_df, MONTHLY_SRC_COLS)
            if month_rows:
                upsert = (
                    "INSERT INTO raw.monthly "
                    "(category, exp, english_level, region, work_format, month, level, "
                    "hires_median, vacancy_fork_low, vacancy_fork_high, salary_series_count, "
                    "jobs, applies_per_job, first_seen, last_seen) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (category, exp, english_level, region, work_format, month) "
                    "DO UPDATE SET level = EXCLUDED.level, hires_median = EXCLUDED.hires_median, "
                    "vacancy_fork_low = EXCLUDED.vacancy_fork_low, "
                    "vacancy_fork_high = EXCLUDED.vacancy_fork_high, "
                    "salary_series_count = EXCLUDED.salary_series_count, jobs = EXCLUDED.jobs, "
                    "applies_per_job = EXCLUDED.applies_per_job, last_seen = EXCLUDED.last_seen"
                )
                cur.executemany(upsert, [row + (d, d) for row in month_rows])
            rows_by_table["monthly"] = len(month_rows)

            cat_rows = _read_categories_csv(data_dir / "categories.csv")
            if cat_rows:
                cat_upsert = (
                    "INSERT INTO raw.category (category, display_name, first_seen, last_seen) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (category) "
                    "DO UPDATE SET display_name = EXCLUDED.display_name, last_seen = EXCLUDED.last_seen"
                )
                cur.executemany(cat_upsert, cat_rows)
            rows_by_table["category"] = len(cat_rows)

            cur.execute(
                "INSERT INTO meta.load_log (scrape_date, status, rows_by_table, loader_git_sha) "
                "VALUES (%s, 'ok', %s, %s) "
                "ON CONFLICT (scrape_date) DO UPDATE SET status = EXCLUDED.status, "
                "rows_by_table = EXCLUDED.rows_by_table, loader_git_sha = EXCLUDED.loader_git_sha, "
                "loaded_at = now(), error = NULL",
                (d, psycopg.types.json.Json(rows_by_table), loader_sha),
            )
    return rows_by_table


def _read_categories_csv(path: Path) -> list[tuple]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [
            (r["category"], r["display_name"] or None, r["first_seen"], r["last_seen"])
            for r in csv.DictReader(f)
        ]


def mark_skipped(conn, d: date, status: str, error: str | None = None) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meta.load_log (scrape_date, status, error) VALUES (%s, %s, %s) "
                "ON CONFLICT (scrape_date) DO UPDATE SET status = EXCLUDED.status, "
                "error = EXCLUDED.error, loaded_at = now()",
                (d, status, error),
            )


# --------------------------------------------------------------- one-time reference loads ----

LEGACY_COLS = (
    "category", "salary_min", "salary_max", "experience_label", "level",
    "candidates", "vacancies", "scrape_date",
)


def load_legacy(conn, parquet_path: Path) -> int:
    """One-time import of legacy/salaries.parquet. ON CONFLICT DO NOTHING -> safe to rerun."""
    df = pd.read_parquet(parquet_path)
    rows = _rows(df, LEGACY_COLS)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO raw.legacy_salaries "
                "(category, salary_min, salary_max, experience_label, level, candidates, "
                "vacancies, scrape_date) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                rows,
            )
    return len(rows)


def load_mapping(conn, csv_path: Path) -> int:
    """Full replace of raw.category_role_mapping from mapping/category_role_mapping.csv
    (small reference file, no history to keep — reloaded wholesale each time)."""
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = [
            (
                r["category"], r["display_name"] or None, r["on_djinni"] or None,
                r["role"] or None, r["group"] or None, r["type"] or None,
                r["role_HR"] or None, r["mapped"] or None,
            )
            for r in csv.DictReader(f)
        ]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("DELETE FROM raw.category_role_mapping")
            if rows:
                cur.executemany(
                    'INSERT INTO raw.category_role_mapping '
                    '(category, display_name, on_djinni, role, "group", type, role_hr, mapped) '
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
    return len(rows)


# --------------------------------------------------------------- loader sha ----

def _loader_git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).parent.parent), "rev-parse", "--short", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


# --------------------------------------------------------------- CLI ----

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, help="local clone dir of the `data` branch")
    ap.add_argument("--clone-url", default=os.environ.get(DATA_URL_ENV, DEFAULT_CLONE_URL))
    ap.add_argument("--no-fetch", action="store_true", help="use --data-dir as-is, skip git fetch/checkout")
    ap.add_argument("--legacy-parquet", help="one-time import of legacy/salaries.parquet")
    ap.add_argument("--mapping-csv", help="reload mapping/category_role_mapping.csv")
    a = ap.parse_args(argv)

    dsn = os.environ.get("DJINNI_PG_DSN")
    if not dsn:
        print("DJINNI_PG_DSN is not set", file=sys.stderr)
        return 2

    data_dir = Path(a.data_dir)
    if not a.no_fetch:
        ensure_data_checkout(data_dir, a.clone_url)

    loader_sha = _loader_git_sha()
    loaded, skipped, errors = 0, 0, 0
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            already_ok = loaded_dates(cur)
        for d in discover_dates(data_dir):
            if d in already_ok:
                continue
            status = read_run_status(data_dir, d)
            if status != "ok":
                mark_skipped(conn, d, "skipped_bad_run", error=f"runs.csv status={status!r}")
                print(f"{d}: skipped (run status {status!r})")
                skipped += 1
                continue
            try:
                rows = load_date(conn, data_dir, d, loader_sha)
                print(f"{d}: loaded {rows}")
                loaded += 1
            except Exception as e:  # noqa: BLE001 - recorded, then re-raised context preserved via log
                mark_skipped(conn, d, "error", error=f"{type(e).__name__}: {e}")
                print(f"{d}: ERROR {type(e).__name__}: {e}", file=sys.stderr)
                errors += 1

        if a.legacy_parquet:
            n = load_legacy(conn, Path(a.legacy_parquet))
            print(f"legacy_salaries: {n} rows considered (ON CONFLICT DO NOTHING)")
        if a.mapping_csv:
            n = load_mapping(conn, Path(a.mapping_csv))
            print(f"category_role_mapping: {n} rows (full replace)")

    print(f"done: loaded={loaded} skipped={skipped} errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
