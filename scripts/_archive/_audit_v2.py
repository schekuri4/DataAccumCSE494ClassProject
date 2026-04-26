import json, re, collections

rows = [json.loads(l) for l in open(r'data/processed/aie_instruction_v2_all.jsonl','r',encoding='utf-8')]
print('total:', len(rows))

has_source_eq = 0
graph_primary = 0
kernel_primary = 0
source_with_string = 0
for r in rows:
    ctx = (r.get('context') or '') + '\n' + (r.get('response') or '')
    rp = (r.get('metadata') or {}).get('relative_path') or ''
    if rp.endswith(('.h','.hpp')):
        graph_primary += 1
    elif rp.endswith(('.cc','.cpp')):
        kernel_primary += 1
    if re.search(r'adf::source\s*\(', ctx):
        has_source_eq += 1
        if re.search(r'adf::source\s*\([^)]*\)\s*=\s*"', ctx):
            source_with_string += 1

print('graph-file primary (.h/.hpp):', graph_primary)
print('kernel-file primary (.cc/.cpp):', kernel_primary)
print('rows touching adf::source(...):', has_source_eq)
print('rows with adf::source(k)="string":', source_with_string)

# Bug types relevant to the 7 complaints
pat_bugs = ['accumulator','acc48','acc80','missing.*source','source.*missing',
            'connect.*direction','reversed','stale','iter','runtime.?ratio',
            'window.*byte','plio','template.*drop']
bt = collections.Counter()
for r in rows:
    b = (r.get('metadata') or {}).get('bug_type') or ''
    for p in pat_bugs:
        if re.search(p, b, re.I):
            bt[b] += 1
print()
print('bug types matching eval complaints:')
for k,v in bt.most_common():
    print(f'  {k}: {v}')

# check symmetric-bug coverage: responses where the same pattern appears twice
# e.g. two connect<...> lines
double_connect = 0
for r in rows:
    resp = r.get('response') or ''
    if resp.count('connect<') >= 2:
        double_connect += 1
print()
print('responses with >=2 connect<...> lines (symmetric-fix coverage):', double_connect)

# iterator begin_vector examples
begin_vec = sum(1 for r in rows if 'begin_vector' in (r.get('response') or ''))
print('responses containing begin_vector:', begin_vec)
