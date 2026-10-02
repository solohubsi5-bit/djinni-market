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
