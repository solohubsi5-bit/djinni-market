import json

from djinni_market import report


def test_validate_filters_flags_renamed_value():
    filters = {'english_level': {'upper': '', 'fluent': '', 'proficient': '', 'native': ''},
               'region': {'UKR': ''}, 'work_format': {'office': '', 'remote_only': ''}}
    assert report.validate_filters(filters) == {'work_format': ['full_remote']}


def test_filter_changes_detects_added_removed_renamed(tmp_path):
    prev = tmp_path / 'filters.json'
    prev.write_text(json.dumps({'category': {'java': 'Java', 'kotlin': 'Kotlin', 'php': 'PHP'}}), encoding='utf-8')
    ch = report.filter_changes(prev, {'category': {'java': 'Java', 'php': 'PHP / Laravel', 'rust': 'Rust'}})
    assert ch == {'category': {'added': ['rust'], 'removed': ['kotlin'], 'renamed': {'php': ['PHP', 'PHP / Laravel']}}}


def test_metric_changes_flags_redefinition(tmp_path, capsys):
    import pandas as pd
    (tmp_path / 'snapshot').mkdir()
    pd.DataFrame({'level': [1], 'candidates_metric': ['online']}).to_parquet(tmp_path / 'snapshot' / '2026-10-03.parquet')
    res = report.metric_changes([{'level': 1, 'candidates_metric': 'active_4w'}], tmp_path, '2026-10-05')
    assert res == {'now': ['active_4w'], 'before': ['online']}
    assert 'candidates metric changed' in capsys.readouterr().out
