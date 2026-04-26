#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_aie_compile import extract_buggy_code, extract_correct_code


def balanced_simple(code: str | None) -> bool:
    if not code:
        return False
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = set(pairs.values())
    closes = set(pairs.keys())
    for ch in code:
        if ch in opens:
            stack.append(ch)
        elif ch in closes:
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return not stack


def classify_row(row: dict, min_len: int) -> tuple[bool, list[str], str, str]:
    reasons: list[str] = []

    buggy = extract_buggy_code(row)
    correct = extract_correct_code(row)

    if not buggy:
        reasons.append("missing_buggy")
    if not correct:
        reasons.append("missing_correct")

    if buggy and len(buggy) < min_len:
        reasons.append("buggy_too_short")
    if correct and len(correct) < min_len:
        reasons.append("correct_too_short")

    if buggy and not balanced_simple(buggy):
        reasons.append("buggy_unbalanced_delimiters")
    if correct and not balanced_simple(correct):
        reasons.append("correct_unbalanced_delimiters")

    if buggy and "...existing code..." in buggy:
        reasons.append("buggy_placeholder_text")
    if correct and "...existing code..." in correct:
        reasons.append("correct_placeholder_text")

    keep = len(reasons) == 0
    return keep, reasons, buggy or "", correct or ""


def process(input_path: Path, out_keep: Path, out_drop: Path, min_len: int) -> None:
    out_keep.parent.mkdir(parents=True, exist_ok=True)
    out_drop.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    kept = 0
    dropped = 0
    reason_counts: dict[str, int] = {}

    with input_path.open("r", encoding="utf-8") as fp, \
        out_keep.open("w", encoding="utf-8") as okf, \
        out_drop.open("w", encoding="utf-8") as badf:

        for idx, line in enumerate(fp):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                dropped += 1
                total += 1
                entry = {
                    "line_no": idx,
                    "filter_reasons": ["json_decode_error"],
                    "raw": line,
                }
                badf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                reason_counts["json_decode_error"] = reason_counts.get("json_decode_error", 0) + 1
                continue

            keep, reasons, buggy, correct = classify_row(row, min_len=min_len)

            if keep:
                row_out = dict(row)
                row_out["whole_code_filter"] = {
                    "kept": True,
                    "reason": "ok",
                    "buggy_len": len(buggy),
                    "correct_len": len(correct),
                }
                okf.write(json.dumps(row_out, ensure_ascii=False) + "\n")
                kept += 1
            else:
                row_out = dict(row)
                row_out["whole_code_filter"] = {
                    "kept": False,
                    "reasons": reasons,
                    "buggy_len": len(buggy),
                    "correct_len": len(correct),
                }
                badf.write(json.dumps(row_out, ensure_ascii=False) + "\n")
                dropped += 1
                for r in reasons:
                    reason_counts[r] = reason_counts.get(r, 0) + 1

            total += 1
            if total % 1000 == 0:
                print(f"[filter] processed={total} kept={kept} dropped={dropped}")

    print(f"[filter] input={input_path}")
    print(f"[filter] total={total} kept={kept} dropped={dropped}")
    print(f"[filter] keep_out={out_keep}")
    print(f"[filter] drop_out={out_drop}")
    print(f"[filter] reason_counts={reason_counts}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Keep only rows with whole code pairs and isolate the rest.")
    ap.add_argument("--input", required=True, help="Input JSONL path")
    ap.add_argument("--out-keep", required=True, help="Output JSONL path for rows kept")
    ap.add_argument("--out-drop", required=True, help="Output JSONL path for rows dropped")
    ap.add_argument("--min-len", type=int, default=80, help="Minimum required length for buggy and correct code")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"input not found: {input_path}")

    process(
        input_path=input_path,
        out_keep=Path(args.out_keep),
        out_drop=Path(args.out_drop),
        min_len=args.min_len,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
