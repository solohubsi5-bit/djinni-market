"""One-off: freeze the old solohubsi/testprHR1 history + seed the category->role mapping.

    python scripts/import_legacy.py <path-to-testprHR1-clone>

- data/legacy/salaries.parquet : the old `Salaries` sheet as-is (zeros preserved
  here; export.py blanks them in the compat output).
- mapping/category_role_mapping.csv : old HR11/mapp.py role_map with its bugs fixed,
  extended to every category Djinni currently lists (unmapped -> role 'Unknown').
"""
import ast
import csv
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
src = Path(sys.argv[1])

df = pd.read_excel(src / 'djinni_structured_tqdm.xlsx', sheet_name='Salaries')
df['scrape_date'] = df['scrape_date'].astype(str)
out = ROOT / 'data' / 'legacy' / 'salaries.parquet'
out.parent.mkdir(parents=True, exist_ok=True)
df.to_parquet(out, index=False, compression='zstd')
print(f'legacy: {len(df)} rows {df.scrape_date.min()}..{df.scrape_date.max()} -> {out}')

# role_map literal from the old script (parse, don't exec)
code = (src / 'HR11' / 'mapp.py').read_text(encoding='utf-8')
start = code.index('role_map = {')
end = code.index('}', start) + 1
role_map = ast.literal_eval(code[start + len('role_map = '):end])
# fixes vs. the old file
role_map['dev_ops'] = role_map.pop('devops')                    # scraper uses dev_ops; old map never matched
role_map['sales_manager'] = role_map['sales']                    # scraped but missing from the map
role_map['sales_leadership'] = role_map['sales']
# 'accountant' was defined twice; the effective (second) value was Finance/Finance - kept as-is

cats = json.loads((ROOT / 'data' / 'filters.json').read_text(encoding='utf-8'))['category']
rows = []
for code_, name in sorted(cats.items()):
    role, group, typ = role_map.get(code_, ('Unknown', 'Other', 'back_office'))
    rows.append({'category': code_, 'display_name': name, 'role': role, 'group': group, 'type': typ})
p = ROOT / 'mapping' / 'category_role_mapping.csv'
p.parent.mkdir(exist_ok=True)
with p.open('w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
unknown = [r['category'] for r in rows if r['role'] == 'Unknown']
print(f'mapping: {len(rows)} categories, {len(unknown)} unmapped: {unknown}')
