import json, sys, collections

fname = sys.argv[1] if len(sys.argv) > 1 else '/tmp/validate_test_out.jsonl'

counts = collections.Counter()
with open(fname) as f:
    for line in f:
        r = json.loads(line)
        counts[(r['compile_ok'], r['error_class'])] += 1

print('=== Error class summary ===')
for k, v in sorted(counts.items()):
    print(f'  ok={k[0]} err_class={k[1]}: {v}')

print('\n=== First compile_error/api_mismatch error per row ===')
with open(fname) as f:
    for line in f:
        r = json.loads(line)
        if not r['compile_ok'] and r['error_class'] in ('compile_error', 'api_version_mismatch', 'aie_api_compile_error'):
            errs = [l for l in r.get('stderr_tail','').split('\n') if 'error:' in l][:1]
            first = errs[0][:120] if errs else '(no stderr error line)'
            print(f'  row {r["row_index"]} [{r["compiler"]}]: {first}')
            if not errs:
                so = r.get('stdout_tail', '')[:200]
                print(f'    stdout: {so!r}')

print('\n=== Rows with missing_dependency ===')
with open(fname) as f:
    for line in f:
        r = json.loads(line)
        if r.get('error_class') == 'missing_dependency':
            print(f'  row {r["row_index"]}: ok={r["compile_ok"]}')

