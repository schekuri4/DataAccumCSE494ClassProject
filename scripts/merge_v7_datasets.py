#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Merge multiple v7 dataset directories into one deduplicated dataset.")
    ap.add_argument("--input-dir", action="append", required=True, help="Dataset directory containing aie_instruction_v7_all.jsonl")
    ap.add_argument("--out-dir", required=True, help="Output directory for merged v7 dataset files")
    return ap.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(path)


def row_key(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    payload = {
        "source": metadata.get("source"),
        "group_id": metadata.get("group_id"),
        "context": row.get("context"),
        "response": row.get("response"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_summary(rows: list[dict[str, Any]], input_dirs: list[str]) -> dict[str, Any]:
    return {
        "total_rows": len(rows),
        "train_rows": sum(1 for row in rows if (row.get("metadata") or {}).get("split") == "train"),
        "validation_rows": sum(1 for row in rows if (row.get("metadata") or {}).get("split") == "validation"),
        "unique_bug_types": sorted({bug for row in rows for bug in (row.get("metadata") or {}).get("bug_types", [])}),
        "canonical": True,
        "inputs": input_dirs,
    }


def main() -> int:
    args = parse_args()
    cwd = Path.cwd().resolve()
    input_dirs = []
    for path in args.input_dir:
        resolved = Path(path).resolve()
        try:
            input_dirs.append(resolved.relative_to(cwd).as_posix())
        except ValueError:
            input_dirs.append(str(resolved))
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for input_dir in args.input_dir:
        all_path = Path(input_dir) / "aie_instruction_v7_all.jsonl"
        for row in read_jsonl(all_path):
            key = row_key(row)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows = merged
    train_rows = [row for row in merged if (row.get("metadata") or {}).get("split") == "train"]
    validation_rows = [row for row in merged if (row.get("metadata") or {}).get("split") == "validation"]

    write_jsonl(out_dir / "aie_instruction_v7_all.jsonl", all_rows)
    write_jsonl(out_dir / "aie_instruction_v7_train.jsonl", train_rows)
    write_jsonl(out_dir / "aie_instruction_v7_validation.jsonl", validation_rows)
    (out_dir / "manifest_summary.json").write_text(
        json.dumps(build_summary(merged, input_dirs), indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"merged_rows": len(all_rows), "train_rows": len(train_rows), "validation_rows": len(validation_rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())