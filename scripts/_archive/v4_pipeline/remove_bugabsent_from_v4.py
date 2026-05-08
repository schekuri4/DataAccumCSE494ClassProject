#!/usr/bin/env python3
"""Remove V4 mutation rows flagged as bug-absent by the bug-presence audit.

Reads:  data/processed/v4/bug_presence_audit.jsonl   (audit results)
        data/processed/v4/aie_instruction_v4_all.jsonl

Removes all bedrock_mutated_bug_fix_pair rows whose row_id appears in the
audit file with bug_present=False, then rewrites all V4 artifacts.

Also writes data/processed/v4/bug_absent_source_ids.jsonl — one row per
removed pair with the source_id + slug so the re-mutation pass can target
exactly those baselines.

Usage:
    python scripts/remove_bugabsent_from_v4.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4_DIR = ROOT / "data" / "processed" / "v4"

AUDIT_FILE = V4_DIR / "bug_presence_audit.jsonl"
V4_ALL     = V4_DIR / "aie_instruction_v4_all.jsonl"
V4_TRAIN   = V4_DIR / "aie_instruction_v4_train.jsonl"
V4_VAL     = V4_DIR / "aie_instruction_v4_validation.jsonl"
V4_SUMMARY = V4_DIR / "aie_instruction_v4_summary.json"
RETRY_OUT  = V4_DIR / "bug_absent_source_ids.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def stable_row_id(context: str, response: str, slug: str) -> str:
    payload = json.dumps({"context": context, "response": response, "slug": slug},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", default=str(AUDIT_FILE))
    parser.add_argument("--v4-dir", default=str(V4_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    audit_path = Path(args.audit)
    v4_dir = Path(args.v4_dir)

    # Build set of row_ids where bug_present is False
    audit_rows = load_jsonl(audit_path)
    absent_row_ids: set[str] = set()
    absent_meta: dict[str, dict] = {}  # row_id -> {source_id, slug, bug_label}
    for r in audit_rows:
        if r.get("bug_present") is False:
            rid = str(r.get("row_id") or "")
            absent_row_ids.add(rid)
            absent_meta[rid] = {
                "source_id": r.get("source_id"),
                "slug": r.get("slug"),
                "bug_label": r.get("bug_label"),
                "confidence": r.get("confidence"),
                "explanation": r.get("explanation"),
            }

    print(f"Bug-absent row_ids from audit: {len(absent_row_ids)}")

    # Load V4
    all_rows = load_jsonl(v4_dir / "aie_instruction_v4_all.jsonl")
    before = len(all_rows)

    kept: list[dict] = []
    removed: list[dict] = []

    for row in all_rows:
        meta = row.get("metadata") or {}
        bucket = str(meta.get("v4_bucket") or "")
        if bucket != "bedrock_mutated_bug_fix_pair":
            kept.append(row)
            continue
        # Recompute row_id the same way the audit script did
        context  = row.get("context", "")
        response = row.get("response", "")
        slug     = meta.get("slug") or meta.get("preferred_bug_type") or ""
        rid = stable_row_id(context, response, slug)
        if rid in absent_row_ids:
            removed.append(row)
        else:
            kept.append(row)

    print(f"V4 rows before:  {before}")
    print(f"Rows removed:    {len(removed)}")
    print(f"V4 rows after:   {len(kept)}")

    train_rows = [r for r in kept if (r.get("metadata") or {}).get("split") == "train"]
    val_rows   = [r for r in kept if (r.get("metadata") or {}).get("split") == "validation"]
    print(f"Train: {len(train_rows)}  Validation: {len(val_rows)}")

    buckets = Counter(str((r.get("metadata") or {}).get("v4_bucket") or "<none>") for r in kept)
    print("\nBucket counts after removal:")
    for b, c in sorted(buckets.items(), key=lambda x: -x[1]):
        print(f"  {c:6d}  {b}")

    # Build retry list: unique source_ids from removed rows
    retry_by_sid: dict[str, dict] = {}
    for row in removed:
        meta = row.get("metadata") or {}
        sid  = str(meta.get("source_id") or "")
        if not sid:
            continue
        if sid not in retry_by_sid:
            context  = row.get("context", "")
            response = row.get("response", "")
            slug     = meta.get("slug") or meta.get("preferred_bug_type") or ""
            rid = stable_row_id(context, response, slug)
            am = absent_meta.get(rid, {})
            retry_by_sid[sid] = {
                "source_id": sid,
                "slug": slug,
                "bug_label": meta.get("preferred_bug_label") or meta.get("bug_label") or slug,
                "tier": meta.get("tier", ""),
                "variant_idx": meta.get("variant_idx"),
                "audit_explanation": am.get("explanation", ""),
                "audit_confidence": am.get("confidence", ""),
            }

    print(f"\nUnique source_ids queued for re-mutation: {len(retry_by_sid)}")

    if args.dry_run:
        print("\n(dry run — no files written)")
        return

    write_jsonl(v4_dir / "aie_instruction_v4_all.jsonl", kept)
    write_jsonl(v4_dir / "aie_instruction_v4_train.jsonl", train_rows)
    write_jsonl(v4_dir / "aie_instruction_v4_validation.jsonl", val_rows)

    summary = {
        "rows_before_bugabsent_removal": before,
        "rows_removed_bugabsent": len(removed),
        "all": {"rows": len(kept)},
        "train": {"rows": len(train_rows)},
        "validation": {"rows": len(val_rows)},
        "bucket_counts": dict(buckets),
    }
    (v4_dir / "aie_instruction_v4_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    retry_rows = list(retry_by_sid.values())
    write_jsonl(RETRY_OUT, retry_rows)

    print(f"V4 artifacts updated.")
    print(f"Re-mutation queue: {RETRY_OUT}  ({len(retry_rows)} rows)")


if __name__ == "__main__":
    main()
