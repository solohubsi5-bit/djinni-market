"""Seeded-Postgres tier: applies the real migrations to a throwaway postgres:16 container
and exercises load_date()/load_legacy()/load_mapping() end to end, incl. rerun-idempotency
and the bad-run-status skip path. Skipped automatically if `docker` isn't on PATH or the
container can't be reached within the timeout (never fails the suite over infra).

    pytest -q tests/test_load_pg.py
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest

from db.apply_migrations import apply_all
from djinni_market import load

DOCKER = shutil.which("docker")
CONTAINER = "djinni-market-pg-test"
PORT = 15799  # distinct from any other local postgres (e.g. the 15433 dev container)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="module")
def pg_dsn():
    if not DOCKER:
        pytest.skip("docker not on PATH")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    run = subprocess.run(
        ["docker", "run", "--rm", "-d", "--name", CONTAINER,
         "-e", "POSTGRES_PASSWORD=test", "-p", f"{PORT}:5432", "postgres:16"],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        pytest.skip(f"could not start postgres:16 container: {run.stderr.strip()}")
    try:
        for _ in range(60):
            if _port_open(PORT):
                try:
                    with psycopg.connect(
                        f"host=localhost port={PORT} dbname=postgres user=postgres password=test",
                        connect_timeout=2,
                    ):
                        break
                except psycopg.OperationalError:
                    pass
            time.sleep(1)
        else:
            pytest.skip("postgres:16 container never became ready")

        admin_dsn = f"host=localhost port={PORT} dbname=postgres user=postgres password=test"
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE ROLE djinni_owner NOLOGIN")
                cur.execute("CREATE ROLE djinni_loader LOGIN")
                cur.execute("CREATE ROLE djinni_ro LOGIN")
                cur.execute("CREATE DATABASE djinni_market OWNER djinni_owner")
        db_dsn = f"host=localhost port={PORT} dbname=djinni_market user=postgres password=test"
        apply_all(db_dsn, Path(__file__).resolve().parent.parent / "db" / "migrations")
        yield db_dsn
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


def _write_day(data_dir: Path, d: str, status: str = "ok"):
    snap = pd.DataFrame([{
        "scrape_date": d, "category": "python", "exp": "", "english_level": "",
        "region": "", "work_format": "", "level": 1,
        "candidates_online": 10, "candidates_delta_30d": 1, "candidates_metric": "active_4w",
        "cand_expect_min": 1000.0,
        "cand_expect_max": 2000.0, "offers_per_candidate": 0.5, "calculated_at": f"{d}T00:00:00",
        "jobs_online": 5, "jobs_delta_30d": 0, "job_fork_min": 900.0, "job_fork_max": 1800.0,
        "applies_per_job_online": 3.0, "djinni_index_30d": 0.1, "djinni_index_delta": 0.0,
        "offers_30d": 20, "applies_30d": 30, "cand_salary_p25": 1100.0, "cand_salary_p75": 1900.0,
        "vacancy_fork_30d_min": 900.0, "vacancy_fork_30d_max": 1800.0, "hires_median_30d": 1500.0,
        "jobs_with_applies_30d": 4, "jobs_with_applies_delta": 0, "applies_per_job_30d": 2.0,
        "applies_per_job_delta": 0.0,
    }])
    (data_dir / "snapshot").mkdir(parents=True, exist_ok=True)
    snap.to_parquet(data_dir / "snapshot" / f"{d}.parquet", index=False)

    month = pd.DataFrame([{
        "category": "python", "exp": "", "english_level": "", "region": "", "work_format": "",
        "month": "2026-09-01", "level": 1, "hires_median": 1500.0, "vacancy_fork_low": 900.0,
        "vacancy_fork_high": 1800.0, "salary_series_count": 5, "jobs": 12, "applies_per_job": 2.5,
    }])
    (data_dir / "monthly").mkdir(parents=True, exist_ok=True)
    month.to_parquet(data_dir / "monthly" / f"{d}.parquet", index=False)

    (data_dir / "categories.csv").write_text(
        "category,display_name,first_seen,last_seen\n"
        f"python,Python,{d},{d}\n",
        encoding="utf-8",
    )
    runs_csv = data_dir / "runs.csv"
    header = not runs_csv.exists()
    with runs_csv.open("a", encoding="utf-8") as f:
        if header:
            f.write("date,finished_utc,status,pages,ok,errors,seconds,per_level\n")
        f.write(f"{d},{d}T00:00:00+00:00,{status},1,1,0,1,{{}}\n")


def test_load_date_then_rerun_is_idempotent(pg_dsn, tmp_path):
    data_dir = tmp_path / "data"
    _write_day(data_dir, "2026-10-02")

    with psycopg.connect(pg_dsn, autocommit=False) as conn:
        rows1 = load.load_date(conn, data_dir, date(2026, 10, 2), "abc1234")
        conn.commit()
        assert rows1 == {"snapshot": 1, "histogram": 0, "monthly": 1, "category": 1}

        # rerun same day: delete+insert / upsert must leave counts identical, not double them
        rows2 = load.load_date(conn, data_dir, date(2026, 10, 2), "abc1234")
        conn.commit()
        assert rows2 == rows1

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw.snapshot")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM raw.monthly")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT status, rows_by_table FROM meta.load_log WHERE scrape_date = %s",
                        (date(2026, 10, 2),))
            status, rows_by_table = cur.fetchone()
            assert status == "ok"
            assert rows_by_table == rows1


def test_bad_run_status_is_skipped_not_loaded(pg_dsn, tmp_path):
    data_dir = tmp_path / "data"
    _write_day(data_dir, "2026-10-05", status="failed")
    assert load.read_run_status(data_dir, date(2026, 10, 5)) == "failed"
    # main()'s gating logic (reproduced here at unit level): a non-ok day must never reach
    # load_date(); mark_skipped() is the only thing that should touch meta.load_log for it.
    with psycopg.connect(pg_dsn, autocommit=False) as conn:
        load.mark_skipped(conn, date(2026, 10, 5), "skipped_bad_run", error="runs.csv status='failed'")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw.snapshot WHERE scrape_date = %s", (date(2026, 10, 5),))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT status FROM meta.load_log WHERE scrape_date = %s", (date(2026, 10, 5),))
            assert cur.fetchone()[0] == "skipped_bad_run"


def test_load_mapping_is_full_replace(pg_dsn, tmp_path):
    csv_path = tmp_path / "mapping.csv"
    csv_path.write_text(
        "category,display_name,on_djinni,role,group,type,role_HR,mapped\n"
        "python,Python,yes,Engineer,Tech,core,Engineer,yes\n",
        encoding="utf-8",
    )
    with psycopg.connect(pg_dsn, autocommit=False) as conn:
        n1 = load.load_mapping(conn, csv_path)
        conn.commit()
        assert n1 == 1

        csv_path.write_text(
            "category,display_name,on_djinni,role,group,type,role_HR,mapped\n"
            "java,Java,yes,Engineer,Tech,core,Engineer,yes\n",
            encoding="utf-8",
        )
        n2 = load.load_mapping(conn, csv_path)
        conn.commit()
        assert n2 == 1
        with conn.cursor() as cur:
            cur.execute("SELECT category FROM raw.category_role_mapping")
            assert [r[0] for r in cur.fetchall()] == ["java"]


def test_load_date_persists_candidates_metric_from_parquet(pg_dsn, tmp_path):
    data_dir = tmp_path / "data"
    _write_day(data_dir, "2026-10-07")  # _write_day's fixture row carries candidates_metric="active_4w"
    with psycopg.connect(pg_dsn, autocommit=False) as conn:
        load.load_date(conn, data_dir, date(2026, 10, 7), "abc1234")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT candidates_metric FROM raw.snapshot WHERE scrape_date = %s", (date(2026, 10, 7),)
            )
            assert cur.fetchone()[0] == "active_4w"


def test_candidates_metric_backfill_and_series_break(pg_dsn):
    # Rows inserted directly (bypassing the loader) simulate history that was loaded by the
    # pre-this-slice loader, i.e. before candidates_metric existed in SNAPSHOT_COLS -> NULL.
    migration_sql = (
        Path(__file__).resolve().parent.parent / "db" / "migrations" / "0003_candidates_metric.sql"
    ).read_text(encoding="utf-8")
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            # distinct category ("ci-metric-test") so this doesn't collide on the
            # (scrape_date, category, ...) primary key with rows other tests in this module
            # already loaded for 2026-10-02/2026-10-05 under category "python".
            for d in ("2026-10-02", "2026-10-04", "2026-10-06"):
                cur.execute(
                    "INSERT INTO raw.snapshot (scrape_date, category, exp, english_level, region, "
                    "work_format, level, candidates_online, cand_salary_p25, cand_salary_p75, jobs_online) "
                    "VALUES (%s, 'ci-metric-test', '', '', '', '', 1, 10, 1000, 2000, 5)",
                    (d,),
                )
            # Re-running the real (idempotent) migration file is what exercises the backfill
            # against these newly-inserted NULL rows -- the fixture's own apply_all() pass ran
            # it once already, against an empty table.
            cur.execute("SET ROLE djinni_owner")
            cur.execute(migration_sql)

            cur.execute(
                "SELECT scrape_date, candidates_metric FROM raw.snapshot "
                "WHERE category = 'ci-metric-test' ORDER BY scrape_date"
            )
            by_date = dict(cur.fetchall())
            assert by_date[date(2026, 10, 2)] == "online"
            assert by_date[date(2026, 10, 4)] is None  # transition day: deliberately not backfilled
            assert by_date[date(2026, 10, 6)] == "active_4w"

            cur.execute(
                "SELECT scrape_date, series_break FROM mart.v_salaries_compat "
                "WHERE category = 'ci-metric-test' ORDER BY scrape_date"
            )
            breaks = dict(cur.fetchall())
            assert breaks[date(2026, 10, 2)] is False
            assert breaks[date(2026, 10, 4)] is False
            assert breaks[date(2026, 10, 6)] is True


def test_load_legacy_on_conflict_do_nothing(pg_dsn, tmp_path):
    df = pd.DataFrame([{
        "category": "python", "salary_min": 1000, "salary_max": 2000,
        "experience_label": "1-3", "level": "mid", "candidates": 10, "vacancies": 5,
        "scrape_date": "2025-05-01",
    }])
    p = tmp_path / "legacy.parquet"
    df.to_parquet(p, index=False)
    with psycopg.connect(pg_dsn, autocommit=False) as conn:
        n1 = load.load_legacy(conn, p)
        conn.commit()
        n2 = load.load_legacy(conn, p)  # rerun: same file, must not duplicate
        conn.commit()
        assert n1 == n2 == 1
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw.legacy_salaries")
            assert cur.fetchone()[0] == 1
