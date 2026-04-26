#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from validate_aie_compile import extract_buggy_code, extract_correct_code


def bal_simple(s: str | None) -> bool:
    if not s:
        return False
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = set(pairs.values())
    closes = set(pairs.keys())
    for ch in s:
        if ch in opens:
            stack.append(ch)
        elif ch in closes:
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return not stack


def audit(path: Path, min_len: int) -> None:
    rows = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    bad = collections.Counter()
    by_variant = collections.Counter()
    by_source = collections.Counter()
    examples: list[tuple[int, str, str, str, str]] = []

    for i, row in enumerate(rows):
        md = row.get("metadata") or {}
        variant = md.get("variant", "unknown")
        source_kind = "synthetic" if md.get("synthetic") else "real"

        buggy = extract_buggy_code(row)
        corr = extract_correct_code(row)

        if not buggy or not corr:
            bad["missing_buggy_or_correct"] += 1
            by_variant[variant] += 1
            by_source[source_kind] += 1
            if len(examples) < 10:
                examples.append((i, variant, source_kind, "missing", str(md.get("source", ""))[:120]))
            continue

        if len(buggy) < min_len or len(corr) < min_len:
            bad["tiny_buggy_or_correct"] += 1
            by_variant[variant] += 1
            by_source[source_kind] += 1
            if len(examples) < 10:
                examples.append((i, variant, source_kind, "tiny", str(md.get("source", ""))[:120]))

        if not bal_simple(buggy) or not bal_simple(corr):
            bad["unbalanced_delimiters_simple"] += 1
            by_variant[variant] += 1
            by_source[source_kind] += 1
            if len(examples) < 10:
                examples.append((i, variant, source_kind, "unbalanced", str(md.get("source", ""))[:120]))

        if "...existing code..." in buggy or "...existing code..." in corr:
            bad["placeholder_text"] += 1
            by_variant[variant] += 1
            by_source[source_kind] += 1

    n = len(rows)
    pct = {k: round(v / n * 100, 2) for k, v in bad.items()} if n else {}

    print("total_rows", n)
    print("bad_counts", dict(bad))
    print("bad_pct", pct)
    print("top_variants", by_variant.most_common(12))
    print("source_split", dict(by_source))
    print("examples", examples)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Audit dataset for missing/incomplete code pairs")
    ap.add_argument("--input", required=True, help="Input JSONL file")
    ap.add_argument("--min-len", type=int, default=80, help="Minimum code length per side")
    args = ap.parse_args()
    audit(Path(args.input), args.min_len)
