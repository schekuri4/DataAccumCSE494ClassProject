#!/usr/bin/env python3
"""
split_fix_correctness.py
========================

Split a JSONL dataset into two files without modifying the original input:
1) correct-after-fix
2) not-correct-after-fix

Classification uses the same proof-style logic as validate_fix_proof_local.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_aie_compile import extract_buggy_code, extract_correct_code
from validate_fix_proof_local import evaluate_case


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="Input JSONL dataset")
    ap.add_argument("--out-correct", required=True, help="Output JSONL for correct-after-fix rows")
    ap.add_argument("--out-not-correct", required=True, help="Output JSONL for not-correct-after-fix rows")
    ap.add_argument("--max-anchors", type=int, default=12)
    ap.add_argument("--min-add-ratio", type=float, default=0.5)
    ap.add_argument("--min-del-absence-ratio", type=float, default=0.6)
    args = ap.parse_args(argv)

    in_path = Path(args.input)
    out_ok = Path(args.out_correct)
    out_bad = Path(args.out_not_correct)

    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    out_ok.parent.mkdir(parents=True, exist_ok=True)
    out_bad.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    ok_n = 0
    bad_n = 0

    with in_path.open("r", encoding="utf-8") as fp, \
        out_ok.open("w", encoding="utf-8") as f_ok, \
        out_bad.open("w", encoding="utf-8") as f_bad:

        for i, line in enumerate(fp):
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue

            md = row.get("metadata") or {}
            case_id = str(md.get("group_id") or f"v2:{i}")
            bug_type = str(md.get("bug_type") or md.get("category") or "unknown")
            buggy = extract_buggy_code(row) or ""
            fixed = extract_correct_code(row) or ""
            candidate = fixed

            # If extraction fails, classify as not-correct-after-fix.
            if not buggy.strip() or not fixed.strip():
                row_out = dict(row)
                row_out["fix_proof"] = {
                    "proof_fix_ok": False,
                    "reason": "missing_buggy_or_fixed_code",
                    "case_id": case_id,
                    "bug_type": bug_type,
                }
                f_bad.write(json.dumps(row_out, ensure_ascii=False) + "\n")
                bad_n += 1
                total += 1
                continue

            res = evaluate_case(
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

            row_out = dict(row)
            row_out["fix_proof"] = {
                "proof_fix_ok": bool(res.proof_fix_ok),
                "add_hit_ratio": res.add_hit_ratio,
                "del_absence_ratio": res.del_absence_ratio,
                "evidence": res.evidence,
                "changed_from_buggy": bool(res.changed_from_buggy),
                "case_id": res.case_id,
                "bug_type": res.bug_type,
            }

            if res.proof_fix_ok:
                f_ok.write(json.dumps(row_out, ensure_ascii=False) + "\n")
                ok_n += 1
            else:
                f_bad.write(json.dumps(row_out, ensure_ascii=False) + "\n")
                bad_n += 1

            total += 1
            if total % 1000 == 0:
                print(f"[split] processed={total} ok={ok_n} not_ok={bad_n}")

    print(f"[split] done total={total} ok={ok_n} not_ok={bad_n}")
    print(f"[split] correct-after-fix: {out_ok}")
    print(f"[split] not-correct-after-fix: {out_bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
