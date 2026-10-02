import pandas as pd

from djinni_market import config, slices
from djinni_market.export import COMPAT_COLS, build_compat


def _tree(filters, n_cats=3):
    """Expand the full tree assuming every node is expandable (worst case)."""
    nodes = {1: slices.level1({**filters, 'category': dict(list(filters['category'].items())[:n_cats])})}
    for lvl in range(2, slices.MAX_LEVEL + 1):
        nodes[lvl] = [c for pl in slices.parent_levels(lvl) for s in nodes[pl] for c in slices.children(s, lvl)]
    return [s for lvl in nodes.values() for s in lvl]


def test_tree_has_no_duplicate_slices():
    filters = {'category': {'python': 'Python', 'java': 'Java', 'cto': 'CTO'}, 'exp': {'0': '', '1': '', '2': '',
                                                                                       '3': '', '5': ''}}
    all_ = _tree(filters)
    keys = [tuple(s[k] for k in slices.KEYS) for s in all_]
    assert len(keys) == len(set(keys))
    # worst case per L1 node: 1 + E + E*R + (E + E*R)*F
    e, r, f = len(config.ENGLISH_LEVELS), len(config.REGIONS), len(config.WORK_FORMATS)
    assert len(all_) == 4 * 6 * (1 + e + e * r + (e + e * r) * f)


def _snap(day, cands):
    return pd.DataFrame([{'scrape_date': day, 'level': 1, 'category': 'python', 'exp': '3', 'english_level': '',
                          'region': '', 'work_format': '', 'cand_salary_p25': 2000, 'cand_salary_p75': 3000,
                          'candidates_online': cands, 'jobs_online': 5}])


def test_export_rerun_same_day_replaces_not_duplicates():
    base = pd.DataFrame([['python', 0, 0, '3-5 років', 'senior', 1, 1, '2026-09-30'],
                         ['python', 1900, 2900, '3-5 років', 'senior', 800, 30, '2026-10-01']], columns=COMPAT_COLS)
    first = build_compat(base, _snap('2026-10-02', 817))
    second = build_compat(first, _snap('2026-10-02', 820))   # rerun of the same day on top of the published file
    assert len(second) == 2                                 # zero row dropped, 10-01 kept, 10-02 once
    assert second[second.scrape_date == '2026-10-02'].candidates.tolist() == [820]
