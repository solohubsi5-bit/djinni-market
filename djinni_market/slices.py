from __future__ import annotations

from . import config

KEYS = ('category', 'exp', 'english_level', 'region', 'work_format')
MAX_LEVEL = 4


def _slice(level, **kw):
    s = dict.fromkeys(KEYS, '')
    s.update(kw)
    s['level'] = level
    return s


def level1(filters: dict) -> list[dict]:
    """category (+all) x exp (+any). '' means "any" (no filter)."""
    cats = [''] + list(filters['category'])
    exps = [''] + list(filters['exp'])
    return [_slice(1, category=c, exp=e) for c in cats for e in exps]


# level -> (dimension it adds, levels whose nodes it expands, values)
_EXPAND = {
    2: ('english_level', (1,), lambda: config.ENGLISH_LEVELS),
    3: ('region', (2,), lambda: config.REGIONS),
    4: ('work_format', (2, 3), lambda: config.WORK_FORMATS),
}


def parent_levels(level: int) -> tuple:
    return _EXPAND[level][1]


def children(node: dict, level: int) -> list[dict]:
    dim, _, values = _EXPAND[level]
    base = {k: node[k] for k in KEYS}
    return [_slice(level, **{**base, dim: v}) for v in values()]


def expandable(snapshot: dict) -> bool:
    return ((snapshot.get('candidates_online') or 0) >= config.MIN_CANDIDATES
            or (snapshot.get('jobs_online') or 0) >= config.MIN_JOBS)


def is_empty(snapshot: dict) -> bool:
    return not any(snapshot.get(k) for k in ('candidates_online', 'jobs_online', 'jobs_with_applies_30d'))
