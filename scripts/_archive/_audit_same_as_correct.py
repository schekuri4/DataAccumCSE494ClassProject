#!/usr/bin/env python3
"""Audit buggy_same_as_correct rejects to find suspicious correct baselines."""
import json
from pathlib import Path
from collections import Counter

src = Path("data/processed/v3/bedrock_buggy_from_compile_validated_correct.jsonl")
rows = [json.loads(line) for line in src.open(encoding="utf-8") if line.strip()]

same_as_correct = [
    r for r in rows
    if not r.get("parse_ok")
    and "buggy_same_as_correct" in ((r.get("full_code_reasons") or {}).get("buggy", []))
]

by_slug = Counter(r.get("slug") for r in same_as_correct)
all_by_slug = Counter(r.get("slug") for r in rows)
accepted_by_slug = Counter(
    r.get("slug") for r in rows
    if r.get("parse_ok") and r.get("buggy") and r.get("correct")
)

print("Total rows in mutation file:", len(rows))
print("Total buggy_same_as_correct rejects:", len(same_as_correct))
print("Unique slugs affected:", len(by_slug))

print("\nTop 25 slugs by same-as-correct reject count:")
for slug, count in by_slug.most_common(25):
    total = all_by_slug.get(slug, 1)
    acc = accepted_by_slug.get(slug, 0)
    print("  %4d/%d attempted  %3d accepted  %s" % (count, total, acc, slug))

suspicious = []
for slug, bad in by_slug.items():
    total = all_by_slug.get(slug, 1)
    pct = bad / total
    if pct > 0.6:
        suspicious.append((slug, bad, total, pct))

suspicious.sort(key=lambda x: -x[3])
print("\nSlugs where >60%% of attempts were same-as-correct (likely pre-baked bug):")
for slug, bad, total, pct in suspicious[:40]:
    print("  %3.0f%%  (%d/%d)  %s" % (pct * 100, bad, total, slug))
print("\nTotal suspicious slugs (>60%%):", len(suspicious))
print("Total suspicious slugs (>80%%):", sum(1 for _, _, _, p in suspicious if p > 0.8))

# Show one example correct code for the most suspicious slug
if suspicious:
    worst_slug = suspicious[0][0]
    example = next(
        (r for r in rows if r.get("slug") == worst_slug and r.get("correct")),
        None
    )
    if example:
        print("\n--- Example correct baseline for most suspicious slug: %s ---" % worst_slug)
        print(str(example.get("correct") or "")[:2500])
