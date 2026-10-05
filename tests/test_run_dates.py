import csv
from datetime import datetime, timezone

from djinni_market.run import run_date, write_run_row


def test_nightly_cron_is_labelled_with_the_kyiv_day():
    # 23:37 UTC on 10-02 is 02:37 on 10-03 in Kyiv (summer, UTC+3): label 10-03, not 10-02
    assert run_date(datetime(2026, 10, 2, 23, 37, tzinfo=timezone.utc)) == '2026-10-03'
    # GitHub-delayed run of the same night still lands on the same Kyiv day
    assert run_date(datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc)) == '2026-10-04'
    # winter (UTC+2): 23:30 UTC is 01:30 next day
    assert run_date(datetime(2026, 12, 1, 23, 30, tzinfo=timezone.utc)) == '2026-12-02'
    # a daytime manual run keeps its own day
    assert run_date(datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)) == '2026-10-02'


def test_runs_csv_one_row_per_date_and_legacy_rows_normalised(tmp_path):
    p = tmp_path / 'runs.csv'
    p.write_text('date,finished_utc,pages,ok,errors,seconds,per_level\n'
                 '2026-10-02,2026-10-02T07:26:32+00:00,158,158,0,46,{}\n'
                 '2026-10-02,2026-10-02T08:22:09+00:00,ok,8775,8775,0,2737,{}\n', encoding='utf-8')
    write_run_row(p, {'date': '2026-10-03', 'finished_utc': 'x', 'status': 'ok', 'pages': 1, 'ok': 1,
                      'errors': 0, 'seconds': 1, 'per_level': '{}'})
    write_run_row(p, {'date': '2026-10-03', 'finished_utc': 'y', 'status': 'partial', 'pages': 2, 'ok': 2,
                      'errors': 0, 'seconds': 2, 'per_level': '{}'})
    rows = list(csv.DictReader(p.open(encoding='utf-8', newline='')))
    assert list(rows[0]) == ['date', 'finished_utc', 'status', 'pages', 'ok', 'errors', 'seconds', 'per_level']
    # the two pre-existing 10-02 rows are left as they were (only the written date is de-duplicated)
    assert [(r['date'], r['status']) for r in rows] == [('2026-10-02', 'ok'), ('2026-10-02', 'ok'),
                                                        ('2026-10-03', 'partial')]
    assert rows[0]['pages'] == '158' and rows[2]['finished_utc'] == 'y'
