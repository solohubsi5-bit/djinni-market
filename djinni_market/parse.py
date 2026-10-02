"""Parse a djinni.co/salaries/ page into structured records.

The page is server-rendered: headline cards are plain HTML, the three charts
ship their data as `JSON.parse("...")` literals inside <script> tags. Nothing
here needs a browser.
"""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

_JSON_RE = re.compile(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\)')
_NUM = r'[+\-−↑↓]?\s*\d[\d\s,]*(?:\.\d+)?'


class LayoutError(Exception):
    """Raised when an anchor the parser relies on is missing from the page."""


def to_num(s: str | None):
    if s is None:
        return None
    s = s.strip().replace('\xa0', '').replace(' ', '').replace(',', '')
    s = s.replace('−', '-').replace('↓', '-').replace('↑', '').replace('+', '')
    if not s or s in {'-', '—'}:
        return None
    v = float(s)
    return int(v) if v.is_integer() and '.' not in s else v


def _text(el) -> str:
    return re.sub(r'\s+', ' ', el.get_text(' ', strip=True)) if el else ''


def _range(text: str, label: str):
    """'$ 2000 – $ 3000' after a label -> (2000, 3000); single value -> (v, None)."""
    m = re.search(re.escape(label) + r'\s*\$\s*(' + _NUM + r')(?:\s*\+)?(?:\s*–\s*\$\s*(' + _NUM + r'))?', text)
    return (to_num(m.group(1)), to_num(m.group(2))) if m else (None, None)


def _after(text: str, label: str):
    m = re.search(re.escape(label) + r'\s*(' + _NUM + r')', text)
    return to_num(m.group(1)) if m else None


def _headline(card):
    """Big number + its 30-day diff badge inside a card."""
    if card is None:
        return None, None
    big = card.select_one('.fs-1')
    diff = card.select_one('.text-diff')
    return to_num(_text(big)) if big else None, to_num(_text(diff)) if diff else None


def _calc_at(el):
    return el.get('data-calculated-at') if el is not None else None


def parse_snapshot(soup: BeautifulSoup) -> dict:
    out: dict = {}
    required = ['candidates_card', 'jobs_card', 'djinni_index_card', 'candidates_salaries', 'jobs_salaries',
                'jobs_applications']
    missing = [i for i in required if soup.find(id=i) is None]
    if missing:
        raise LayoutError(f'missing anchors: {missing}')

    c = soup.find(id='candidates_card')
    t = _text(c)
    out['candidates_online'], out['candidates_delta_30d'] = _headline(c)
    out['cand_expect_min'], out['cand_expect_max'] = _range(t, 'Середні очікування')
    out['offers_per_candidate'] = _after(t, 'Пропозицій в середньому')
    out['calculated_at'] = _calc_at(c)

    j = soup.find(id='jobs_card')
    t = _text(j)
    out['jobs_online'], out['jobs_delta_30d'] = _headline(j)
    out['job_fork_min'], out['job_fork_max'] = _range(t, 'Середня вилка')
    out['applies_per_job_online'] = _after(t, 'Відгуків в середньому')

    d = soup.find(id='djinni_index_card')
    t = _text(d)
    out['djinni_index_30d'], out['djinni_index_delta'] = _headline(d)
    m = re.search(r'(' + _NUM + r')\s*пропозиц', t)
    out['offers_30d'] = to_num(m.group(1)) if m else None
    m = re.search(r'(' + _NUM + r')\s*відгук', t)
    out['applies_30d'] = to_num(m.group(1)) if m else None

    cs = soup.find(id='candidates_salaries')
    t = _text(cs.select_one('.fs-1'))
    out['cand_salary_p25'], out['cand_salary_p75'] = _range(t, '')

    js = soup.find(id='jobs_salaries')
    heads = [_text(x) for x in js.select('.fs-1')]
    out['vacancy_fork_30d_min'], out['vacancy_fork_30d_max'] = _range(heads[0], '') if heads else (None, None)
    out['hires_median_30d'] = _range(heads[1], '')[0] if len(heads) > 1 else None

    ja = soup.find(id='jobs_applications')
    t = _text(ja)
    m = re.search(r'Вакансій з відгуками\s*(' + _NUM + r')\s*(' + _NUM + r')?', t)
    out['jobs_with_applies_30d'] = to_num(m.group(1)) if m else None
    out['jobs_with_applies_delta'] = to_num(m.group(2)) if m and m.group(2) else None
    m = re.search(r'Відгуків на вакансію в середньому\s*(' + _NUM + r')\s*(' + _NUM + r')?', t)
    out['applies_per_job_30d'] = to_num(m.group(1)) if m else None
    out['applies_per_job_delta'] = to_num(m.group(2)) if m and m.group(2) else None

    # Empty slices render "$ 0 – $ 0"; a zero salary is "no data", not a real value.
    for k in SALARY_FIELDS:
        if out.get(k) == 0:
            out[k] = None
    return out


SALARY_FIELDS = ('cand_expect_min', 'cand_expect_max', 'job_fork_min', 'job_fork_max', 'cand_salary_p25',
                 'cand_salary_p75', 'vacancy_fork_30d_min', 'vacancy_fork_30d_max', 'hires_median_30d')


def parse_charts(html: str):
    """-> (histogram rows, monthly rows). Blobs are identified by their keys, not position."""
    hist, sal, app = [], {}, {}
    for m in _JSON_RE.finditer(html):
        data = json.loads(json.loads('"' + m.group(1) + '"'))
        if not data:
            continue
        keys = set(data[0])
        if 'bin' in keys:
            hist = [{'bin': r['bin'], 'bin_min': r.get('salary_min'), 'bin_max': r.get('salary_max'),
                     'count': r.get('count')} for r in data]
        elif 'median' in keys:
            sal = {r['month'][:10]: r for r in data}
        elif 'applies_per_job' in keys:
            app = {r['month'][:10]: r for r in data}
    monthly = []
    for month in sorted(set(sal) | set(app)):
        s, a = sal.get(month, {}), app.get(month, {})
        monthly.append({
            'month': month,
            'hires_median': s.get('median') or None,          # line: median salary of confirmed hires
            'vacancy_fork_low': s.get('lower_bound') or None,  # bar: median of vacancy lower bounds
            'vacancy_fork_high': s.get('upper_bound') or None,
            'salary_series_count': s.get('count'),
            'jobs': a.get('jobs'),
            'applies_per_job': a.get('applies_per_job'),
        })
    return hist, monthly


def parse_filters(soup: BeautifulSoup) -> dict:
    """Category list (with display names) + every other filter's allowed values, read from the form."""
    out: dict = {}
    cat = soup.find(id='filter_category')
    if cat is None:
        raise LayoutError('missing filter_category')
    cats = {}
    for el in cat.find_all(attrs={'value': True}):
        v = el.get('value')
        if v and re.fullmatch(r'[a-z0-9_]+', v) and el.get('name', 'category') == 'category':
            cats.setdefault(v, _text(el) or None)
    out['category'] = cats
    # other filters: <input type=hidden id=filter_X name=...> + <div data-controls="#filter_X"> with data-value items
    for inp in soup.select('input[type=hidden][id^=filter_]'):
        name = inp.get('name')
        if not name or name == 'category':
            continue
        menu = soup.find(attrs={'data-controls': '#' + inp['id']})
        if menu is None:
            continue
        vals = {}
        for item in menu.find_all(attrs={'data-value': True}):
            if item['data-value']:
                vals.setdefault(item['data-value'], _text(item) or None)
        out[name] = vals
    return out


def parse_page(html: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    hist, monthly = parse_charts(html)
    return {'snapshot': parse_snapshot(soup), 'histogram': hist, 'monthly': monthly}
