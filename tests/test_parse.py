from pathlib import Path

from bs4 import BeautifulSoup

from djinni_market import slices
from djinni_market.parse import parse_filters, parse_page, to_num

FX = Path(__file__).parent / 'fixtures'


def page(name):
    return (FX / name).read_text(encoding='utf-8')


def test_full_page_snapshot():
    s = parse_page(page('python_exp3.html'))['snapshot']
    assert (s['candidates_online'], s['candidates_delta_30d']) == (817, 26)
    assert (s['cand_salary_p25'], s['cand_salary_p75']) == (2000, 3000)
    assert (s['jobs_online'], s['jobs_delta_30d']) == (35, 5)
    assert (s['job_fork_min'], s['job_fork_max']) == (2000, 3500)
    assert s['djinni_index_30d'] == 0.16 and s['offers_30d'] == 403 and s['applies_30d'] == 2534
    assert s['hires_median_30d'] == 2550
    assert (s['jobs_with_applies_30d'], s['jobs_with_applies_delta']) == (65, -4)
    assert (s['applies_per_job_30d'], s['applies_per_job_delta']) == (57.1, 15.0)
    assert s['calculated_at'].startswith('2026-10-01')


def test_charts():
    p = parse_page(page('python_exp3.html'))
    assert len(p['histogram']) == 12 and p['histogram'][1]['count'] == 30
    assert len(p['monthly']) == 12
    last = p['monthly'][-1]
    assert last['month'] == '2026-09-01' and last['hires_median'] == 2550.0 and last['jobs'] == 66


def test_empty_page_blanks_salaries():
    p = parse_page(page('cfo_exp0.html'))
    s = p['snapshot']
    assert s['candidates_online'] == 0 and s['cand_salary_p25'] is None and s['job_fork_min'] is None
    assert slices.is_empty(s)
    assert p['histogram'] == []


def test_filters_discovered():
    f = parse_filters(BeautifulSoup(page('python_exp3.html'), 'html.parser'))
    assert len(f['category']) == 146 and f['category']['dotnet'] == 'C# / .NET'
    assert list(f['exp']) == ['0', '1', '2', '3', '5']
    assert 'UKR' in f['region'] and 'office' in f['work_format']
    assert {'intermediate', 'upper', 'fluent', 'proficient', 'native'} <= set(f['english_level'])


def test_to_num():
    assert to_num('+26') == 26 and to_num('-4') == -4 and to_num('↑15.0') == 15.0 and to_num('↓2.5') == -2.5
    assert to_num('1 234') == 1234 and to_num('—') is None


def test_children_expand_one_dimension():
    node = {'category': 'python', 'exp': '3', 'english_level': 'upper', 'region': '', 'work_format': '', 'level': 2}
    kids = slices.children(node, 3)
    assert [k['region'] for k in kids] == ['UKR'] and kids[0]['english_level'] == 'upper' and kids[0]['level'] == 3
    assert [k['work_format'] for k in slices.children(node, 4)] == ['office', 'full_remote']
    assert slices.parent_levels(4) == (2, 3) and slices.parent_levels(2) == (1,)


def test_expandable_threshold():
    assert slices.expandable({'candidates_online': 10, 'jobs_online': 0})
    assert slices.expandable({'candidates_online': 2, 'jobs_online': 12})
    assert not slices.expandable({'candidates_online': 9, 'jobs_online': 9})
