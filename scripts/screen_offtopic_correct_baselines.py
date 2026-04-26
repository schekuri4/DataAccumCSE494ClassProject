#!/usr/bin/env python3
"""Screen compile-validated correct baselines for topic relevance.

For each accepted correct row, scores how many of the slug's keyword tokens
appear in the code. Rows whose score falls below --min-score are flagged as
off-topic and written to a quarantine JSONL; the rest are written to the
kept JSONL.  Off-topic rows should be removed from V4 and their (slug,
variant_idx) pairs re-queued for regeneration.

Usage:
    python scripts/screen_offtopic_correct_baselines.py \\
        --input data/processed/v3/bedrock_expanded_topics_correct_compile_validated.jsonl \\
        --kept   data/processed/v3/correct_baselines_kept.jsonl \\
        --flagged data/processed/v3/correct_baselines_offtopic.jsonl \\
        --min-score 1
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


def slugify_tokens(slug: str) -> list[str]:
    """Split slug into meaningful keyword tokens (ignore common words)."""
    STOP = {
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "from",
        "is", "are", "has", "have", "be", "by", "as", "or", "and", "with",
        "not", "no", "do", "does", "vs", "via", "per", "its", "it",
        "where", "when", "that", "this", "which", "than", "after", "before",
        "between", "due", "used", "using", "can", "would", "but", "only",
        "each", "all", "one", "two", "both", "same", "wrong", "incorrect",
        "missing", "wrong", "bad", "causes", "cause", "caused", "into",
        "out", "too", "instead", "because", "already", "never", "then",
        "does", "did", "should", "also", "over", "under", "up", "down",
        "so", "more", "less", "if", "even", "just", "gets", "gets",
    }
    words = re.findall(r"[a-z][a-z0-9]*", slug.lower())
    # Keep tokens >=3 chars that are not stop words
    tokens = [w for w in words if len(w) >= 3 and w not in STOP]
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def score_relevance(slug: str, code: str) -> tuple[int, list[str], list[str]]:
    """Return (matched_count, matched_tokens, all_tokens)."""
    tokens = slugify_tokens(slug)
    code_lower = code.lower()
    matched = [t for t in tokens if t in code_lower]
    return len(matched), matched, tokens


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="data/processed/v3/bedrock_expanded_topics_correct_compile_validated.jsonl",
    )
    parser.add_argument(
        "--kept",
        default="data/processed/v3/correct_baselines_kept.jsonl",
    )
    parser.add_argument(
        "--flagged",
        default="data/processed/v3/correct_baselines_offtopic.jsonl",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=1,
        help="Minimum keyword token matches required to keep a row (default 1)",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Print stats without writing output files",
    )
    args = parser.parse_args()

    rows = load_jsonl(Path(args.input))
    ok_rows = [r for r in rows if r.get("compile_ok") is True and r.get("correct")]

    print(f"Loaded {len(rows)} rows, {len(ok_rows)} compile-ok with code")

    kept: list[dict] = []
    flagged: list[dict] = []
    score_dist: Counter = Counter()
    slug_flag_count: Counter = Counter()
    zero_score_slugs: set[str] = set()

    for row in ok_rows:
        slug = str(row.get("slug") or "")
        code = str(row.get("correct") or "")
        score, matched, tokens = score_relevance(slug, code)
        score_dist[score] += 1

        row_out = dict(row)
        row_out["_relevance_score"] = score
        row_out["_relevance_matched"] = matched
        row_out["_relevance_tokens"] = tokens

        if score >= args.min_score:
            kept.append(row_out)
        else:
            flagged.append(row_out)
            slug_flag_count[slug] += 1
            if score == 0:
                zero_score_slugs.add(slug)

    print(f"\nRelevance score distribution (token hits in code):")
    for s in sorted(score_dist):
        print(f"  score={s:2d}: {score_dist[s]:5d} rows")

    print(f"\nKept (score >= {args.min_score}): {len(kept)}")
    print(f"Flagged (score < {args.min_score}): {len(flagged)}")
    print(f"Slugs with zero-score rows: {len(zero_score_slugs)}")

    if flagged:
        print(f"\nTop 20 slugs by flagged row count:")
        for slug, count in slug_flag_count.most_common(20):
            total = sum(1 for r in ok_rows if r.get("slug") == slug)
            print(f"  {count:4d}/{total}  {slug}")

    if args.stats_only:
        return

    write_jsonl(Path(args.kept), [
        {k: v for k, v in r.items() if not k.startswith("_")} for r in kept
    ])
    write_jsonl(Path(args.flagged), flagged)

    print(f"\nWrote {len(kept)} rows -> {args.kept}")
    print(f"Wrote {len(flagged)} rows -> {args.flagged}")


if __name__ == "__main__":
    main()
