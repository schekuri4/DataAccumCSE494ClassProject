#!/usr/bin/env python3
"""
validate_aie_windows_alt.py
===========================

Windows-friendly AIE fix validator when xchesscc/aiecompiler are unavailable.

This combines:
1) proof-style fix validation (did the candidate address buggy->fixed anchors?)
2) local logical plausibility validation (open-source C++ + AIE stubs)

Final status:
- pass: proof_fix_ok and logical_ok
- fail: otherwise
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_aie_compile import extract_buggy_code, extract_correct_code
from validate_fix_proof_local import evaluate_case
from validate_aie_logical_local import evaluate_one, find_cpp_compiler


def iter_rows(path: Path):
    with path.open("r", encoding="utf-8") as fp:
        for i, line in enumerate(fp):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", nargs="+", required=True, help="Input JSONL files")
    ap.add_argument("--out", required=True, help="Output JSONL with combined verdict")
    ap.add_argument("--limit", type=int, default=None, help="Optional max rows")
    ap.add_argument("--scope", choices=["correct", "buggy"], default="correct")
    ap.add_argument("--max-retries", type=int, default=6)
    ap.add_argument("--max-anchors", type=int, default=12)
    ap.add_argument("--min-add-ratio", type=float, default=0.5)
    ap.add_argument("--min-del-absence-ratio", type=float, default=0.6)
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    compiler_name, compiler_cmd = find_cpp_compiler()
    compiler_display = compiler_name or "<none>"
    print(f"[alt] host compiler: {compiler_display}")

    total = 0
    n_pass = 0
    n_fail = 0

    with out_path.open("w", encoding="utf-8") as ofp:
        for in_item in args.input:
            ip = Path(in_item)
            if not ip.exists():
                print(f"[alt] missing input: {ip}")
                continue

            for row_index, row in iter_rows(ip):
                if args.limit is not None and total >= args.limit:
                    break

                md = row.get("metadata") or {}
                bug_type = str(md.get("bug_type") or md.get("category") or "unknown")
                case_id = str(md.get("group_id") or f"{ip.name}:{row_index}")

                buggy = extract_buggy_code(row) or ""
                fixed = extract_correct_code(row) or ""
                candidate = fixed if args.scope == "correct" else buggy

                proof = evaluate_case(
                    case_id=case_id,
                    bug_type=bug_type,
                    buggy=buggy,
                    fixed=fixed,
                    candidate=candidate,
                    source_kind="v2-jsonl",
                    max_anchors=args.max_anchors,
                    min_add_ratio=args.min_add_ratio,
                    min_del_absence_ratio=args.min_del_absence_ratio,
                )

                logical = evaluate_one(
                    input_path=str(ip),
                    row_index=row_index,
                    scope=args.scope,
                    row=row,
                    compiler_name=compiler_display,
                    compiler_cmd=compiler_cmd,
                    retries=args.max_retries,
                )

                final_pass = bool(proof.proof_fix_ok and logical.logical_ok)
                if final_pass:
                    n_pass += 1
                else:
                    n_fail += 1

                out_row = {
                    "input_path": str(ip),
                    "row_index": row_index,
                    "scope": args.scope,
                    "case_id": case_id,
                    "bug_type": bug_type,
                    "final_pass": final_pass,
                    "proof_fix_ok": proof.proof_fix_ok,
                    "proof_evidence": proof.evidence,
                    "proof_add_hit_ratio": proof.add_hit_ratio,
                    "proof_del_absence_ratio": proof.del_absence_ratio,
                    "logical_ok": logical.logical_ok,
                    "logical_verdict": logical.verdict,
                    "logical_confidence": logical.confidence,
                    "logical_reason": logical.reason,
                    "compiler": compiler_display,
                }
                ofp.write(json.dumps(out_row, ensure_ascii=False) + "\n")
                total += 1

                if total % 100 == 0:
                    print(f"[alt] processed={total} pass={n_pass} fail={n_fail}")

            if args.limit is not None and total >= args.limit:
                break

    print(f"[alt] done total={total} pass={n_pass} fail={n_fail} out={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
