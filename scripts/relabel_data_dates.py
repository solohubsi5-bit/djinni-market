"""One-off: re-label `data` branch files that were written under the old UTC `date.today()` label.

Until fix-dates-values a run was labelled with the UTC date on the runner, so the 23:30 UTC nightly
run got the PREVIOUS Kyiv day's label and overwrote that day's earlier run. The new rule
(run.run_date) is: label = Europe/Kyiv date of the run START. This script applies that rule to the
files already on the `data` branch.

For every snapshot/<D>.parquet it finds the run that wrote it (the last runs.csv row labelled D),
computes that run's Kyiv start date (finished_utc - seconds) and, if it differs, moves
snapshot/histogram/monthly/<D>.parquet -> <new>.parquet with scrape_date rewritten. Evidence check:
the file's latest Djinni `calculated_at` (Kyiv wall time) must not be later than the new label's
day - a file containing data calculated after its old label's day is provably mislabelled.
runs.csv is rewritten one row per (new) date, last run wins; run_report.json `date` and
categories.csv `last_seen` are mapped too. Dry run unless --apply. Never touches git.

    python scripts/relabel_data_dates.py --data <checkout of the data branch>          # plan only
    python scripts/relabel_data_dates.py --data <checkout of the data branch> --apply
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djinni_market.run import RUNS_FIELDS, run_date  # noqa: E402

TABLES = ('snapshot', 'histogram', 'monthly')


def read_runs(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding='utf-8', newline='') as f:
        r = csv.reader(f)
        next(r, None)
        for old in r:
            if not old:
                continue
            if len(old) == 7:
                ok = old[4] == '0' and old[3] not in ('', '0')
                old = old[:2] + ['ok' if ok else 'failed'] + old[2:]
            row = dict(zip(RUNS_FIELDS, old))
            start = datetime.fromisoformat(row['finished_utc']) - timedelta(seconds=int(row['seconds']))
            row['new_date'] = run_date(start)
            rows.append(row)
    return rows


def plan(data: Path) -> tuple[dict, list[dict]]:
    runs = read_runs(data / 'runs.csv')
    mapping = {}
    for f in sorted((data / 'snapshot').glob('*.parquet')):
        d = f.stem
        writers = sorted((r for r in runs if r['date'] == d), key=lambda r: r['finished_utc'])
        if not writers:
            print(f'{d}: no runs.csv row - left as is')
            continue
        w = writers[-1]
        snap = pd.read_parquet(f, columns=['calculated_at'])
        calc_max = str(snap.calculated_at.dropna().max())[:19]
        new = w['new_date']
        print(f'{d}: written by run finished {w["finished_utc"]} after {w["seconds"]} s -> Kyiv start date {new}; '
              f'latest calculated_at (Kyiv) {calc_max}')
        if new == d:
            continue
        if calc_max[:10] > new:
            raise SystemExit(f'{d}: calculated_at {calc_max} is after the new label {new} - refusing')
        mapping[d] = new
    targets = list(mapping.values())
    if len(set(targets)) != len(targets):
        raise SystemExit(f'two files map to the same date: {mapping}')
    for old, new in mapping.items():
        if (data / 'snapshot' / f'{new}.parquet').exists() and new not in mapping:
            raise SystemExit(f'{old} -> {new}: target already exists - refusing to overwrite')
    return mapping, runs


def apply(data: Path, mapping: dict, runs: list[dict]):
    # move in an order that never clobbers a file still waiting to be moved
    staged = []
    for old, new in mapping.items():
        for t in TABLES:
            src = data / t / f'{old}.parquet'
            if not src.exists():
                continue
            df = pd.read_parquet(src)
            df['scrape_date'] = new
            tmp = data / t / f'{new}.parquet.relabel'
            df.to_parquet(tmp, index=False, compression='zstd')
            staged.append((src, tmp, data / t / f'{new}.parquet'))
    for src, _, _ in staged:
        src.unlink()
    for _, tmp, dst in staged:
        tmp.replace(dst)
        print(f'wrote {dst}')

    last = {}
    for r in sorted(runs, key=lambda r: r['finished_utc']):
        last[r['new_date']] = {**{k: r[k] for k in RUNS_FIELDS}, 'date': r['new_date']}
    with (data / 'runs.csv').open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(RUNS_FIELDS))
        w.writeheader()
        w.writerows(last[d] for d in sorted(last))
    print(f'runs.csv: {len(runs)} rows -> {len(last)} (one per Kyiv day)')

    rep = data / 'run_report.json'
    if rep.exists():
        r = json.loads(rep.read_text(encoding='utf-8'))
        r['date'] = mapping.get(r.get('date'), r.get('date'))
        rep.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding='utf-8')
    cats = data / 'categories.csv'
    if cats.exists():
        with cats.open(encoding='utf-8', newline='') as f:
            rows = list(csv.DictReader(f))
        for r in rows:   # first_seen kept: it may come from an earlier, correctly-labelled run
            r['last_seen'] = mapping.get(r['last_seen'], r['last_seen'])
        with cats.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['category', 'display_name', 'first_seen', 'last_seen'])
            w.writeheader()
            w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True, type=Path)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)
    mapping, runs = plan(a.data)
    print('relabel:', mapping or 'nothing to do')
    if a.apply and mapping:
        apply(a.data, mapping, runs)
    elif mapping:
        print('dry run - rerun with --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
