#!/usr/bin/env python3
"""Audit every bedrock_mutated_bug_fix_pair row in V4 to verify the bug was
actually implemented in the buggy version.

Reads:  data/processed/v4/aie_instruction_v4_all.jsonl
Writes: data/processed/v4/bug_presence_audit.jsonl

Each output row:
  row_id        - stable SHA1 over context+response+slug
  slug          - bug category slug
  bug_label     - human-readable label
  bug_present   - true / false
  confidence    - "high" / "medium" / "low"
  explanation   - one sentence from the model
  raw_verdict   - raw model text (first ~200 chars)

Resume: rows already in the output file (with a verdict) are skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4_ALL = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_all.jsonl"
AUDIT_OUT = ROOT / "data" / "processed" / "v4" / "bug_presence_audit.jsonl"

DEFAULT_MODEL = "deepseek.v3.2"
DEFAULT_REGION = "us-east-1"
DEFAULT_WORKERS = 12
MAX_RETRIES = 4
RETRY_BASE_DELAY = 2.0

# Buckets that represent bug-fix pairs we want to audit
BUG_FIX_BUCKETS = {"bedrock_mutated_bug_fix_pair", "curated_seed_bug_fix"}

SYSTEM_PROMPT = (
    "You are a precise Xilinx AIE code reviewer. "
    "Respond only with the JSON object requested. No preamble or markdown fences."
)

AUDIT_TEMPLATE = """\
You are verifying a bug-fix training pair for Xilinx AIE code.

Bug category  : {bug_label}
Slug          : {slug}

BUGGY CODE (the code to fix):
{buggy}

CORRECT CODE (the reference fix):
{correct}

Task: Determine whether the BUGGY CODE actually contains the intended bug described
by the bug category above (when compared to the CORRECT CODE).

Rules:
- Compare the two versions. The diff should implement a realistic version of "{bug_label}".
- If the two versions are identical, the bug is NOT present.
- If the diff exists but is unrelated to the bug category, the bug is NOT present.
- If there is a real, recognizable implementation of the stated bug, it IS present.

Respond with ONLY a JSON object (no markdown fences):
{{
  "bug_present": true|false,
  "confidence": "high"|"medium"|"low",
  "explanation": "<one sentence>"
}}
"""


# ---------------------------------------------------------------------------
# Bedrock helpers
# ---------------------------------------------------------------------------

def make_client(region: str):
    import boto3
    if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        raise RuntimeError("AWS_BEARER_TOKEN_BEDROCK is not set.")
    return boto3.client("bedrock-runtime", region_name=region)


def call_bedrock(client, model_id: str, user_text: str) -> str:
    resp = client.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_text}]}],
        inferenceConfig={"maxTokens": 256, "temperature": 0.0},
    )
    content = resp["output"]["message"]["content"]
    return "".join(block.get("text", "") for block in content)


def call_with_retry(client, model_id: str, user_text: str) -> str:
    delay = RETRY_BASE_DELAY
    for attempt in range(MAX_RETRIES):
        try:
            return call_bedrock(client, model_id, user_text)
        except Exception as exc:
            msg = str(exc)
            retryable = any(t in msg for t in ("ThrottlingException", "ServiceUnavailable", "TooManyRequests"))
            if retryable and attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("max retries exceeded")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def stable_row_id(context: str, response: str, slug: str) -> str:
    payload = json.dumps({"context": context, "response": response, "slug": slug},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def extract_code_from_fenced(text: str) -> str:
    """Strip ```cpp ... ``` fences if present."""
    m = re.match(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def load_done_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    for line in path.open("r", encoding="utf-8"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if "bug_present" in row and row.get("row_id"):
                done.add(str(row["row_id"]))
        except json.JSONDecodeError:
            continue
    return done


def load_pairs(v4_path: Path, buckets: set[str]) -> list[dict]:
    rows = []
    for line in v4_path.open("r", encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        meta = row.get("metadata") or {}
        if meta.get("v4_bucket") not in buckets:
            continue
        context = row.get("context", "")
        response = row.get("response", "")
        slug = meta.get("slug") or meta.get("preferred_bug_type") or ""
        bug_label = meta.get("preferred_bug_label") or meta.get("bug_label") or slug
        if not context or not response or not slug:
            continue
        rows.append({
            "row_id": stable_row_id(context, response, slug),
            "slug": slug,
            "bug_label": bug_label,
            "buggy": extract_code_from_fenced(context),
            "correct": extract_code_from_fenced(response),
            "v4_bucket": meta.get("v4_bucket"),
            "source_id": meta.get("source_id"),
            "variant_idx": meta.get("variant_idx"),
        })
    return rows


def parse_model_verdict(raw: str) -> tuple[bool | None, str, str]:
    """Parse JSON from model. Returns (bug_present, confidence, explanation)."""
    raw = raw.strip()
    # Strip optional markdown fences
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    raw = raw.strip()
    try:
        obj = json.loads(raw)
        bug_present = bool(obj.get("bug_present"))
        confidence = str(obj.get("confidence", "medium"))
        explanation = str(obj.get("explanation", ""))
        return bug_present, confidence, explanation
    except (json.JSONDecodeError, Exception):
        # Fallback: look for YES/NO
        upper = raw.upper()
        if "\"BUG_PRESENT\": TRUE" in upper or '"BUG_PRESENT":TRUE' in upper:
            return True, "low", raw[:200]
        if "\"BUG_PRESENT\": FALSE" in upper or '"BUG_PRESENT":FALSE' in upper:
            return False, "low", raw[:200]
        return None, "low", raw[:200]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

_write_lock = threading.Lock()
_stats = {"done": 0, "pass": 0, "fail": 0, "uncertain": 0, "error": 0, "total": 0}


def process_row(row: dict, client, model_id: str, out_path: Path) -> dict:
    prompt = AUDIT_TEMPLATE.format(
        bug_label=row["bug_label"],
        slug=row["slug"],
        buggy=row["buggy"],
        correct=row["correct"],
    )
    try:
        raw = call_with_retry(client, model_id, prompt)
    except Exception as exc:
        result = {
            "row_id": row["row_id"],
            "slug": row["slug"],
            "bug_label": row["bug_label"],
            "v4_bucket": row["v4_bucket"],
            "source_id": row.get("source_id"),
            "variant_idx": row.get("variant_idx"),
            "bug_present": None,
            "confidence": "low",
            "explanation": f"ERROR: {exc}",
            "raw_verdict": "",
            "error": True,
        }
        with _write_lock:
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
            _stats["error"] += 1
            _stats["done"] += 1
        return result

    bug_present, confidence, explanation = parse_model_verdict(raw)

    result = {
        "row_id": row["row_id"],
        "slug": row["slug"],
        "bug_label": row["bug_label"],
        "v4_bucket": row["v4_bucket"],
        "source_id": row.get("source_id"),
        "variant_idx": row.get("variant_idx"),
        "bug_present": bug_present,
        "confidence": confidence,
        "explanation": explanation,
        "raw_verdict": raw[:300],
        "error": False,
    }

    with _write_lock:
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
        if bug_present is True:
            _stats["pass"] += 1
        elif bug_present is False:
            _stats["fail"] += 1
        else:
            _stats["uncertain"] += 1
        _stats["done"] += 1
        done = _stats["done"]
        total = _stats["total"]
        if done % 50 == 0 or done == total:
            pct = 100 * done / total if total else 0
            print(
                f"  [{done}/{total} {pct:.0f}%]  pass={_stats['pass']}  "
                f"fail={_stats['fail']}  uncertain={_stats['uncertain']}  error={_stats['error']}",
                flush=True,
            )
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Audit bug presence in V4 debug pairs")
    parser.add_argument("--input", default=str(V4_ALL), help="V4 JSONL path")
    parser.add_argument("--output", default=str(AUDIT_OUT), help="Audit output JSONL")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--limit", type=int, default=0, help="Max rows to process (0=all)")
    parser.add_argument("--stats-only", action="store_true", help="Only print stats from existing audit file")
    args = parser.parse_args()

    out_path = Path(args.output)

    if args.stats_only:
        if not out_path.exists():
            print("No audit file found.")
            return
        rows = [json.loads(l) for l in out_path.open(encoding="utf-8") if l.strip()]
        total = len(rows)
        present = sum(1 for r in rows if r.get("bug_present") is True)
        absent  = sum(1 for r in rows if r.get("bug_present") is False)
        uncertain = sum(1 for r in rows if r.get("bug_present") is None)
        errors = sum(1 for r in rows if r.get("error"))
        print(f"Audit results ({total} rows audited):")
        print(f"  Bug present (PASS):  {present:5d}  ({100*present/total:.1f}%)")
        print(f"  Bug absent  (FAIL):  {absent:5d}  ({100*absent/total:.1f}%)")
        print(f"  Uncertain:           {uncertain:5d}  ({100*uncertain/total:.1f}%)")
        print(f"  Errors:              {errors:5d}  ({100*errors/total:.1f}%)")
        # By confidence
        from collections import Counter
        conf = Counter(r.get("confidence") for r in rows if r.get("bug_present") is False)
        if conf:
            print(f"\nFailing rows by confidence: {dict(conf)}")
        # Top failing slugs
        fail_slugs = Counter(r.get("slug") for r in rows if r.get("bug_present") is False)
        if fail_slugs:
            print(f"\nTop 20 slugs with bug NOT present:")
            for slug, cnt in fail_slugs.most_common(20):
                print(f"  {cnt:4d}  {slug}")
        return

    # Load pairs
    v4_path = Path(args.input)
    print(f"Loading bug-fix pairs from {v4_path.name}...")
    all_pairs = load_pairs(v4_path, BUG_FIX_BUCKETS)
    print(f"  Found {len(all_pairs)} bug-fix pairs")

    # Resume
    done_ids = load_done_ids(out_path)
    pending = [r for r in all_pairs if r["row_id"] not in done_ids]
    if args.limit > 0:
        pending = pending[: args.limit]
    print(f"  Already audited: {len(done_ids)}")
    print(f"  Pending:         {len(pending)}")

    if not pending:
        print("All rows already audited. Use --stats-only to see results.")
        return

    _stats["total"] = len(pending)

    # Check env
    if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        print("ERROR: AWS_BEARER_TOKEN_BEDROCK not set.", file=sys.stderr)
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    import boto3
    client = boto3.client("bedrock-runtime", region_name=args.region)

    print(f"Running with {args.workers} workers, model={args.model}")
    print(f"Output: {out_path}")
    print()

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_row, row, client, args.model, out_path): row for row in pending}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                print(f"Unhandled error: {exc}", file=sys.stderr)

    elapsed = time.time() - t0
    total_done = _stats["done"]
    print()
    print(f"Done in {elapsed:.1f}s — {total_done} rows audited")
    print(f"  Bug present:  {_stats['pass']}  ({100*_stats['pass']/max(total_done,1):.1f}%)")
    print(f"  Bug absent:   {_stats['fail']}  ({100*_stats['fail']/max(total_done,1):.1f}%)")
    print(f"  Uncertain:    {_stats['uncertain']}  ({100*_stats['uncertain']/max(total_done,1):.1f}%)")
    print(f"  Errors:       {_stats['error']}")


if __name__ == "__main__":
    main()
