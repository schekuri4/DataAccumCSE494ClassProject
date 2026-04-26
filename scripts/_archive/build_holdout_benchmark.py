"""Build a held-out bug-fix benchmark from the V2 validation split.

Output: data/processed/aie_debug_benchmark_holdout.json

Contains:
  1. Real buggy/correct code pairs drawn from V2 validation rows (covers the
     44 bug families that have paired source in the dataset).
  2. Synthesized pairs covering every taxonomy slug that is NOT already
     represented by a validation pair - one case per remaining slug, so the
     final benchmark spans all 205 taxonomy entries plus extra cases from
     heavily-represented families.

The eval script's `_make_diff_grader` auto-generates anchors from each
case's input->fixed_code diff, so no hand-written graders are needed.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from synthesize_taxonomy_bugs import synthesize_all  # noqa: E402
from build_aie_instruction_dataset import BUG_TAXONOMY_ENTRIES  # noqa: E402

VAL_PATH = ROOT / "data" / "processed" / "aie_instruction_v2_validation.jsonl"
OUT_PATH = ROOT / "data" / "processed" / "aie_debug_benchmark_holdout.json"

PAIR_VARIANTS = {
    "bug_fix_pair",
    "multi_file_bug_fix_pair",
    "bug_fix_pair_cropped",
    "bug_fix_pair_compiler_error",
    "taxonomy_debug_scenario",
    "taxonomy_multi_file_debug_scenario",
}

# Cases per bug_type - caps heavy families (e.g. off_by_one_oob has 146 rows)
# so one family can't dominate eval. Aim for ~200 cases total across ~44 families.
MAX_PER_BUG_TYPE = 6
TARGET_TOTAL = 220
# Cap input size so the eval's 4096-token tokenizer truncation doesn't cut
# the buggy code mid-file. ~8000 chars ≈ 2000 tokens, leaving room for prompt
# and generation budget.
MAX_INPUT_CHARS = 8000


def _parse_pair(context: str) -> tuple[str, str] | None:
    """Extract buggy and correct code blocks from the shared context format."""
    if "Buggy version:" not in context or "Correct version:" not in context:
        return None
    try:
        head, rest = context.split("Buggy version:", 1)
        buggy, correct = rest.split("Correct version:", 1)
    except ValueError:
        return None
    buggy = buggy.strip()
    correct = correct.strip()
    if not buggy or not correct:
        return None
    if buggy == correct:
        return None
    return buggy, correct


def _extract_fenced(code: str) -> str:
    """Strip a surrounding ```cpp ... ``` if present."""
    m = re.search(r"```(?:cpp|c\+\+|c)?\s*\n?(.*?)```", code, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return code.strip()


def _instruction_for(bug_type: str, variant: str, original: str) -> str:
    # Use the row's own instruction for error-message / cropped variants (they
    # carry unique phrasing). For others, use a neutral "fix this bug" prompt.
    if variant in {"bug_fix_pair_compiler_error", "bug_fix_pair_cropped"}:
        return original.strip()
    bt = bug_type.replace("_", " ") if bug_type else "a bug"
    return f"There is a bug in the AIE source below related to: {bt}. Return the full corrected source."


def build() -> list[dict]:
    if not VAL_PATH.exists():
        print(f"missing {VAL_PATH}", file=sys.stderr)
        sys.exit(1)

    by_bug: dict[str, list[dict]] = defaultdict(list)
    with VAL_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            meta = row.get("metadata", {}) or {}
            variant = meta.get("variant", "")
            if variant not in PAIR_VARIANTS:
                continue
            parsed = _parse_pair(row.get("context", ""))
            if not parsed:
                continue
            buggy, correct = parsed
            response_fixed = _extract_fenced(row.get("response", ""))
            # Prefer the response block as fixed_code since it's the training
            # target; fall back to the inline "Correct version" if response is empty.
            fixed_code = response_fixed or correct
            if not fixed_code.strip() or fixed_code.strip() == buggy.strip():
                continue
            if len(buggy) > MAX_INPUT_CHARS or len(fixed_code) > MAX_INPUT_CHARS:
                continue
            by_bug[meta.get("bug_type") or "unknown"].append({
                "instruction": row.get("instruction", "").strip(),
                "input": buggy,
                "fixed_code": fixed_code,
                "variant": variant,
                "difficulty_tier": meta.get("difficulty_tier"),
                "group_id": meta.get("group_id"),
                "source": meta.get("source_path") or meta.get("relative_path") or meta.get("source"),
            })

    # Stratified sampling: up to MAX_PER_BUG_TYPE per bug family, tier-diverse.
    benchmark: list[dict] = []
    for bug_type, rows in sorted(by_bug.items()):
        # Prefer one-per-tier first, then fill by group_id diversity.
        by_tier: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_tier[r.get("difficulty_tier") or "normal"].append(r)
        picked: list[dict] = []
        seen_groups: set[str] = set()
        # Round-robin across tiers
        tier_order = ["easy", "normal", "medium", "hard", "extra_hard"]
        while len(picked) < MAX_PER_BUG_TYPE:
            progressed = False
            for t in tier_order:
                if len(picked) >= MAX_PER_BUG_TYPE:
                    break
                bucket = by_tier.get(t, [])
                for candidate in bucket:
                    gid = candidate.get("group_id") or ""
                    if gid in seen_groups:
                        continue
                    picked.append(candidate)
                    seen_groups.add(gid)
                    bucket.remove(candidate)
                    progressed = True
                    break
            if not progressed:
                break
        for idx, pick in enumerate(picked):
            benchmark.append({
                "id": f"{bug_type}__{idx}",
                "instruction": _instruction_for(bug_type, pick["variant"], pick["instruction"]),
                "input": pick["input"],
                "fixed_code": pick["fixed_code"],
                "metadata": {
                    "bug_type": bug_type,
                    "variant": pick["variant"],
                    "difficulty_tier": pick.get("difficulty_tier"),
                    "group_id": pick.get("group_id"),
                    "source": pick.get("source"),
                    "split": "holdout",
                    "synthetic": False,
                },
            })
            if len(benchmark) >= TARGET_TOTAL:
                break
        if len(benchmark) >= TARGET_TOTAL:
            break

    return benchmark


def build_synthetic_fill(existing_cases: list[dict]) -> list[dict]:
    """Add one synthesized case per taxonomy slug not already represented in
    existing_cases by bug_type. Ensures the final benchmark spans all 205
    taxonomy entries."""
    covered_bug_types = {c["metadata"].get("bug_type") for c in existing_cases}
    # Build slug -> entry map once.
    tax_by_slug = {e["slug"]: e for e in BUG_TAXONOMY_ENTRIES}
    synth_pairs = synthesize_all()
    additions: list[dict] = []
    for pair in synth_pairs:
        slug = pair["slug"]
        if slug in covered_bug_types:
            continue
        label = pair["label"]
        tier = pair["tier"]
        buggy = pair["buggy"]
        correct = pair["correct"]
        pretty = label
        instruction = (
            f"There is a bug in the AIE source below related to: {pretty}. "
            "Return the full corrected source."
        )
        additions.append({
            "id": f"{slug}__synth_0",
            "instruction": instruction,
            "input": buggy,
            "fixed_code": correct,
            "metadata": {
                "bug_type": slug,
                "variant": "synthetic_taxonomy_fill",
                "difficulty_tier": tier,
                "group_id": f"synth:{slug}",
                "source": None,
                "split": "holdout",
                "synthetic": True,
                "taxonomy_label": label,
            },
        })
    return additions


def main() -> None:
    cases = build()
    synth = build_synthetic_fill(cases)
    cases.extend(synth)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(cases, indent=2), encoding="utf-8")

    bug_types = sorted({c["metadata"]["bug_type"] for c in cases})
    synth_count = sum(1 for c in cases if c["metadata"].get("synthetic"))
    real_count = len(cases) - synth_count
    tiers: dict[str, int] = {}
    for c in cases:
        t = c["metadata"].get("difficulty_tier") or "unknown"
        tiers[t] = tiers.get(t, 0) + 1
    variants: dict[str, int] = {}
    for c in cases:
        v = c["metadata"].get("variant") or ""
        variants[v] = variants.get(v, 0) + 1
    groups = {c["metadata"].get("group_id") for c in cases}
    print(f"wrote {len(cases)} cases to {OUT_PATH}")
    print(f"  real (from validation): {real_count}")
    print(f"  synthesized (taxonomy fill): {synth_count}")
    print(f"bug_types covered: {len(bug_types)}")
    print(f"unique source groups: {len(groups)}")
    print(f"tiers: {tiers}")
    print(f"variants: {variants}")


if __name__ == "__main__":
    main()
