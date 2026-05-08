#!/usr/bin/env python3
"""Remove V4 mutation rows whose correct baseline was flagged as off-topic.

Off-topic baselines were scored 0 for keyword relevance by
scripts/screen_offtopic_correct_baselines.py.  Their source_ids are used to
find the corresponding mutation rows in V4 and remove them.

Usage:
    python scripts/remove_offtopic_mutations_from_v4.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
V3_DIR = ROOT / "data" / "processed" / "v3"
V4_DIR = ROOT / "data" / "processed" / "v4"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def stable_source_id(slug: str, variant_idx, correct: str) -> str:
    payload = json.dumps(
        {"slug": slug, "variant_idx": variant_idx, "correct": correct},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def assign_split(group_key: str, validation_pct: int = 20) -> str:
    return "validation" if (int(hashlib.sha1(group_key.encode()).hexdigest(), 16) % 100) < validation_pct else "train"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flagged", default=str(V3_DIR / "correct_baselines_offtopic.jsonl"))
    parser.add_argument("--v4-dir", default=str(V4_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    flagged_rows = load_jsonl(Path(args.flagged))
    # Build the set of source_ids for flagged correct baselines
    flagged_source_ids: set[str] = set()
    for row in flagged_rows:
        sid = row.get("source_id")
        if sid:
            flagged_source_ids.add(str(sid))
        else:
            # Recompute from slug/variant_idx/correct
            sid = stable_source_id(
                str(row.get("slug") or ""),
                row.get("variant_idx"),
                str(row.get("correct") or ""),
            )
            flagged_source_ids.add(sid)

    print(f"Flagged correct baseline source_ids: {len(flagged_source_ids)}")

    v4_dir = Path(args.v4_dir)
    all_path = v4_dir / "aie_instruction_v4_all.jsonl"
    train_path = v4_dir / "aie_instruction_v4_train.jsonl"
    validation_path = v4_dir / "aie_instruction_v4_validation.jsonl"
    summary_path = v4_dir / "aie_instruction_v4_summary.json"

    all_rows = load_jsonl(all_path)
    before = len(all_rows)

    removed = 0
    kept_rows: list[dict] = []
    removed_bucket: Counter = Counter()
    for row in all_rows:
        meta = row.get("metadata") or {}
        source_id = str(meta.get("source_id") or "")
        bucket = str(meta.get("v4_bucket") or "")
        # Only remove Bedrock mutation rows whose source_id is in the flagged set
        if bucket == "bedrock_mutated_bug_fix_pair" and source_id in flagged_source_ids:
            removed += 1
            removed_bucket[bucket] += 1
        else:
            kept_rows.append(row)

    train_rows = [r for r in kept_rows if (r.get("metadata") or {}).get("split") == "train"]
    validation_rows = [r for r in kept_rows if (r.get("metadata") or {}).get("split") == "validation"]

    print(f"V4 rows before: {before}")
    print(f"Rows removed (off-topic mutations): {removed}")
    print(f"V4 rows after: {len(kept_rows)}")
    print(f"Train: {len(train_rows)}  Validation: {len(validation_rows)}")

    buckets = Counter(str((r.get("metadata") or {}).get("v4_bucket") or "<none>") for r in kept_rows)
    print("\nBucket counts after cleanup:")
    for bucket, count in sorted(buckets.items(), key=lambda x: -x[1]):
        print(f"  {count:6d}  {bucket}")

    if args.dry_run:
        print("\n(dry run — no files written)")
        return

    write_jsonl(all_path, kept_rows)
    write_jsonl(train_path, train_rows)
    write_jsonl(validation_path, validation_rows)

    summary = {
        "rows_before_cleanup": before,
        "rows_removed_offtopic": removed,
        "all": {"rows": len(kept_rows)},
        "train": {"rows": len(train_rows)},
        "validation": {"rows": len(validation_rows)},
        "bucket_counts": dict(buckets),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nV4 artifacts updated.")


if __name__ == "__main__":
    main()
