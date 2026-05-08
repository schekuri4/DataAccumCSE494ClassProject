#!/usr/bin/env python3
"""Screen V4 bug-fix pairs for zero-signal and very-low-similarity issues.

Three filters applied to rows with v4_bucket in (bedrock_mutated_bug_fix_pair,
compile_validated_original, compile_validated_replacement):

  1. buggy_eq_correct  — buggy code block identical to response (training signal = 0)
  2. very_low_sim      — similarity(buggy, response) < LOW_SIM_THRESHOLD (default 0.50)
                         → model learns to rewrite rather than surgically fix
  3. low_sim           — similarity < HIGH_SIM_THRESHOLD (default 0.75) — reported only,
                         not removed unless --remove-low-sim is passed

Similarity is computed with difflib.SequenceMatcher on stripped source text.

Usage:
    python scripts/screen_low_quality_pairs_v4.py [--dry-run] [--remove-low-sim]
    python scripts/screen_low_quality_pairs_v4.py --stats-only
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[1]
V4_DIR = ROOT / "data" / "processed" / "v4"
V4_ALL   = V4_DIR / "aie_instruction_v4_all.jsonl"
V4_TRAIN = V4_DIR / "aie_instruction_v4_train.jsonl"
V4_VAL   = V4_DIR / "aie_instruction_v4_validation.jsonl"
V4_SUM   = V4_DIR / "aie_instruction_v4_summary.json"

# Similarity thresholds
VERY_LOW_SIM = 0.50   # always removed
LOW_SIM      = 0.75   # removed only with --remove-low-sim

# Which buckets to apply similarity checks to
PAIR_BUCKETS = {
    "bedrock_mutated_bug_fix_pair",
    "compile_validated_original",
    "compile_validated_replacement",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

def extract_code(text: str) -> str:
    """Extract first fenced code block, or fall back to full text."""
    m = _FENCE_RE.search(text or "")
    return m.group(1).strip() if m else (text or "").strip()


def extract_buggy(context: str) -> str:
    """Pull the buggy code from the context field.

    Context typically contains 'Buggy version:\n```cpp\n...\n```' or just
    a fenced block. We extract all fenced blocks and return the last one
    (which is usually the buggy code after the scenario description).
    """
    blocks = _FENCE_RE.findall(context or "")
    if blocks:
        return blocks[-1].strip()
    # fallback: lines after 'Buggy version:' heading
    lines = (context or "").splitlines()
    for i, line in enumerate(lines):
        if "buggy" in line.lower() or "broken" in line.lower():
            return "\n".join(lines[i + 1:]).strip()
    return (context or "").strip()


def sim(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def stable_row_id(context: str, response: str, slug: str) -> str:
    payload = json.dumps({"context": context, "response": response, "slug": slug},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def update_summary(rows: list[dict], summary_path: Path) -> None:
    from collections import Counter as C
    buckets = C(str((r.get("metadata") or {}).get("v4_bucket") or "") for r in rows)
    splits  = C(str((r.get("metadata") or {}).get("split") or "") for r in rows)
    train_rows = [r for r in rows if (r.get("metadata") or {}).get("split") == "train"]
    val_rows   = [r for r in rows if (r.get("metadata") or {}).get("split") == "validation"]
    summary = {
        "all":   {"rows": len(rows),        "bucket_counts": dict(buckets)},
        "train": {"rows": len(train_rows)},
        "validation": {"rows": len(val_rows)},
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v4-dir",         default=str(V4_DIR))
    parser.add_argument("--very-low-sim",   type=float, default=VERY_LOW_SIM,
                        help="Remove pairs with sim < this (default %(default)s)")
    parser.add_argument("--low-sim",        type=float, default=LOW_SIM,
                        help="Report (and optionally remove) pairs with sim < this (default %(default)s)")
    parser.add_argument("--remove-low-sim", action="store_true",
                        help="Also remove pairs in the low-sim (but not very-low-sim) band")
    parser.add_argument("--stats-only",     action="store_true")
    parser.add_argument("--dry-run",        action="store_true")
    args = parser.parse_args()

    v4_dir = Path(args.v4_dir)
    all_rows = load_jsonl(v4_dir / "aie_instruction_v4_all.jsonl")

    # ── Analyse ──────────────────────────────────────────────────────────────
    zero_signal:   list[dict] = []   # buggy == correct
    very_low:      list[dict] = []   # sim < very_low_sim (and not zero)
    low_band:      list[dict] = []   # very_low_sim <= sim < low_sim
    ok:            list[dict] = []   # everything else (including non-pair buckets)

    sim_by_bucket: dict[str, list[float]] = defaultdict(list)

    for row in all_rows:
        meta   = row.get("metadata") or {}
        bucket = str(meta.get("v4_bucket") or "")

        if bucket not in PAIR_BUCKETS:
            ok.append(row)
            continue

        context  = row.get("context", "")
        response = row.get("response", "")

        buggy   = extract_buggy(context)
        correct = extract_code(response)

        s = sim(buggy, correct)
        sim_by_bucket[bucket].append(s)

        if buggy == correct:
            row = {**row, "_screen_reason": "buggy_eq_correct", "_sim": 1.0}
            zero_signal.append(row)
        elif s < args.very_low_sim:
            row = {**row, "_screen_reason": "very_low_sim", "_sim": round(s, 4)}
            very_low.append(row)
        elif s < args.low_sim:
            row = {**row, "_screen_reason": "low_sim", "_sim": round(s, 4)}
            low_band.append(row)
        else:
            ok.append(row)

    # ── Report ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"V4 quality screen  —  {len(all_rows)} total rows")
    print(f"{'='*60}")
    print(f"  buggy_eq_correct  (sim=1.0, zero signal):   {len(zero_signal):5d}")
    print(f"  very_low_sim      (sim < {args.very_low_sim:.2f}):              {len(very_low):5d}")
    print(f"  low_sim band      ({args.very_low_sim:.2f} ≤ sim < {args.low_sim:.2f}):        {len(low_band):5d}")
    print(f"  clean             :                          {len(ok):5d}")

    # Breakdown of very_low_sim by bucket
    if very_low:
        print(f"\n  very_low_sim by bucket:")
        bc = Counter(r.get("metadata", {}).get("v4_bucket", "?") for r in very_low)
        for b, c in bc.most_common():
            print(f"    {c:5d}  {b}")

    # Breakdown of zero_signal by bucket
    if zero_signal:
        print(f"\n  buggy_eq_correct by bucket:")
        bc = Counter(r.get("metadata", {}).get("v4_bucket", "?") for r in zero_signal)
        for b, c in bc.most_common():
            print(f"    {c:5d}  {b}")

    # Similarity percentiles per pair bucket
    print(f"\n  Similarity stats per bucket:")
    for bkt, sims in sorted(sim_by_bucket.items()):
        sims_s = sorted(sims)
        n = len(sims_s)
        p10 = sims_s[int(n * 0.10)]
        p25 = sims_s[int(n * 0.25)]
        p50 = sims_s[n // 2]
        print(f"    {bkt}")
        print(f"      n={n}  p10={p10:.3f}  p25={p25:.3f}  median={p50:.3f}")

    if args.stats_only:
        return

    # ── Decide what to remove ────────────────────────────────────────────────
    to_remove = zero_signal + very_low
    if args.remove_low_sim:
        to_remove += low_band
        print(f"\n  --remove-low-sim set: removing low_sim band too")

    remove_ids: set[str] = set()
    for row in to_remove:
        meta     = row.get("metadata") or {}
        context  = row.get("context", "")
        response = row.get("response", "")
        slug     = meta.get("slug") or meta.get("preferred_bug_type") or ""
        remove_ids.add(stable_row_id(context, response, slug))

    print(f"\n  Total rows to remove: {len(to_remove)}")

    if args.dry_run:
        print("  [dry-run] No files written.")
        # Print a few examples
        print("\n  Sample very_low_sim rows:")
        for row in very_low[:5]:
            meta = row.get("metadata", {})
            print(f"    sim={row.get('_sim')}  bucket={meta.get('v4_bucket')}  slug={meta.get('slug') or meta.get('preferred_bug_type','?')[:60]}")
        return

    # ── Rewrite V4 ───────────────────────────────────────────────────────────
    kept: list[dict] = []
    for row in all_rows:
        meta     = row.get("metadata") or {}
        context  = row.get("context", "")
        response = row.get("response", "")
        slug     = meta.get("slug") or meta.get("preferred_bug_type") or ""
        rid = stable_row_id(context, response, slug)
        if rid in remove_ids:
            continue
        kept.append(row)

    train_kept = [r for r in kept if (r.get("metadata") or {}).get("split") == "train"]
    val_kept   = [r for r in kept if (r.get("metadata") or {}).get("split") == "validation"]

    write_jsonl(v4_dir / "aie_instruction_v4_all.jsonl",        kept)
    write_jsonl(v4_dir / "aie_instruction_v4_train.jsonl",      train_kept)
    write_jsonl(v4_dir / "aie_instruction_v4_validation.jsonl", val_kept)
    update_summary(kept, v4_dir / "aie_instruction_v4_summary.json")

    print(f"\n  V4 rows before: {len(all_rows)}")
    print(f"  V4 rows after:  {len(kept)}")
    print(f"  Train: {len(train_kept)}  Validation: {len(val_kept)}")
    print("  V4 artifacts updated.")

    # Save removed rows for review
    review_path = v4_dir / "screen_removed_pairs.jsonl"
    write_jsonl(review_path, to_remove)
    print(f"  Removed rows saved to: {review_path}")


if __name__ == "__main__":
    main()
