"""Rebuild mapping/category_role_mapping.csv from the PBI HR mapping tables.

    python scripts/build_mapping.py mapping2.json mapping3.json

Inputs are `EVALUATE 'mapping2'` / `EVALUATE 'mapping3'` results from the
PL_1C_HoD_2_1 dataset (dax-authoring run_query.py output, JSON list of rows).
- mapping2: category -> role, group, type          (one row per category)
- mapping3: category -> role_HR (HR-maintained)     (a category can have several)
Every category Djinni currently lists (data/filters.json or --filters) gets a
row; categories missing from both tables are flagged `mapped=False`.
"""
import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rows(path, table):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return [{k.split('[', 1)[1].rstrip(']'): v for k, v in r.items()} for r in data]


ap = argparse.ArgumentParser()
ap.add_argument('mapping2')
ap.add_argument('mapping3')
ap.add_argument('--filters', default=str(ROOT / 'tests' / 'fixtures' / 'python_exp3.html'))
a = ap.parse_args()

m2 = {r['category']: r for r in rows(a.mapping2, 'mapping2')}
m3 = {}
for r in rows(a.mapping3, 'mapping3'):
    if r.get('role_HR'):
        m3.setdefault(r['category'], [])
        if r['role_HR'] not in m3[r['category']]:
            m3[r['category']].append(r['role_HR'])

if a.filters.endswith('.json'):
    cats = json.loads(Path(a.filters).read_text(encoding='utf-8'))['category']
else:
    import sys
    sys.path.insert(0, str(ROOT))
    from bs4 import BeautifulSoup
    from djinni_market.parse import parse_filters
    cats = parse_filters(BeautifulSoup(Path(a.filters).read_text(encoding='utf-8'), 'html.parser'))['category']

out = []
for code in sorted(set(cats) | set(m2) | set(m3)):
    r2 = m2.get(code, {})
    out.append({
        'category': code,
        'display_name': cats.get(code, ''),
        'on_djinni': code in cats,
        'role': r2.get('role', ''),
        'group': r2.get('group', ''),
        'type': r2.get('type', ''),
        'role_HR': '; '.join(m3.get(code, [])),
        'mapped': bool(r2 or m3.get(code)),
    })
p = ROOT / 'mapping' / 'category_role_mapping.csv'
with p.open('w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]))
    w.writeheader()
    w.writerows(out)
live = [r for r in out if r['on_djinni']]
print(f'{len(live)} live categories, {sum(r["mapped"] for r in live)} mapped')
print('unmapped live:', [r['category'] for r in live if not r['mapped']])
print('in PBI mapping but gone from Djinni:', [r['category'] for r in out if not r['on_djinni']])
print('multi role_HR:', {r['category']: r['role_HR'] for r in out if ';' in r['role_HR']})
