#!/usr/bin/env python3
"""Merge accepted Bedrock buggy-from-correct mutation pairs into V4.

Rows are generic bug-fix examples: the model sees buggy source and must return
corrected source. The preferred Bedrock bug category is retained only in
metadata for auditing, not in the training instruction/context.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V3_DIR = ROOT / "data" / "processed" / "v3"
V4_DIR = ROOT / "data" / "processed" / "v4"


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def assign_split(group_key: str, validation_pct: int = 20) -> str:
    return "validation" if (int(stable_hash(group_key), 16) % 100) < validation_pct else "train"


def strip_fence(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text


def fence_cpp(text: str) -> str:
    return "```cpp\n" + strip_fence(text).rstrip() + "\n```"


def row_identity(row: dict) -> str:
    return json.dumps(
        {
            "instruction": row.get("instruction"),
            "context": row.get("context"),
            "response": row.get("response"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def dedup_rows(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    kept: list[dict] = []
    for row in rows:
        key = row_identity(row)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def accepted_mutations(path: Path) -> list[dict]:
    by_source_id: dict[str, dict] = {}
    for row in load_jsonl(path):
        source_id = str(row.get("source_id") or "")
        if not source_id:
            continue
        if not (row.get("parse_ok") and row.get("buggy") and row.get("correct")):
            continue
        by_source_id[source_id] = row
    return list(by_source_id.values())


def make_v4_rows(mutation_path: Path) -> list[dict]:
    rows: list[dict] = []
    for mutation in accepted_mutations(mutation_path):
        source_id = str(mutation.get("source_id"))
        group_key = f"bedrock_mutation_pair:{source_id}"
        buggy = strip_fence(str(mutation.get("buggy") or ""))
        correct = strip_fence(str(mutation.get("correct") or ""))
        if not buggy or not correct or buggy == correct:
            continue
        rows.append({
            "instruction": "Fix this AIE mini-project. Return the corrected source.",
            "context": fence_cpp(buggy),
            "response": fence_cpp(correct),
            "metadata": {
                "variant": "v4_bedrock_buggy_from_compile_validated_correct",
                "split": assign_split(group_key),
                "group_id": group_key,
                "bug_type": "bedrock_mutated_bug",
                "bug_label": "Bedrock mutation of compile-validated AIE correct project",
                "source": "bedrock_buggy_from_compile_validated_correct",
                "source_id": source_id,
                "preferred_bug_type": mutation.get("preferred_bug_type"),
                "preferred_bug_label": mutation.get("preferred_bug_label"),
                "slug": mutation.get("slug"),
                "tier": mutation.get("tier"),
                "variant_idx": mutation.get("variant_idx"),
                "model": mutation.get("model"),
                "synthetic": True,
                "correct_compile_ok": True,
                "mutation_source": mutation.get("mutation_source"),
                "v4_bucket": "bedrock_mutated_bug_fix_pair",
            },
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    bucket_counts = Counter(str((row.get("metadata") or {}).get("v4_bucket") or "<none>") for row in rows)
    split_counts = Counter(str((row.get("metadata") or {}).get("split") or "<none>") for row in rows)
    variant_counts = Counter(str((row.get("metadata") or {}).get("variant") or "<none>") for row in rows)
    synthetic_counts = Counter("synthetic" if (row.get("metadata") or {}).get("synthetic") else "non_synthetic" for row in rows)
    return {
        "rows": len(rows),
        "bucket_counts": dict(bucket_counts),
        "split_counts": dict(split_counts),
        "variant_counts": dict(variant_counts),
        "synthetic_counts": dict(synthetic_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutations", default=str(V3_DIR / "bedrock_buggy_from_compile_validated_correct.jsonl"))
    parser.add_argument("--v4-dir", default=str(V4_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    v4_dir = Path(args.v4_dir)
    all_path = v4_dir / "aie_instruction_v4_all.jsonl"
    train_path = v4_dir / "aie_instruction_v4_train.jsonl"
    validation_path = v4_dir / "aie_instruction_v4_validation.jsonl"
    summary_path = v4_dir / "aie_instruction_v4_summary.json"

    existing_rows = load_jsonl(all_path)
    mutation_rows = make_v4_rows(Path(args.mutations))
    existing_dedup = dedup_rows(existing_rows)
    merged_rows = dedup_rows(existing_rows + mutation_rows)
    added_count = len(merged_rows) - len(existing_dedup)

    train_rows = [row for row in merged_rows if (row.get("metadata") or {}).get("split") == "train"]
    validation_rows = [row for row in merged_rows if (row.get("metadata") or {}).get("split") == "validation"]

    summary = {
        "existing_rows": len(existing_rows),
        "candidate_mutation_rows": len(mutation_rows),
        "added_mutation_rows": added_count,
        "all": summarize(merged_rows),
        "train": summarize(train_rows),
        "validation": summarize(validation_rows),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.dry_run:
        return

    write_jsonl(all_path, merged_rows)
    write_jsonl(train_path, train_rows)
    write_jsonl(validation_path, validation_rows)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
