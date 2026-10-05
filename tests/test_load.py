"""Pure-logic tests for the Postgres loader: parquet/CSV -> row-tuple mapping, date
discovery, run-status gating. No DB needed (see tests/test_load_pg.py for that tier)."""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path

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


def test_rows_whole_floats_become_int():
    import pandas as pd
    df = pd.DataFrame({"a": [-624.0, None, 3.5]})
    assert load._rows(df, ("a",)) == [(-624,), (None,), (3.5,)]


# --------------------------------------------------------------- data-source auth ----

def test_auth_url_and_env_no_token_is_a_passthrough():
    url, env, path = load._auth_url_and_env("https://example.org/repo.git", None)
    assert url == "https://example.org/repo.git"
    assert env is None
    assert path is None


def test_auth_url_and_env_with_token_never_in_url():
    url, env, path = load._auth_url_and_env("https://git.example/repo.git", "s3cr3t-token")
    try:
        assert "s3cr3t-token" not in url
        assert url == "https://oauth2@git.example/repo.git"
        assert env["DJINNI_DATA_TOKEN"] == "s3cr3t-token"
        assert env["GIT_ASKPASS"] == path
        # the askpass script references the env var by name only, never the literal secret
        script_text = Path(path).read_text(encoding="utf-8")
        assert "s3cr3t-token" not in script_text
        assert "DJINNI_DATA_TOKEN" in script_text
    finally:
        Path(path).unlink(missing_ok=True)


def test_auth_url_and_env_preserves_existing_userinfo():
    url, env, path = load._auth_url_and_env("https://bot@git.example/repo.git", "tok")
    try:
        assert url == "https://bot@git.example/repo.git"
    finally:
        Path(path).unlink(missing_ok=True)


def test_ensure_data_checkout_clone_never_puts_token_in_argv(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(load.subprocess, "run", fake_run)
    monkeypatch.setenv(load.DATA_URL_ENV, "https://git.example/repo.git")
    monkeypatch.setenv(load.DATA_TOKEN_ENV, "s3cr3t-token")

    load.ensure_data_checkout(tmp_path / "clone")

    assert calls, "expected at least one subprocess.run call"
    for args, _kwargs in calls:
        assert all("s3cr3t-token" not in str(a) for a in args)
    assert any(
        (kwargs.get("env") or {}).get("DJINNI_DATA_TOKEN") == "s3cr3t-token" for _args, kwargs in calls
    ), "token must reach git via env=, not argv"


def test_ensure_data_checkout_default_url_from_env_when_unset(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(load.subprocess, "run", fake_run)
    monkeypatch.delenv(load.DATA_URL_ENV, raising=False)
    monkeypatch.delenv(load.DATA_TOKEN_ENV, raising=False)

    load.ensure_data_checkout(tmp_path / "clone")

    assert any(load.DEFAULT_CLONE_URL in args for args in calls)
