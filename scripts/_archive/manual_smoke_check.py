#!/usr/bin/env python3
import json
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from bedrock_synth_taxonomy import _full_code_requirements, _is_quality_ok


def main() -> int:
    path = Path("data/processed/v3/manual_quality_smoke_3.jsonl")
    if not path.exists():
        print(f"missing {path}")
        return 1

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"total_examples={len(rows)}")

    for i, row in enumerate(rows, 1):
        buggy = row["buggy"]
        correct = row["correct"]

        buggy_ok, buggy_reasons = _full_code_requirements(buggy)
        correct_ok, correct_reasons = _full_code_requirements(correct)
        quality_ok = _is_quality_ok(buggy, correct)

        diff = list(difflib.unified_diff(buggy.splitlines(), correct.splitlines(), lineterm=""))
        semantic_changes = [
            d for d in diff
            if (d.startswith("+") or d.startswith("-")) and not d.startswith("+++") and not d.startswith("---")
        ]

        print(f"example={i} slug={row.get('slug')}")
        print(f"  full_code_buggy={buggy_ok} full_code_correct={correct_ok} quality_ok={quality_ok}")
        print(f"  buggy_reasons={buggy_reasons}")
        print(f"  correct_reasons={correct_reasons}")
        print(f"  changed_lines={len(semantic_changes)}")
        if semantic_changes:
            print(f"  first_change={semantic_changes[0]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
