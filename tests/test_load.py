"""Pure-logic tests for the Postgres loader: parquet/CSV -> row-tuple mapping, date
discovery, run-status gating. No DB needed (see tests/test_load_pg.py for that tier)."""
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from djinni_market import load


def test_discover_dates_sorted_from_filenames(tmp_path):
    (tmp_path / "snapshot").mkdir()
    for name in ("2026-10-04.parquet", "2026-10-02.parquet", "2026-10-03.parquet"):
        (tmp_path / "snapshot" / name).touch()
    assert load.discover_dates(tmp_path) == [
        date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4),
    ]


def test_discover_dates_no_snapshot_dir(tmp_path):
    assert load.discover_dates(tmp_path) == []


def test_read_run_status_matches_row(tmp_path):
    (tmp_path / "runs.csv").write_text(
        "date,finished_utc,status,pages,ok,errors,seconds,per_level\n"
        "2026-10-02,2026-10-02T07:34:21+00:00,ok,12,12,0,4,{}\n"
        "2026-10-04,2026-10-04T07:30:00+00:00,partial,9000,8500,500,2700,{}\n",
        encoding="utf-8",
    )
    assert load.read_run_status(tmp_path, date(2026, 10, 2)) == "ok"
    assert load.read_run_status(tmp_path, date(2026, 10, 4)) == "partial"


def test_read_run_status_missing_file_or_row(tmp_path):
    assert load.read_run_status(tmp_path, date(2026, 10, 2)) is None
    (tmp_path / "runs.csv").write_text("date,status\n2026-10-02,ok\n", encoding="utf-8")
    assert load.read_run_status(tmp_path, date(2099, 1, 1)) is None


def test_rows_converts_nan_to_none_and_preserves_order():
    # Real shape from the data branch's snapshot parquet: nullable string dtype columns +
    # float64 metrics that can be NaN for a sparse slice.
    df = pd.DataFrame({
        "a": pd.array(["x", "y"], dtype="string"),
        "b": [1, 2],
        "c": [1.5, math.nan],
    })
    rows = load._rows(df, ("a", "b", "c"))
    assert rows == [("x", 1, 1.5), ("y", 2, None)]


def test_read_categories_csv(tmp_path):
    p = tmp_path / "categories.csv"
    p.write_text(
        "category,display_name,first_seen,last_seen\n"
        "cto,CTO,2026-09-01,2026-10-04\n"
        "python,,2026-10-02,2026-10-04\n",
        encoding="utf-8",
    )
    rows = load._read_categories_csv(p)
    assert rows == [
        ("cto", "CTO", "2026-09-01", "2026-10-04"),
        ("python", None, "2026-10-02", "2026-10-04"),
    ]


def test_read_categories_csv_missing_file(tmp_path):
    assert load._read_categories_csv(tmp_path / "nope.csv") == []


@pytest.mark.parametrize("cols", [load.SNAPSHOT_COLS, load.HISTOGRAM_COLS, load.MONTHLY_SRC_COLS, load.LEGACY_COLS])
def test_column_tuples_have_no_duplicates(cols):
    assert len(cols) == len(set(cols))


def test_read_run_status_real_drifted_header(tmp_path):
    # The real runs.csv header has no `status` column, but newer rows carry one at index 2; last row per date wins.
    (tmp_path / "runs.csv").write_text(
        "date,finished_utc,pages,ok,errors,seconds,per_level\n"
        '2026-10-02,2026-10-02T07:26:32+00:00,158,158,0,46,"{}"\n'
        '2026-10-02,2026-10-03T00:06:20+00:00,ok,8785,8785,0,1736,"{}"\n'
        '2026-10-04,2026-10-04T03:42:40+00:00,partial,6880,6800,80,2509,"{}"\n'
        '2026-10-05,2026-10-05T03:42:40+00:00,100,90,10,25,"{}"\n',
        encoding="utf-8",
    )
    assert load.read_run_status(tmp_path, date(2026, 10, 2)) == "ok"
    assert load.read_run_status(tmp_path, date(2026, 10, 4)) == "partial"
    assert load.read_run_status(tmp_path, date(2026, 10, 5)) == "error"
