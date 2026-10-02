"""Build the published artifacts (gh-pages).

    python -m djinni_market.export --data store --prev prev/salaries_compat.csv --out public

The git `data` branch only holds the last 7 days, so the long compat history is
carried forward on gh-pages itself: base = previously published
salaries_compat.csv (or legacy/salaries.parquet on the very first run), then the
days present in --data replace/append.

public/djinni_structured_tqdm.xlsx  - drop-in replacement for the old file (sheet
                                      `Salaries`, same 8 columns). Zero salaries are
                                      blanked (old scraper wrote 0 for "no data").
public/salaries_compat.csv          - same rows as CSV (faster to read)
public/snapshot_latest.csv          - every slice, every field, latest day
public/snapshot_7d.parquet          - every slice, every field, rolling 7 days
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

EXP_LABEL = {'0': ('<1 року', 'junior'), '1': ('1-2 роки', 'middle'), '2': ('2-3 роки', 'middle'),
             '3': ('3-5 років', 'senior'), '5': ('5+ років', 'senior')}
COMPAT_COLS = ['category', 'salary_min', 'salary_max', 'experience_label', 'level', 'candidates', 'vacancies',
               'scrape_date']


def load_snapshots(data: Path) -> pd.DataFrame:
    files = sorted((data / 'snapshot').glob('*.parquet'))
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True) if files else pd.DataFrame()


def compat_rows(snap: pd.DataFrame) -> pd.DataFrame:
    s = snap[(snap.level == 1) & (snap.category != '') & (snap.exp != '')].copy()
    s['experience_label'] = s.exp.map(lambda e: EXP_LABEL[e][0])
    s['level'] = s.exp.map(lambda e: EXP_LABEL[e][1])
    s = s.rename(columns={'cand_salary_p25': 'salary_min', 'cand_salary_p75': 'salary_max',
                          'candidates_online': 'candidates', 'jobs_online': 'vacancies'})
    return s[COMPAT_COLS]


def load_base(prev: Path | None, legacy: Path) -> pd.DataFrame:
    if prev and prev.exists():
        return pd.read_csv(prev, dtype={'scrape_date': str})
    if legacy.exists():
        return pd.read_parquet(legacy)
    return pd.DataFrame(columns=COMPAT_COLS)


def build_compat(base: pd.DataFrame, snap: pd.DataFrame) -> pd.DataFrame:
    new = compat_rows(snap) if len(snap) else pd.DataFrame(columns=COMPAT_COLS)
    if len(new):
        base = base[~base.scrape_date.isin(set(new.scrape_date))]
    df = pd.concat([base, new], ignore_index=True)
    for c in ('salary_min', 'salary_max'):
        df[c] = pd.to_numeric(df[c]).astype('Int64').mask(lambda x: x == 0)
    df = df[df.salary_min.notna() | df.salary_max.notna()]
    for c in ('candidates', 'vacancies'):
        df[c] = pd.to_numeric(df[c]).astype('Int64')
    return df.sort_values(['scrape_date', 'category', 'experience_label'], kind='stable')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data')
    ap.add_argument('--prev', default='', help='previously published salaries_compat.csv')
    ap.add_argument('--legacy', default='legacy/salaries.parquet')
    ap.add_argument('--out', default='public')
    a = ap.parse_args(argv)
    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    snap = load_snapshots(data)
    compat = build_compat(load_base(Path(a.prev) if a.prev else None, Path(a.legacy)), snap)
    compat.to_csv(out / 'salaries_compat.csv', index=False, encoding='utf-8')
    with pd.ExcelWriter(out / 'djinni_structured_tqdm.xlsx', engine='openpyxl') as xw:
        compat.to_excel(xw, sheet_name='Salaries', index=False)
    if len(snap):
        snap.to_parquet(out / 'snapshot_7d.parquet', index=False, compression='zstd')
        snap[snap.scrape_date == snap.scrape_date.max()].to_csv(out / 'snapshot_latest.csv', index=False,
                                                                 encoding='utf-8')
    print(f'compat rows: {len(compat)} ({compat.scrape_date.min()}..{compat.scrape_date.max()}), '
          f'snapshot rows: {len(snap)}')


if __name__ == '__main__':
    main()
