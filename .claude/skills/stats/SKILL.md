---
name: stats
description: Show summary statistics for the insect trade discovery outputs (discovered_shops.csv, platform_results.csv)
---

Run the following Python command and present the results as a clean summary:

```bash
python3 -c "
import csv, os
from collections import Counter

def summarise(path, domain_col='domain'):
    if not os.path.exists(path):
        return None
    rows = list(csv.DictReader(open(path, encoding='utf-8')))
    domains = [r.get(domain_col, '') for r in rows]
    engines = Counter(r.get('engine', '?') for r in rows)
    top = Counter(domains).most_common(10)
    return {'rows': len(rows), 'domains': len(set(d for d in domains if d)), 'engines': dict(engines), 'top10': top}

s = summarise('discovered_shops.csv')
if s:
    print(f'=== discovered_shops.csv ===')
    print(f'Unique domains : {s[\"domains\"]}')
    print(f'Total rows     : {s[\"rows\"]}')
    print(f'By engine      : {s[\"engines\"]}')
    print('Top 10 domains :')
    for d, n in s['top10']:
        print(f'  {n:3d}  {d}')

p = summarise('platform_results.csv', domain_col='platform')
if p:
    print(f'\n=== platform_results.csv ===')
    print(f'Platforms      : {p[\"domains\"]}')
    print(f'Total rows     : {p[\"rows\"]}')
    print('Top 10 platforms :')
    for d, n in p['top10']:
        print(f'  {n:3d}  {d}')
"
```
