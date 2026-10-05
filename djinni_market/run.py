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
from zoneinfo import ZoneInfo

import pandas as pd
from bs4 import BeautifulSoup

from . import config, fetch, parse, report, slices

SNAPSHOT_COLS = slices.KEYS + ('level',)


def scrape_one(s: dict):
    params = {k: s[k] for k in slices.KEYS}
    try:
        return s, parse.parse_page(fetch.get(params))
    except Exception as e:  # noqa: BLE001 - re-raised with the URL attached
        raise RuntimeError(json.dumps({'url': fetch.url_for(params), 'error': f'{type(e).__name__}: {e}'},
                                      ensure_ascii=False)) from e


def fetch_all(todo, ex, t0, label):
    done, errors = [], []
    futs = [ex.submit(scrape_one, s) for s in todo]
    for n, f in enumerate(as_completed(futs), 1):
        try:
            done.append(f.result())
        except RuntimeError as e:
            errors.append(json.loads(str(e)))
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


KYIV = ZoneInfo('Europe/Kyiv')
RUNS_FIELDS = ('date', 'finished_utc', 'status', 'pages', 'ok', 'errors', 'seconds', 'per_level')


def run_date(now: datetime | None = None) -> str:
    """Label of a run = Europe/Kyiv calendar date at which it STARTS.

    The nightly cron fires 23:30 UTC = 01:30/02:30 Kyiv, i.e. already the next Kyiv day, so the
    old `date.today()` (UTC on the runner) labelled it with the previous day and overwrote that
    day's earlier run; a GitHub-delayed run (10-03's fired at 10-04 03:00 UTC) was then overwritten
    by the next night's. Kyiv start date gives one label per real day unless a run slips by ~a day.
    Djinni's own `calculated_at` is no use as a label: pages are recomputed lazily and one run's
    values span ~24 h of calculated_at."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(KYIV).date().isoformat()


def write_run_row(path: Path, row: dict):
    """runs.csv keeps ONE row per date: a rerun of the same date replaces its row. Rewrites the file
    with the canonical header; legacy 7-field rows (no status column) get a derived status."""
    rows = []
    if path.exists():
        with path.open(encoding='utf-8', newline='') as f:
            r = csv.reader(f)
            next(r, None)
            for old in r:
                if not old:
                    continue
                if len(old) == 7:   # pre-status format: date,finished_utc,pages,ok,errors,seconds,per_level
                    ok = old[4] == '0' and old[3] not in ('', '0')
                    old = old[:2] + ['ok' if ok else 'failed'] + old[2:]
                rows.append(dict(zip(RUNS_FIELDS, old)))
    rows = [r for r in rows if r['date'] != row['date']] + [row]
    rows.sort(key=lambda r: r['date'])
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(RUNS_FIELDS))
        w.writeheader()
        w.writerows(rows)


def prune(out: Path, day: str, keep_days: int):
    """Keep only the newest `keep_days` daily files per table (the git `data` branch is a rolling buffer)."""
    cutoff = (date.fromisoformat(day) - timedelta(days=keep_days - 1)).isoformat()
    for name in ('snapshot', 'histogram', 'monthly'):
        for f in (out / name).glob('*.parquet'):
            if f.stem < cutoff:
                f.unlink()
                print(f'pruned {f}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-level', type=int, default=slices.MAX_LEVEL)
    ap.add_argument('--categories', default='', help='comma list, restricts L1 (smoke test)')
    ap.add_argument('--out', default='data')
    ap.add_argument('--date', default=None,
                    help='label for this run; default = Europe/Kyiv calendar date of the run START (see run_date)')
    ap.add_argument('--force', action='store_true', help='overwrite an existing day')
    ap.add_argument('--keep-days', type=int, default=0, help='after writing, delete daily files older than N days')
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    day = a.date or run_date()
    if (out / 'snapshot' / f'{day}.parquet').exists() and not a.force:
        print(f'{day} already collected - nothing to do')
        return 0

    t0 = time.time()
    filters = parse.parse_filters(BeautifulSoup(fetch.get({'category': 'python'}), 'html.parser'))
    missing = report.validate_filters(filters)
    for dim, gone in missing.items():   # never send a value Djinni would silently ignore
        setattr(config, {'english_level': 'ENGLISH_LEVELS', 'region': 'REGIONS',
                         'work_format': 'WORK_FORMATS'}[dim],
                tuple(v for v in report.CONFIGURED[dim] if v not in gone))
    changes = report.filter_changes(out / 'filters.json', filters)

    todo = slices.level1(filters)
    if a.categories:
        keep = set(a.categories.split(','))
        unknown = sorted(keep - set(filters['category']))
        if unknown:
            report.annotate('error', f'--categories not on Djinni: {unknown}')
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
                report.annotate('error', 'circuit breaker open (repeated 403/429) - Djinni may be blocking us; '
                                         'stopping early')
                break
            level += 1
            if level > a.max_level:
                break
            todo = [c for pl in slices.parent_levels(level) for s in expand.get(pl, [])
                    for c in slices.children(s, level)]

    total = sum(v['fetched'] for v in per_level.values())
    too_many = len(errors) / max(total, 1) > config.MAX_ERROR_RATE
    snaps = [{**{k: s[k] for k in SNAPSHOT_COLS}, **p['snapshot']} for s, p in results]
    rep = {
        'date': day, 'finished_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'status': 'failed' if too_many else ('partial' if fetch.breaker_open() else 'ok'),
        'pages': total, 'ok': len(results), 'errors': len(errors), 'seconds': round(time.time() - t0),
        'breaker_open': fetch.breaker_open(), 'per_level': per_level,
        'config_values_missing': missing, 'filter_changes': changes,
        'unmapped_categories': report.unmapped(filters),
        'candidate_drops': report.drops(snaps, out, day),
        'candidates_metric': report.metric_changes(snaps, out, day),
        'error_kinds': pd.Series([e['error'].split(':')[0] for e in errors]).value_counts().to_dict() if errors else {},
        'error_samples': errors[:200],
    }
    report.write(out, rep)
    print(json.dumps({k: rep[k] for k in ('date', 'status', 'pages', 'ok', 'errors', 'seconds', 'error_kinds')}))
    if too_many:
        report.annotate('error', f'error rate {len(errors)}/{total} above {config.MAX_ERROR_RATE:.0%} - '
                                 f'nothing written; see run_report.json / job summary')
        return 1

    hists, months = [], []
    for s, p in results:
        if s['level'] in config.DETAIL_LEVELS:
            key = {k: s[k] for k in SNAPSHOT_COLS}
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
    row = {k: rep[k] for k in ('date', 'finished_utc', 'status', 'pages', 'ok', 'errors', 'seconds')}
    row['per_level'] = json.dumps(per_level)
    write_run_row(out / 'runs.csv', row)
    if a.keep_days:
        prune(out, day, a.keep_days)
    return 0


if __name__ == '__main__':
    sys.exit(main())
