import json, collections, re

rows = [json.loads(l) for l in open(r'data/processed/aie_instruction_v2_all.jsonl','r',encoding='utf-8')]
print('total rows:', len(rows))

inst = collections.Counter(r['instruction'] for r in rows)
resp = collections.Counter(r['response'] for r in rows)
print('unique instructions:', len(inst), '| max repeat:', inst.most_common(1)[0][1])
print('unique responses:', len(resp))

for variant in ['bug_fix_pair','multi_file_bug_fix_pair','taxonomy_debug_scenario','taxonomy_multi_file_debug_scenario','taxonomy_inspection_negative','taxonomy_multi_file_inspection_negative']:
    sub = [r for r in rows if r['metadata'].get('variant') == variant]
    ic = collections.Counter(r['instruction'] for r in sub)
    rc = collections.Counter(r['response'] for r in sub)
    top_i = ic.most_common(1)[0][1] if ic else 0
    top_r = rc.most_common(1)[0][1] if rc else 0
    print(f'{variant}: rows={len(sub)}, uniq_inst={len(ic)} (top_repeat={top_i}), uniq_resp={len(rc)} (top_repeat={top_r})')

print()
print('inspection-negative per-bug per-instruction max repeat:')
for variant in ['taxonomy_inspection_negative','taxonomy_multi_file_inspection_negative']:
    sub = [r for r in rows if r['metadata'].get('variant') == variant]
    by_bug = collections.defaultdict(collections.Counter)
    for r in sub:
        by_bug[r['metadata'].get('bug_type')][r['instruction']] += 1
    worst = max((cc.most_common(1)[0][1] if cc else 0) for cc in by_bug.values())
    print(f'  {variant}: worst per-bug per-shape repeat = {worst}')

print()
slug_pat = re.compile(r'`[a-z][a-z0-9_]{4,}`')
leaks = sum(1 for r in rows if slug_pat.search(r.get('instruction') or ''))
print('rows with snake_case slug in instruction backticks:', leaks)

print()
print('sample inspection-negative instructions:')
sub = [r for r in rows if r['metadata'].get('variant') == 'taxonomy_inspection_negative']
for r in sub[:8]:
    print(' ', r['instruction'][:180])

print()
print('sample inspection-negative responses:')
for r in sub[:8]:
    print(' ', r['response'][:180])
