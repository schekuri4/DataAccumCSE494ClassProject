import sys, json

rows = [json.loads(l) for l in sys.stdin if l.strip()]
ok = [r for r in rows if r.get("parse_ok") and r.get("quality_ok")]
fail = [r for r in rows if not r.get("parse_ok")]
models = set(r.get("model", "?") for r in rows)

print(f"Last {len(rows)} rows | parse_ok+quality_ok: {len(ok)} | parse_fail: {len(fail)} | models: {models}")
print()

for r in ok[:3]:
    slug = r["slug"]
    vi = r["variant_idx"]
    model = r.get("model", "?")
    print(f"=== {slug}  v={vi}  model={model} ===")
    print("-- BUGGY (first 30 lines) --")
    print("\n".join(r["buggy"].splitlines()[:30]))
    print()
    print("-- CORRECT diff --")
    bl = r["buggy"].splitlines()
    cl = r["correct"].splitlines()
    diffs = [(i, b, c) for i, (b, c) in enumerate(zip(bl, cl)) if b != c]
    if not diffs:
        print("  (no inline diff — length differs)")
        for i, line in enumerate(cl[len(bl):], start=len(bl)):
            print(f"  +line {i+1}: {line.strip()}")
    for i, b, c in diffs:
        print(f"  line {i+1}  BUGGY:   {b.strip()}")
        print(f"  line {i+1}  CORRECT: {c.strip()}")
    print()
