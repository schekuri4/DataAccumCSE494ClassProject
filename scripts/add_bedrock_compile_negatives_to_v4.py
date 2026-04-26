#!/usr/bin/env python3
"""Add failed Bedrock compile-tested candidates to the V4 dataset as negatives.

The successful Bedrock fixes are already included by build_verified_v4_dataset.py
via bedrock_fixed_synth_validated.jsonl / bedrock_fixed_real_validated.jsonl.
This script preserves those rows and appends unique failed Bedrock candidate
attempts as compiler-error inspection negatives.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
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


def compact_error(text: str, max_chars: int = 900) -> str:
    lines: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "error:" in stripped or "ERROR:" in stripped or "fatal error:" in stripped:
            lines.append(stripped)
        elif not lines and len(lines) < 3:
            lines.append(stripped)
        if len("\n".join(lines)) >= max_chars:
            break
    if not lines:
        lines = [str(text or "compile failed").strip()]
    return "\n".join(lines)[:max_chars]


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


def make_negative_rows(attempt_log: Path, max_rows: int = 0) -> list[dict]:
    rows: list[dict] = []
    seen_candidates: set[str] = set()
    for attempt in load_jsonl(attempt_log):
        if attempt.get("compile_ok") is True:
            continue
        candidate_code = str(attempt.get("candidate_code") or "").strip()
        if not candidate_code:
            continue
        digest = stable_hash(candidate_code)
        if digest in seen_candidates:
            continue
        seen_candidates.add(digest)

        error_class = str(attempt.get("validation_error_class") or "compile_error")
        error_excerpt = compact_error(
            attempt.get("validation_error_excerpt")
            or attempt.get("prompt_error_excerpt")
            or error_class
        )
        source_kind = str(attempt.get("source_kind") or "bedrock")
        row_index = attempt.get("row_index")
        attempt_no = attempt.get("attempt")
        bug_type = "bedrock_compile_failed_candidate"
        group_key = f"bedrock_failed_candidate:{digest}"
        rows.append({
            "instruction": "Inspect this AI-generated AIE source and say whether it is compile-ready.",
            "context": candidate_code,
            "response": (
                "No - this AI-generated AIE source is not compile-ready. "
                f"The validator reported {error_class}: {error_excerpt}"
            ),
            "metadata": {
                "variant": "v4_bedrock_compile_failure_negative",
                "split": assign_split(group_key),
                "group_id": group_key,
                "bug_type": bug_type,
                "bug_label": "Bedrock generated AIE candidate failed compile validation",
                "source": "bedrock_fix_attempt_log",
                "source_kind": source_kind,
                "row_index": row_index,
                "attempt": attempt_no,
                "file_type": attempt.get("file_type"),
                "target": attempt.get("target"),
                "synthetic": True,
                "verdict": "compile_failed",
                "compile_ok": False,
                "error_class": error_class,
                "error_reason": error_excerpt,
                "v4_bucket": "bedrock_compile_failure_negative",
                "candidate_sha1": digest,
            },
        })
        if max_rows > 0 and len(rows) >= max_rows:
            break
    return rows


def summarize(rows: list[dict]) -> dict:
    bucket_counts = Counter(str((row.get("metadata") or {}).get("v4_bucket") or "<none>") for row in rows)
    split_counts = Counter(str((row.get("metadata") or {}).get("split") or "<none>") for row in rows)
    variant_counts = Counter(str((row.get("metadata") or {}).get("variant") or "<none>") for row in rows)
    return {
        "rows": len(rows),
        "bucket_counts": dict(bucket_counts),
        "split_counts": dict(split_counts),
        "variant_counts": dict(variant_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-log", default=str(V3_DIR / "bedrock_fix_attempt_log.jsonl"))
    parser.add_argument("--v4-dir", default=str(V4_DIR))
    parser.add_argument("--max-negative-rows", type=int, default=0, help="0 means no cap")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    v4_dir = Path(args.v4_dir)
    all_path = v4_dir / "aie_instruction_v4_all.jsonl"
    train_path = v4_dir / "aie_instruction_v4_train.jsonl"
    validation_path = v4_dir / "aie_instruction_v4_validation.jsonl"
    summary_path = v4_dir / "aie_instruction_v4_summary.json"

    existing_rows = load_jsonl(all_path)
    negative_rows = make_negative_rows(Path(args.attempt_log), args.max_negative_rows)
    merged_rows = dedup_rows(existing_rows + negative_rows)
    added_count = len(merged_rows) - len(dedup_rows(existing_rows))

    train_rows = [row for row in merged_rows if (row.get("metadata") or {}).get("split") == "train"]
    validation_rows = [row for row in merged_rows if (row.get("metadata") or {}).get("split") == "validation"]

    summary = {
        "existing_rows": len(existing_rows),
        "candidate_negative_rows": len(negative_rows),
        "added_negative_rows": added_count,
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
