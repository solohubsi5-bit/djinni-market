"""Daily scrape: discover filters -> expand the slice tree level by level -> write parquet.

    python -m djinni_market.run                                   # full tree
    python -m djinni_market.run --categories python,java --out tmp_data   # smoke test
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from . import config, fetch, parse, slices

SNAPSHOT_COLS = slices.KEYS + ('level',)


def scrape_one(s: dict):
    return s, parse.parse_page(fetch.get({k: s[k] for k in slices.KEYS}))


def fetch_all(todo, ex, t0, label):
    done, errors = [], []
    futs = [ex.submit(scrape_one, s) for s in todo]
    for n, f in enumerate(as_completed(futs), 1):
        try:
            done.append(f.result())
        except Exception as e:  # noqa: BLE001 - counted, run fails above threshold
            errors.append(repr(e))
        if n % 500 == 0:
            print(f'  {label}: {n}/{len(todo)}  errors={len(errors)}  {time.time() - t0:.0f}s', flush=True)
    return done, errors


def update_categories(path: Path, filters: dict, today: str):
    rows = {}
    if path.exists():
        with path.open(encoding='utf-8', newline='') as f:
            rows = {r['category']: r for r in csv.DictReader(f)}
    for code, name in filters['category'].items():
        r = rows.setdefault(code, {'category': code, 'display_name': name, 'first_seen': today, 'last_seen': today})
        r['display_name'] = name or r['display_name']
        r['last_seen'] = today
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['category', 'display_name', 'first_seen', 'last_seen'])
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: r['category']))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-level', type=int, default=slices.MAX_LEVEL)
    ap.add_argument('--categories', default='', help='comma list, restricts L1 (smoke test)')
    ap.add_argument('--out', default='data')
    ap.add_argument('--date', default=date.today().isoformat())
    ap.add_argument('--force', action='store_true', help='overwrite an existing day')
    ap.add_argument('--keep-days', type=int, default=0, help='after writing, delete daily files older than N days')
    a = ap.parse_args(argv)

    out = Path(a.out)
    day = a.date
    if (out / 'snapshot' / f'{day}.parquet').exists() and not a.force:
        print(f'{day} already collected - nothing to do')
        return 0

    t0 = time.time()
    filters = parse.parse_filters(BeautifulSoup(fetch.get({'category': 'python'}), 'html.parser'))
    todo = slices.level1(filters)
    if a.categories:
        keep = set(a.categories.split(','))
        todo = [s for s in todo if s['category'] in keep]
    print(f'{len(filters["category"])} categories on Djinni; L1 = {len(todo)} slices', flush=True)

    results, errors, per_level = [], [], {}
    expand = {}  # level -> nodes big enough to split further
    with ThreadPoolExecutor(config.WORKERS) as ex:
        level = 1
        while todo:
            done, errs = fetch_all(todo, ex, t0, f'L{level}')
            results += done
            errors += errs
            expand[level] = [s for s, p in done if slices.expandable(p['snapshot'])]
            per_level[level] = {'fetched': len(todo), 'expandable': len(expand[level]), 'errors': len(errs)}
            print(f'L{level}: {per_level[level]}  {time.time() - t0:.0f}s', flush=True)
            if fetch.breaker_open():
                print('circuit breaker open (repeated 403/429) - stopping, keeping what we have', file=sys.stderr)
                break
            level += 1
            if level > a.max_level:
                break
            todo = [c for pl in slices.parent_levels(level) for s in expand.get(pl, [])
                    for c in slices.children(s, level)]

    total = sum(v['fetched'] for v in per_level.values())
    run = {'date': day, 'finished_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'pages': total, 'ok': len(results), 'errors': len(errors), 'seconds': round(time.time() - t0),
           'per_level': json.dumps(per_level)}
    print(json.dumps(run))
    if errors:
        print('first errors:', *errors[:5], sep='\n  ')
    if len(errors) / max(total, 1) > config.MAX_ERROR_RATE:
        print(f'error rate above {config.MAX_ERROR_RATE:.0%} - not writing', file=sys.stderr)
        return 1

    snaps, hists, months = [], [], []
    for s, p in results:
        key = {k: s[k] for k in SNAPSHOT_COLS}
        snaps.append({**key, **p['snapshot']})
        if s['level'] in config.DETAIL_LEVELS:
            hists += [{**key, **h} for h in p['histogram']]
            months += [{**key, **m} for m in p['monthly']]

    sort_keys = list(slices.KEYS)
    for name, rows, extra in (('snapshot', snaps, []), ('histogram', hists, ['bin_min']),
                              ('monthly', months, ['month'])):
        p = out / name / f'{day}.parquet'
        p.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(rows)
        df.insert(0, 'scrape_date', day)
        df.sort_values(sort_keys + extra).to_parquet(p, index=False, compression='zstd')
    update_categories(out / 'categories.csv', filters, day)
    (out / 'filters.json').write_text(json.dumps(filters, ensure_ascii=False, indent=1), encoding='utf-8')
    runs = out / 'runs.csv'
    new = not runs.exists()
    with runs.open('a', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(run))
        if new:
            w.writeheader()
        w.writerow(run)
    if a.keep_days:
        prune(out, day, a.keep_days)
    return 0


def prune(out: Path, day: str, keep_days: int):
    """Keep only the newest `keep_days` daily files per table (the git `data` branch is a rolling buffer)."""
    cutoff = (date.fromisoformat(day) - timedelta(days=keep_days - 1)).isoformat()
    for name in ('snapshot', 'histogram', 'monthly'):
        for f in (out / name).glob('*.parquet'):
            if f.stem < cutoff:
                f.unlink()
                print(f'pruned {f}')


if __name__ == '__main__':
    sys.exit(main())
