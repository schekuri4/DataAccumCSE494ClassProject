#!/usr/bin/env python3
"""Re-run mutation generation for pairs flagged as bug-absent by the audit.

Reads:
  data/processed/v4/bug_absent_source_ids.jsonl  -- 545 failed source_ids + metadata
  data/processed/v3/bedrock_buggy_from_compile_validated_correct.jsonl  -- original mutation file
      (we extract the correct baseline code from here by source_id)
  Also checks bedrock_compile_validated_correct_full_budget80.jsonl as fallback.

Writes:
  data/processed/v3/bedrock_buggy_remutated.jsonl  -- new mutation attempts

The prompt is more explicit about the previous failure mode: it tells the model
that a prior attempt produced code identical to the correct version (i.e. no bug
was introduced) and demands a concrete, verifiable edit.

After this runs, use add_bedrock_mutations_to_v4.py with
--input bedrock_buggy_remutated.jsonl to add accepted pairs back into V4.

Usage:
    python scripts/remutate_bugabsent.py [--workers 12] [--resume]
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
V3_DIR = ROOT / "data" / "processed" / "v3"
V4_DIR = ROOT / "data" / "processed" / "v4"

RETRY_QUEUE = V4_DIR / "bug_absent_source_ids.jsonl"
MUTATION_FILE_PRIMARY = V3_DIR / "bedrock_buggy_from_compile_validated_correct.jsonl"
MUTATION_FILE_OLD     = V3_DIR / "bedrock_compile_validated_correct_full_budget80.jsonl"
CORRECT_FILE_NEW      = V3_DIR / "bedrock_expanded_topics_correct_compile_validated.jsonl"
OUTPUT_FILE           = V3_DIR / "bedrock_buggy_remutated.jsonl"

DEFAULT_MODEL   = "deepseek.v3.2"
DEFAULT_REGION  = "us-east-1"
DEFAULT_WORKERS = 12
DEFAULT_MAX_TOKENS = 4096
MAX_RETRIES = 4
RETRY_BASE_DELAY = 2.0

BUGGY_RE = re.compile(r"<BUGGY>(.*?)</BUGGY>", re.DOTALL)

SYSTEM_PROMPT = (
    "You are an expert Xilinx AIE C++ programmer. "
    "Follow instructions precisely. Return only the requested XML block."
)

REMUTATION_TEMPLATE = """\
You are given a CORRECT AIE mini-project and must create a BUGGY version for a
bug-fix training pair.

IMPORTANT: A previous attempt for this exact input produced code that was
IDENTICAL to the correct version — meaning NO bug was introduced. This time you
MUST make a real, concrete code change that introduces the bug described below.

Preferred bug category: {label}
Slug                  : {slug}
Tier                  : {tier}
Variant               : {variant_idx}
Previous failure note : {audit_explanation}

Requirements:
1. You MUST change at least one line of code from the CORRECT version.
2. The change must introduce a realistic bug matching "{label}".
3. If that exact category truly cannot be represented in this code, introduce a
   DIFFERENT realistic AIE bug (e.g. wrong loop bound, off-by-one, missing
   writeincr, wrong PLIO width, swapped port connection). Do NOT return
   unchanged code under any circumstances.
4. Keep the same // FILE: markers and file layout.
5. Make the smallest source edit that clearly introduces the bug.
6. Do not add any comments explaining the bug.
7. Do not use markdown fences. Return ONLY this XML block:

<BUGGY>
...full buggy project with the same // FILE: markers...
</BUGGY>

Correct project:
<CORRECT>
{correct}
</CORRECT>
"""


# ---------------------------------------------------------------------------
# Bedrock helpers (same pattern as generate_bedrock_buggy_from_correct.py)
# ---------------------------------------------------------------------------

def make_client(region: str):
    import boto3
    if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        raise RuntimeError("AWS_BEARER_TOKEN_BEDROCK is not set.")
    return boto3.client("bedrock-runtime", region_name=region)


def call_bedrock(client, model_id: str, user_text: str, temperature: float, max_tokens: int) -> tuple[str, int, int]:
    resp = client.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_text}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    content = resp["output"]["message"]["content"]
    text = "".join(block.get("text", "") for block in content)
    usage = resp.get("usage", {})
    return text, int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))


def call_with_retry(client, model_id: str, user_text: str, temperature: float, max_tokens: int) -> tuple[str, int, int]:
    delay = RETRY_BASE_DELAY
    for attempt in range(MAX_RETRIES):
        try:
            return call_bedrock(client, model_id, user_text, temperature, max_tokens)
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
# Parsing helpers (reused from original script)
# ---------------------------------------------------------------------------

def strip_optional_fences(text: str) -> str:
    text = text.strip()
    for lang in ("```cpp", "```c++", "```c", "```"):
        text = text.removeprefix(lang).strip()
    return text.removesuffix("```").strip()


def parse_buggy_project(text: str, correct: str) -> tuple[str | None, list[str]]:
    from scripts.generate_bedrock_buggy_from_correct import (
        parse_buggy_project as _parse,
    )
    return _parse(text, correct)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def build_source_id_to_correct(paths: list[Path]) -> dict[str, str]:
    """Map source_id -> correct code from mutation files and correct files."""
    mapping: dict[str, str] = {}

    # From mutation file: rows have source_id and correct fields
    for path in paths:
        if not path.exists():
            continue
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = str(row.get("source_id") or "")
            correct = row.get("correct") or ""
            if sid and correct and sid not in mapping:
                mapping[sid] = correct
    return mapping


def load_done_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if row.get("parse_ok") and row.get("buggy") and row.get("correct"):
                sid = str(row.get("source_id") or "")
                if sid:
                    done.add(sid)
        except json.JSONDecodeError:
            continue
    return done


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

_write_lock = threading.Lock()
_stats = {"done": 0, "accepted": 0, "rejected": 0, "error": 0, "total": 0}


def process_row(row: dict, correct: str, client, model_id: str, temperature: float,
                max_tokens: int, out_path: Path) -> None:
    prompt = REMUTATION_TEMPLATE.format(
        label=row.get("bug_label") or row.get("slug") or "AIE bug",
        slug=row.get("slug") or "",
        tier=row.get("tier") or "unknown",
        variant_idx=row.get("variant_idx"),
        audit_explanation=row.get("audit_explanation") or "prior attempt returned unchanged code",
        correct=correct.rstrip(),
    )

    try:
        text, in_tok, out_tok = call_with_retry(client, model_id, prompt, temperature, max_tokens)
    except Exception as exc:
        out_row = {
            "source_id": row["source_id"],
            "slug": row.get("slug"),
            "parse_ok": False,
            "reject_reason": ["call_error"],
            "error": str(exc),
            "buggy": None,
            "correct": correct,
        }
        with _write_lock:
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            _stats["error"] += 1
            _stats["done"] += 1
        return

    # Use the same parser as the original pipeline
    m = BUGGY_RE.search(text)
    buggy_raw = m.group(1).strip() if m else text.strip()
    buggy = strip_optional_fences(buggy_raw)

    reasons: list[str] = []
    if not buggy:
        reasons = ["empty_buggy_project"]
    elif buggy == correct:
        reasons = ["buggy_same_as_correct"]
    elif "// FILE:" not in buggy:
        reasons = ["missing_file_markers"]
    else:
        correct_files = set(re.findall(r"^\s*//\s*FILE:\s*(.+?)\s*$", correct, flags=re.MULTILINE))
        buggy_files   = set(re.findall(r"^\s*//\s*FILE:\s*(.+?)\s*$", buggy,   flags=re.MULTILINE))
        if correct_files != buggy_files:
            reasons = ["file_layout_changed"]

    parse_ok = len(reasons) == 0

    out_row = {
        "source_id": row["source_id"],
        "slug": row.get("slug"),
        "tier": row.get("tier"),
        "variant_idx": row.get("variant_idx"),
        "bug_label": row.get("bug_label"),
        "parse_ok": parse_ok,
        "reject_reason": reasons if not parse_ok else [],
        "buggy": buggy if parse_ok else None,
        "correct": correct,
        "model": model_id,
        "remutation": True,
        "mutation_source": "bedrock_compile_validated_correct",
    }

    with _write_lock:
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(out_row, ensure_ascii=False) + "\n")
        if parse_ok:
            _stats["accepted"] += 1
        else:
            _stats["rejected"] += 1
        _stats["done"] += 1
        done = _stats["done"]
        total = _stats["total"]
        if done % 50 == 0 or done == total:
            pct = 100 * done / total if total else 0
            print(
                f"  [{done}/{total} {pct:.0f}%]  accepted={_stats['accepted']}  "
                f"rejected={_stats['rejected']}  error={_stats['error']}",
                flush=True,
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry-queue", default=str(RETRY_QUEUE))
    parser.add_argument("--output", default=str(OUTPUT_FILE))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--temperature", type=float, default=0.4,
                        help="Slightly higher than default to encourage different output")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--resume", action="store_true", default=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    out_path = Path(args.output)

    if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        print("ERROR: AWS_BEARER_TOKEN_BEDROCK not set.", file=sys.stderr)
        sys.exit(1)

    # Load retry queue
    retry_rows = load_jsonl(Path(args.retry_queue))
    print(f"Retry queue: {len(retry_rows)} source_ids")

    # Build source_id -> correct mapping from existing mutation file
    print("Loading correct baselines...")
    sid_to_correct = build_source_id_to_correct([MUTATION_FILE_PRIMARY])
    print(f"  Found correct code for {len(sid_to_correct)} source_ids")

    # Match retry rows to their correct code
    jobs = []
    no_correct = []
    for row in retry_rows:
        sid = str(row.get("source_id") or "")
        correct = sid_to_correct.get(sid)
        if correct:
            jobs.append((row, correct))
        else:
            no_correct.append(sid)

    if no_correct:
        print(f"  WARNING: {len(no_correct)} source_ids have no correct baseline in mutation file")

    # Resume
    if args.resume:
        done_ids = load_done_ids(out_path)
        jobs = [(row, correct) for row, correct in jobs if str(row.get("source_id") or "") not in done_ids]
        print(f"  Already done (resume): {len(retry_rows) - len(no_correct) - len(jobs)}")

    if args.limit > 0:
        jobs = jobs[: args.limit]

    print(f"  Jobs to run: {len(jobs)}")

    if not jobs:
        print("Nothing to do.")
        return

    _stats["total"] = len(jobs)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    import boto3
    client = boto3.client("bedrock-runtime", region_name=args.region)

    print(f"\nRunning {args.workers} workers, model={args.model}, temp={args.temperature}")
    print(f"Output: {out_path}\n")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(process_row, row, correct, client, args.model,
                      args.temperature, args.max_tokens, out_path): row
            for row, correct in jobs
        }
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                print(f"Unhandled error: {exc}", file=sys.stderr)

    elapsed = time.time() - t0
    total_done = _stats["done"]
    print()
    print(f"Done in {elapsed:.1f}s  —  {total_done} rows processed")
    print(f"  Accepted: {_stats['accepted']}  ({100*_stats['accepted']/max(total_done,1):.1f}%)")
    print(f"  Rejected: {_stats['rejected']}")
    print(f"  Errors:   {_stats['error']}")
    print()
    print(f"Next step: add accepted pairs back to V4 with:")
    print(f"  python scripts/add_bedrock_mutations_to_v4.py --input {out_path}")


if __name__ == "__main__":
    main()
