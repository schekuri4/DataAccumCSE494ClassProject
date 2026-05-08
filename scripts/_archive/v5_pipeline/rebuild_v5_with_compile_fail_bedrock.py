#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V5_DIR = ROOT / "data" / "processed" / "v5"

INSTRUCTION_VARIANTS = [
    "An AIE kernel or ADF graph has a hardware-specific error. The error log is in the context below. Return a unified diff that fixes the root cause - only changed lines, no full file.",
    "This AIE source triggers the error shown below. Identify the root cause and return a unified diff with the minimal fix.",
    "A Versal AIE build is failing with the error below. Return a unified diff that resolves it.",
    "The AIE code below produces the reported error. Return a unified diff showing only the lines that must change.",
    "Diagnose the AIE bug from the error log and return a unified diff with the surgical fix.",
    "The following AIE source has an architectural defect logged below. Return a unified diff - removals with -, additions with +.",
    "An xchesscc / aiesimulator error is shown below for this AIE source. Provide the unified diff that eliminates the root cause.",
    "This AIE kernel or graph has a bug manifesting as the error below. Return only the unified diff needed to fix it.",
]


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def stable_hex_source_id(row: dict) -> str:
    raw = str(row.get("source_id") or "").strip()
    if raw and all(ch in "0123456789abcdef" for ch in raw.lower()):
        return raw.lower()
    payload = json.dumps(
        {
            "slug": row.get("slug"),
            "tier": row.get("tier"),
            "variant_idx": row.get("variant_idx"),
            "buggy": row.get("buggy"),
            "correct": row.get("correct"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()


def format_error_log(row: dict) -> str:
    validation = row.get("buggy_compile_validation") or {}
    stderr = str(validation.get("stderr_tail") or "").strip()
    stdout = str(validation.get("stdout_tail") or "").strip()
    error_sig = str(row.get("error_signature") or "").strip()
    error_class = str(validation.get("error_class") or "compile_error").strip()

    text = stderr or stdout or error_sig or error_class
    lines: list[str] = []
    for line in text.splitlines():
        s = line.rstrip()
        if s:
            lines.append(s)
    if not lines:
        lines = ["compile_error"]
    return "\n".join(lines[:80])


def unified_diff_response(buggy: str, correct: str) -> str:
    lines = list(
        difflib.unified_diff(
            buggy.rstrip().splitlines(),
            correct.rstrip().splitlines(),
            fromfile="a/aie_source",
            tofile="b/aie_source",
            lineterm="",
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def split_for(source_id_hex: str) -> str:
    return "train" if (int(source_id_hex[:8], 16) % 100) >= 13 else "validation"


def instruction_for(source_id_hex: str) -> str:
    return INSTRUCTION_VARIANTS[int(source_id_hex[:8], 16) % len(INSTRUCTION_VARIANTS)]


def make_v5_row(raw: dict) -> dict:
    source_id = stable_hex_source_id(raw)
    buggy = str(raw.get("buggy") or "").rstrip()
    correct = str(raw.get("correct") or "").rstrip()
    context = f"{buggy}\n\n--- Error Log ---\n{format_error_log(raw)}"
    response = unified_diff_response(buggy, correct)
    split = split_for(source_id)

    return {
        "instruction": instruction_for(source_id),
        "context": context,
        "response": response,
        "metadata": {
            "variant": "v5_bedrock_compile_fail_only",
            "split": split,
            "group_id": f"bedrock_compile_bug:{source_id}",
            "bug_type": "bedrock_compile_bug",
            "bug_label": raw.get("label"),
            "source": "bedrock_compile_bug_from_compile_validated_correct",
            "source_id": source_id,
            "slug": raw.get("slug"),
            "tier": raw.get("tier"),
            "variant_idx": raw.get("variant_idx"),
            "model": raw.get("model"),
            "synthetic": True,
            "response_format": "unified_diff",
            "has_real_error_log": True,
            "correct_compile_ok": bool(raw.get("correct_compile_ok") is True),
            "buggy_compile_ok": False,
            "v5_bucket": "bedrock_compile_error_fix_pair",
            "mutation_source": raw.get("mutation_source"),
        },
    }


def row_has_required_format(row: dict) -> bool:
    context = str(row.get("context") or "")
    response = str(row.get("response") or "")
    return (
        "--- Error Log ---" in context
        and re.match(r"^--- a/aie_source\r?\n\+\+\+ b/aie_source\r?\n", response) is not None
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v5-dir", default=str(V5_DIR))
    parser.add_argument(
        "--raw-bedrock",
        default=str(V5_DIR / "bedrock_buggy_from_compile_validated_correct_v5.jsonl"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    v5_dir = Path(args.v5_dir)
    all_path = v5_dir / "aie_instruction_v5_all.jsonl"
    train_path = v5_dir / "aie_instruction_v5_train.jsonl"
    validation_path = v5_dir / "aie_instruction_v5_validation.jsonl"

    all_rows = load_jsonl(all_path)
    raw_rows = load_jsonl(Path(args.raw_bedrock))

    kept_non_bedrock = []
    removed_old_bedrock = 0
    for row in all_rows:
        src = str((row.get("metadata") or {}).get("source") or "")
        if src.startswith("bedrock"):
            removed_old_bedrock += 1
            continue
        kept_non_bedrock.append(row)

    new_bedrock = []
    for row in raw_rows:
        if row.get("buggy_compile_ok") is not False:
            continue
        if row.get("correct_compile_ok") is not True:
            continue
        candidate = make_v5_row(row)
        if not row_has_required_format(candidate):
            continue
        new_bedrock.append(candidate)

    merged = kept_non_bedrock + new_bedrock
    train_rows = [r for r in merged if (r.get("metadata") or {}).get("split") == "train"]
    validation_rows = [r for r in merged if (r.get("metadata") or {}).get("split") == "validation"]

    summary = {
        "all_existing": len(all_rows),
        "removed_existing_bedrock": removed_old_bedrock,
        "kept_non_bedrock": len(kept_non_bedrock),
        "raw_bedrock_rows": len(raw_rows),
        "new_bedrock_compile_fail_rows": len(new_bedrock),
        "merged_total": len(merged),
        "train_total": len(train_rows),
        "validation_total": len(validation_rows),
    }
    print(json.dumps(summary, indent=2))

    if args.dry_run:
        return

    write_jsonl(all_path, merged)
    write_jsonl(train_path, train_rows)
    write_jsonl(validation_path, validation_rows)


if __name__ == "__main__":
    main()
