"""Per-run health report: what changed on Djinni, what failed, what looks wrong.

Written to <out>/run_report.json (kept on the data branch), rendered to the
GitHub Actions job summary, and surfaced as ::warning:: / ::error:: annotations.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pandas as pd

from . import config

ROOT = Path(__file__).resolve().parents[1]
CONFIGURED = {'english_level': config.ENGLISH_LEVELS, 'region': config.REGIONS, 'work_format': config.WORK_FORMATS}
DROP_SHARE = 0.5   # L1 slice lost more than half its candidates day-over-day
DROP_MIN = 50      # ...and had at least this many before


def annotate(level: str, msg: str):
    print(f'::{level}::{msg}' if os.environ.get('GITHUB_ACTIONS') else f'[{level}] {msg}', flush=True)


def validate_filters(filters: dict) -> dict:
    """Configured values Djinni no longer offers. Djinni silently ignores unknown filter
    values (returns the unfiltered page), so these must be dropped, never fetched."""
    missing = {}
    for dim, vals in CONFIGURED.items():
        offered = set(filters.get(dim, {}))
        gone = [v for v in vals if v not in offered]
        if gone:
            missing[dim] = gone
            annotate('error', f'{dim} values {gone} not offered by Djinni any more (offered: {sorted(offered)}) '
                              f'- skipped; update config.py')
    return missing


def filter_changes(prev_path: Path, filters: dict) -> dict:
    if not prev_path.exists():
        return {}
    prev = json.loads(prev_path.read_text(encoding='utf-8'))
    out = {}
    for dim in sorted(set(prev) | set(filters)):
        a, b = prev.get(dim, {}), filters.get(dim, {})
        if isinstance(a, list):
            a = dict.fromkeys(a)
        if isinstance(b, list):
            b = dict.fromkeys(b)
        added = sorted(set(b) - set(a))
        removed = sorted(set(a) - set(b))
        renamed = {k: [a[k], b[k]] for k in set(a) & set(b) if a[k] and b[k] and a[k] != b[k]}
        if added or removed or renamed:
            out[dim] = {'added': added, 'removed': removed, 'renamed': renamed}
            annotate('warning', f'Djinni {dim} changed: +{added} -{removed} renamed={renamed}')
    return out


def unmapped(filters: dict) -> list:
    p = ROOT / 'mapping' / 'category_role_mapping.csv'
    if not p.exists():
        return []
    with p.open(encoding='utf-8', newline='') as f:
        known = {r['category'] for r in csv.DictReader(f) if r.get('mapped', 'True') == 'True'}
    return sorted(set(filters['category']) - known)


def drops(snaps: list, out: Path, day: str) -> list:
    prev_files = sorted(f for f in (out / 'snapshot').glob('*.parquet') if f.stem < day)
    if not prev_files:
        return []
    prev = pd.read_parquet(prev_files[-1])
    prev = prev[prev.level == 1].set_index(['category', 'exp']).candidates_online
    res = []
    for s in snaps:
        if s['level'] != 1:
            continue
        before = prev.get((s['category'], s['exp']))
        now = s.get('candidates_online') or 0
        if before is not None and before >= DROP_MIN and now < before * (1 - DROP_SHARE):
            res.append({'category': s['category'], 'exp': s['exp'], 'before': int(before), 'now': int(now)})
    if res:
        annotate('warning', f'{len(res)} category x exp slices lost >{DROP_SHARE:.0%} of candidates vs '
                            f'{prev_files[-1].stem}: {res[:5]}')
    return res


def metric_changes(snaps: list, out: Path, day: str) -> dict:
    """Which candidates metric(s) this run saw vs the previous file. Djinni silently redefined the
    headline candidates number once already (online -> active_4w, 2026-10-04); a change here means
    the candidates series breaks and day-over-day comparisons are meaningless."""
    now = sorted({s.get('candidates_metric') for s in snaps if s.get('candidates_metric')})
    res = {'now': now}
    unknown = [m for m in now if m.startswith('unknown:')]
    if unknown:
        annotate('error', f'unrecognised candidates card title(s) {unknown} - Djinni may have redefined the '
                          f'candidates number again; check the page and parse.CANDIDATE_METRICS')
    prev_files = sorted(f for f in (out / 'snapshot').glob('*.parquet') if f.stem < day)
    if prev_files:
        prev = pd.read_parquet(prev_files[-1])
        before = sorted(prev.candidates_metric.dropna().unique()) if 'candidates_metric' in prev else []
        res['before'] = before
        if before and before != now:
            annotate('warning', f'candidates metric changed {before} -> {now} vs {prev_files[-1].stem}: '
                                f'candidates_online is a different series from here on')
    return res


def write(out: Path, report: dict):
    (out / 'run_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary:
        return
    r = report
    lines = [f"## Djinni scrape {r['date']}: {'OK' if r['status'] == 'ok' else r['status'].upper()}", '',
             f"pages **{r['pages']}**, ok {r['ok']}, errors **{r['errors']}**, {r['seconds']} s, "
             f"breaker {'OPEN' if r['breaker_open'] else 'closed'}", '',
             '| level | fetched | expandable | errors |', '|---|---|---|---|']
    lines += [f"| L{k} | {v['fetched']} | {v['expandable']} | {v['errors']} |" for k, v in r['per_level'].items()]
    if r['config_values_missing']:
        lines += ['', f"**Configured filter values no longer on Djinni:** `{r['config_values_missing']}`"]
    if r['filter_changes']:
        lines += ['', '**Djinni filter/category changes vs previous run:**']
        lines += [f"- `{d}`: added {c['added']}, removed {c['removed']}, renamed {c['renamed']}"
                  for d, c in r['filter_changes'].items()]
    if r['unmapped_categories']:
        lines += ['', f"Unmapped categories ({len(r['unmapped_categories'])}): "
                      + ', '.join(f'`{c}`' for c in r['unmapped_categories'])]
    if r['candidate_drops']:
        lines += ['', f"**Sharp drops:** {len(r['candidate_drops'])} slices, e.g. {r['candidate_drops'][:5]}"]
    if r['error_samples']:
        lines += ['', '**Errors (first 20):**'] + [f"- `{e['url']}`: {e['error']}" for e in r['error_samples'][:20]]
    with open(summary, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
