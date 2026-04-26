#!/usr/bin/env python3
"""
validate_fix_proof_local.py
===========================

Reference-based proof-style fix checker.

This script answers: "did the candidate fix address the presented issue?"
by comparing candidate code against anchors extracted from the known buggy->fixed
pair for each case.

Supported inputs:
1) V2 JSONL rows from build_aie_instruction_dataset.py
2) Holdout benchmark JSON from build_holdout_benchmark.py

For model evaluation, provide --predictions JSONL with per-case candidate code.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator

from validate_aie_compile import extract_buggy_code, extract_correct_code


_CODE_FENCE = re.compile(r"```(?:cpp|c\+\+|c)?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def strip_fence(text: str) -> str:
    if not text:
        return ""
    m = _CODE_FENCE.search(text)
    return (m.group(1) if m else text).strip()


def normalize_line(line: str) -> str:
    line = re.sub(r"//.*", "", line)
    line = line.strip()
    line = re.sub(r"\s+", " ", line)
    return line


def normalize_code(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    return "\n".join(normalize_line(l) for l in code.splitlines() if normalize_line(l))


@dataclass
class ProofResult:
    case_id: str
    source_kind: str
    bug_type: str
    anchor_add_total: int
    anchor_add_hit: int
    anchor_del_total: int
    anchor_del_absent: int
    add_hit_ratio: float
    del_absence_ratio: float
    changed_from_buggy: bool
    proof_fix_ok: bool
    evidence: str


def iter_v2_rows(path: Path) -> Iterator[tuple[str, dict, str, str, str, str]]:
    with path.open("r", encoding="utf-8") as fp:
        for i, line in enumerate(fp):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            buggy = extract_buggy_code(row) or ""
            fixed = extract_correct_code(row) or ""
            candidate = strip_fence(row.get("response", ""))
            md = row.get("metadata") or {}
            bug_type = str(md.get("bug_type") or md.get("category") or "unknown")
            case_id = str(md.get("group_id") or f"v2:{i}")
            yield case_id, row, buggy, fixed, candidate, bug_type


def iter_holdout_rows(path: Path) -> Iterator[tuple[str, dict, str, str, str, str]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    for i, row in enumerate(rows):
        case_id = str(row.get("id") or f"holdout:{i}")
        buggy = str(row.get("input") or "")
        fixed = str(row.get("fixed_code") or "")
        candidate = fixed
        md = row.get("metadata") or {}
        bug_type = str(md.get("bug_type") or "unknown")
        yield case_id, row, buggy, fixed, candidate, bug_type


def load_predictions(path: Path) -> dict[str, str]:
    preds: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            case_id = str(row.get("id") or row.get("case_id") or "")
            if not case_id:
                continue
            pred = row.get("prediction") or row.get("response") or ""
            preds[case_id] = strip_fence(str(pred))
    return preds


def anchors_from_diff(buggy: str, fixed: str, max_anchors: int) -> tuple[list[str], list[str]]:
    b_lines = [normalize_line(x) for x in normalize_code(buggy).splitlines()]
    f_lines = [normalize_line(x) for x in normalize_code(fixed).splitlines()]

    adds: list[str] = []
    dels: list[str] = []
    for d in difflib.ndiff(b_lines, f_lines):
        if d.startswith("+ "):
            val = d[2:].strip()
            if val:
                adds.append(val)
        elif d.startswith("- "):
            val = d[2:].strip()
            if val:
                dels.append(val)

    def uniq_keep_order(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for it in items:
            if it in seen:
                continue
            seen.add(it)
            out.append(it)
        return out

    adds = uniq_keep_order(adds)[:max_anchors]
    dels = uniq_keep_order(dels)[:max_anchors]
    return adds, dels


def evaluate_case(
    case_id: str,
    bug_type: str,
    buggy: str,
    fixed: str,
    candidate: str,
    source_kind: str,
    max_anchors: int,
    min_add_ratio: float,
    min_del_absence_ratio: float,
) -> ProofResult:
    add_anchors, del_anchors = anchors_from_diff(buggy, fixed, max_anchors=max_anchors)
    cand_norm = normalize_code(candidate)
    bug_norm = normalize_code(buggy)

    add_hit = sum(1 for a in add_anchors if a and a in cand_norm)
    del_absent = sum(1 for d in del_anchors if d and d not in cand_norm)

    add_total = len(add_anchors)
    del_total = len(del_anchors)

    add_ratio = (add_hit / add_total) if add_total else 1.0
    del_ratio = (del_absent / del_total) if del_total else 1.0

    changed = cand_norm != bug_norm

    proof_ok = bool(
        changed
        and add_ratio >= min_add_ratio
        and del_ratio >= min_del_absence_ratio
    )

    evidence_parts = [
        f"add={add_hit}/{add_total}",
        f"del_absent={del_absent}/{del_total}",
        f"changed={changed}",
    ]

    return ProofResult(
        case_id=case_id,
        source_kind=source_kind,
        bug_type=bug_type,
        anchor_add_total=add_total,
        anchor_add_hit=add_hit,
        anchor_del_total=del_total,
        anchor_del_absent=del_absent,
        add_hit_ratio=round(add_ratio, 3),
        del_absence_ratio=round(del_ratio, 3),
        changed_from_buggy=changed,
        proof_fix_ok=proof_ok,
        evidence=",".join(evidence_parts),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="Path to v2 JSONL or holdout JSON")
    ap.add_argument("--out", required=True, help="Output JSONL path")
    ap.add_argument("--input-kind", choices=["v2-jsonl", "holdout-json"], default="v2-jsonl")
    ap.add_argument("--predictions", default=None, help="Optional JSONL with id/case_id + prediction/response")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-anchors", type=int, default=12)
    ap.add_argument("--min-add-ratio", type=float, default=0.5)
    ap.add_argument("--min-del-absence-ratio", type=float, default=0.6)
    args = ap.parse_args(argv)

    in_path = Path(args.input)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    preds = load_predictions(Path(args.predictions)) if args.predictions else {}

    if args.input_kind == "v2-jsonl":
        rows = iter_v2_rows(in_path)
    else:
        rows = iter_holdout_rows(in_path)

    total = 0
    ok = 0
    bad = 0

    with out_path.open("w", encoding="utf-8") as ofp:
        for case_id, row, buggy, fixed, candidate, bug_type in rows:
            if args.limit is not None and total >= args.limit:
                break

            # If predictions are provided, override candidate by case id.
            if preds:
                pred = preds.get(case_id)
                if pred is None:
                    continue
                candidate = pred

            res = evaluate_case(
                case_id=case_id,
                bug_type=bug_type,
                buggy=buggy,
                fixed=fixed,
                candidate=candidate,
                source_kind=args.input_kind,
                max_anchors=args.max_anchors,
                min_add_ratio=args.min_add_ratio,
                min_del_absence_ratio=args.min_del_absence_ratio,
            )
            total += 1
            if res.proof_fix_ok:
                ok += 1
            else:
                bad += 1
            ofp.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")

    print(f"[proof] total={total} ok={ok} bad={bad} out={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
