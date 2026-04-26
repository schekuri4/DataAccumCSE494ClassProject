#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def row_group_id(row: dict) -> str:
    meta = row.get("metadata", {}) or {}
    repo = str(meta.get("source_repo") or "local")
    src = str(meta.get("source_path") or meta.get("relative_path") or meta.get("source") or "")
    norm = src.replace("\\", "/").lower()
    norm = re.sub(r"_correct(?=\.)", "", norm)
    norm = re.sub(r"_buggy[^/.]*(?=\.)", "", norm)
    return f"{repo}:{norm}"


def row_key(row: dict) -> tuple[str, str, str]:
    meta = row.get("metadata", {}) or {}
    group_id = str(meta.get("group_id") or row_group_id(row))
    return (
        group_id,
        str(row.get("instruction") or ""),
        str(row.get("context") or ""),
    )


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge validated Bedrock fixes into the base V2 dataset.")
    ap.add_argument("--base", nargs="+", required=True, help="Base dataset JSONL files to merge in order.")
    ap.add_argument("--validated", nargs="+", required=True, help="Validated fix JSONL files whose rows replace base rows by key.")
    ap.add_argument("--out-all", required=True, help="Output merged all-rows JSONL path.")
    ap.add_argument("--out-train", default="", help="Optional train split output path.")
    ap.add_argument("--out-validation", default="", help="Optional validation split output path.")
    args = ap.parse_args()

    base_rows: list[dict] = []
    for p in args.base:
        rows = load_jsonl(Path(p))
        base_rows.extend(rows)

    replacements: dict[tuple[str, str, str], dict] = {}
    for p in args.validated:
        for row in load_jsonl(Path(p)):
            replacements[row_key(row)] = row

    merged: list[dict] = []
    replaced = 0
    seen_keys: set[tuple[str, str, str]] = set()
    for row in base_rows:
        key = row_key(row)
        seen_keys.add(key)
        replacement = replacements.get(key)
        if replacement is not None:
            merged.append(replacement)
            replaced += 1
        else:
            merged.append(row)

    appended = 0
    for key, row in replacements.items():
        if key not in seen_keys:
            merged.append(row)
            appended += 1

    write_jsonl(Path(args.out_all), merged)

    train_rows = [r for r in merged if (r.get("metadata", {}) or {}).get("split") == "train"]
    validation_rows = [r for r in merged if (r.get("metadata", {}) or {}).get("split") == "validation"]
    if args.out_train:
        write_jsonl(Path(args.out_train), train_rows)
    if args.out_validation:
        write_jsonl(Path(args.out_validation), validation_rows)

    print(f"Base rows: {len(base_rows)}")
    print(f"Validated replacements loaded: {len(replacements)}")
    print(f"Rows replaced in base: {replaced}")
    print(f"Validated rows appended (new keys): {appended}")
    print(f"Merged rows: {len(merged)}")
    print(f"Train rows: {len(train_rows)}")
    print(f"Validation rows: {len(validation_rows)}")
    print(f"Output all: {args.out_all}")
    if args.out_train:
        print(f"Output train: {args.out_train}")
    if args.out_validation:
        print(f"Output validation: {args.out_validation}")


if __name__ == "__main__":
    main()