#!/usr/bin/env python3
"""Merge Bedrock compile-error rows into the V5 dataset files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V5_DIR = ROOT / "data" / "processed" / "v5"
DEFAULT_BEDROCK_ROWS = V5_DIR / "bedrock_compile_bug_rows_v5.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
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


def row_key(row: dict) -> str:
    metadata = row.get("metadata") or {}
    source_id = metadata.get("source_id")
    if source_id:
        return f"source_id:{source_id}"
    payload = json.dumps(
        {
            "instruction": row.get("instruction"),
            "context": row.get("context"),
            "response": row.get("response"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return "sha1:" + hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()


def is_valid_v5_row(row: dict) -> bool:
    metadata = row.get("metadata") or {}
    context = str(row.get("context") or "")
    response = str(row.get("response") or "")
    return (
        "--- Error Log ---" in context
        and response.startswith("--- a/aie_source\n+++ b/aie_source\n")
        and metadata.get("has_real_error_log") is True
        and metadata.get("response_format") == "unified_diff"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v5-dir", default=str(V5_DIR))
    parser.add_argument("--bedrock-rows", default=str(DEFAULT_BEDROCK_ROWS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    v5_dir = Path(args.v5_dir)
    all_path = v5_dir / "aie_instruction_v5_all.jsonl"
    train_path = v5_dir / "aie_instruction_v5_train.jsonl"
    validation_path = v5_dir / "aie_instruction_v5_validation.jsonl"

    existing = load_jsonl(all_path)
    existing_train = load_jsonl(train_path)
    existing_validation = load_jsonl(validation_path)
    bedrock = [row for row in load_jsonl(Path(args.bedrock_rows)) if is_valid_v5_row(row)]

    seen = {row_key(row) for row in existing}
    added: list[dict] = []
    for row in bedrock:
        key = row_key(row)
        if key in seen:
            continue
        seen.add(key)
        added.append(row)
    merged = existing + added

    train_added = [row for row in added if (row.get("metadata") or {}).get("split") == "train"]
    validation_added = [row for row in added if (row.get("metadata") or {}).get("split") == "validation"]
    train = existing_train + train_added
    validation = existing_validation + validation_added

    print(json.dumps({
        "existing_rows": len(existing),
        "existing_train_rows": len(existing_train),
        "existing_validation_rows": len(existing_validation),
        "bedrock_candidate_rows": len(load_jsonl(Path(args.bedrock_rows))),
        "bedrock_valid_rows": len(bedrock),
        "merged_rows": len(merged),
        "added_rows": len(added),
        "added_train_rows": len(train_added),
        "added_validation_rows": len(validation_added),
        "train_rows": len(train),
        "validation_rows": len(validation),
    }, indent=2))

    if args.dry_run:
        return

    write_jsonl(all_path, merged)
    write_jsonl(train_path, train)
    write_jsonl(validation_path, validation)


if __name__ == "__main__":
    main()
