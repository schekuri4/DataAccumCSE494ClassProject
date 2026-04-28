#!/usr/bin/env python3
"""Generate buggy AIE projects from compile-validated CORRECT-only Bedrock rows.

This is intentionally separate from bedrock_synth_taxonomy.py. It reads rows
where compile_ok=true and correct is present, calls Bedrock in parallel, and
writes paired rows containing both buggy and correct code.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bedrock_synth_taxonomy import (  # noqa: E402
    SYSTEM_PROMPT,
    _full_code_requirements,
    _is_quality_ok,
    estimate_cost,
)

DEFAULT_INPUT = ROOT / "data" / "processed" / "v3" / "bedrock_compile_validated_correct_full_budget80.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "v3" / "bedrock_buggy_from_compile_validated_correct.jsonl"
DEFAULT_V5_OUTPUT = ROOT / "data" / "processed" / "v5" / "bedrock_compile_bug_rows_v5.jsonl"
DEFAULT_MODEL = "moonshotai.kimi-k2.5"
DEFAULT_REGION = "us-east-1"
DEFAULT_WORKERS = 12
DEFAULT_MAX_TOKENS = 4096
MAX_RETRIES = 4
RETRY_BASE_DELAY = 2.0

BUGGY_RE = re.compile(r"<BUGGY>(.*?)</BUGGY>", re.DOTALL)

MUTATION_TEMPLATE = """\
You are given a CORRECT AIE mini-project that already compiled with Vitis AIE.
Create a BUGGY version for a compile-error bug-fix training pair.

Preferred bug category: {label}
Slug                  : {slug}
Tier                  : {tier}
Variant               : {variant_idx}

Important:
- Introduce exactly one realistic COMPILE-TIME bug matching the preferred bug category.
- If that exact category does not fit this code naturally, introduce exactly one
    different realistic AIE compile-time bug instead. Do NOT skip.
- Keep the same file markers and file layout.
- Make the smallest source edit that creates an xchesscc/aiecompiler failure.
- The buggy code MUST NOT compile. Semantic-only wrong-output bugs are rejected.
- Prefer AIE-kernel-specific compile failures: wrong stream pointer/reference,
    invalid readincr_v/writeincr_v lane count, aie::vector type/lane mismatch,
    accumulator conversion error, missing aie_api include, invalid load_v/store_v,
    input_buffer/output_buffer signature mismatch, unsupported AIE intrinsic, or
    graph-to-kernel port type mismatch.
- Do not add comments explaining the bug. Existing innocent comments may stay.
- Do not use markdown fences. Return ONLY this XML block:

<BUGGY>
...full buggy project with the same // FILE: markers...
</BUGGY>

Correct project:
<CORRECT>
{correct}
</CORRECT>
"""


INSTRUCTION_VARIANTS = [
    "Fix the AIE compile error. Return a unified diff only.",
    "Repair this AIE mini-project so it compiles. Return only the patch.",
    "Find the xchesscc/AIE compiler issue and provide the minimal unified diff fix.",
    "Correct the compile-time bug in this AIE code. Respond with a unified diff.",
    "Patch this AIE project to resolve the compiler error. Return the diff only.",
]


def make_client(region: str):
    import boto3

    if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        raise RuntimeError("AWS_BEARER_TOKEN_BEDROCK is not set in this process.")
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
            retryable = "ThrottlingException" in msg or "ServiceUnavailable" in msg or "TooManyRequests" in msg
            if retryable and attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("max retries exceeded")


def stable_source_id(row: dict) -> str:
    payload = json.dumps(
        {
            "slug": row.get("slug"),
            "variant_idx": row.get("variant_idx"),
            "correct": row.get("correct"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def strip_optional_fences(text: str) -> str:
    text = text.strip()
    for lang in ("```cpp", "```c++", "```c", "```"):
        text = text.removeprefix(lang).strip()
    return text.removesuffix("```").strip()


def parse_buggy_project(text: str, correct: str) -> tuple[str | None, list[str]]:
    match = BUGGY_RE.search(text)
    buggy = match.group(1).strip() if match else text.strip()
    buggy = strip_optional_fences(buggy)
    if not buggy:
        return None, ["empty_buggy_project"]
    if buggy == correct:
        return None, ["buggy_same_as_correct"]
    if "// FILE:" not in buggy:
        return None, ["missing_file_markers"]

    correct_files = set(re.findall(r"^\s*//\s*FILE:\s*(.+?)\s*$", correct, flags=re.MULTILINE))
    buggy_files = set(re.findall(r"^\s*//\s*FILE:\s*(.+?)\s*$", buggy, flags=re.MULTILINE))
    if correct_files != buggy_files:
        return None, ["file_layout_changed"]

    ok, reasons = _full_code_requirements(buggy)
    if not ok:
        # Syntax/compile-breaking mutations can still be useful, but they must
        # retain the mini-project structure. Store the reasons for audit.
        structural_reasons = reasons
    else:
        structural_reasons = []

    if not _is_quality_ok(buggy, correct):
        return None, ["quality_gate_failed"]
    return buggy, structural_reasons


def load_compile_validated_corrects(path: Path, slugs: set[str] | None, limit: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for line in path.open("r", encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("compile_ok") is not True:
            continue
        if not row.get("correct"):
            continue
        if slugs and row.get("slug") not in slugs:
            continue
        source_id = stable_source_id(row)
        if source_id in seen:
            continue
        seen.add(source_id)
        row = dict(row)
        row["source_id"] = source_id
        rows.append(row)
        if limit > 0 and len(rows) >= limit:
            break
    return rows


def load_done_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    for line in path.open("r", encoding="utf-8"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("parse_ok") and row.get("buggy") and row.get("correct"):
            source_id = row.get("source_id") or (row.get("metadata") or {}).get("source_id")
            if source_id:
                done.add(str(source_id))
    return done


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _workspace_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def validate_buggy_project_wsl(code: str, *, wsl_distro: str, timeout_s: int) -> dict:
    temp_dir = ROOT / "data" / "processed" / "v5" / ".bedrock_buggy_validate_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    stem = f"candidate_{hashlib.sha1(code.encode('utf-8', errors='replace')).hexdigest()[:16]}_{int(time.time() * 1000)}"
    input_path = temp_dir / f"{stem}.jsonl"
    out_path = temp_dir / f"{stem}_results.jsonl"

    input_row = {
        "instruction": "Compile-check Bedrock generated buggy AIE mini-project.",
        "context": "Buggy version:\n" + code.rstrip(),
        "response": "",
        "metadata": {"source": "bedrock_compile_bug_probe"},
    }
    input_path.write_text(json.dumps(input_row, ensure_ascii=False) + "\n", encoding="utf-8")

    cmd = [
        "wsl",
        "-d",
        wsl_distro,
        "--",
        "bash",
        "scripts/run_validate_wsl.sh",
        "--input",
        _workspace_relative(input_path),
        "--out",
        _workspace_relative(out_path),
        "--scope",
        "buggy",
        "--target",
        "AIE",
        "--workers",
        "1",
        "--timeout",
        str(timeout_s),
        "--limit",
        "1",
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s + 240,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "compile_ok": False,
            "error_class": "validation_timeout",
            "return_code": -9,
            "stdout_tail": (exc.stdout or "")[-2000:],
            "stderr_tail": f"validation timeout after {timeout_s + 240}s\n{(exc.stderr or '')[-4000:]}",
        }

    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return json.loads(line)

    return {
        "compile_ok": False,
        "error_class": "validation_driver_error",
        "return_code": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def format_error_log(validation: dict) -> str:
    stderr = str(validation.get("stderr_tail") or "").strip()
    stdout = str(validation.get("stdout_tail") or "").strip()
    error_class = str(validation.get("error_class") or "compile_error").strip()
    text = stderr or stdout or error_class
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        lines.append(stripped)
    if not lines:
        lines = [error_class]
    return "\n".join(lines[:80])


def unified_diff_response(buggy: str, correct: str) -> str:
    return "".join(difflib.unified_diff(
        buggy.rstrip().splitlines(keepends=True),
        correct.rstrip().splitlines(keepends=True),
        fromfile="a/aie_source",
        tofile="b/aie_source",
        lineterm="",
    )).rstrip() + "\n"


def make_v5_row(row: dict, buggy: str, correct: str, validation: dict, model: str) -> dict:
    source_id = str(row.get("source_id") or stable_source_id(row))
    instruction = INSTRUCTION_VARIANTS[int(source_id[:8], 16) % len(INSTRUCTION_VARIANTS)]
    return {
        "instruction": instruction,
        "context": buggy.rstrip() + "\n\n--- Error Log ---\n" + format_error_log(validation),
        "response": unified_diff_response(buggy, correct),
        "metadata": {
            "variant": "v5_bedrock_compile_validated_correct_to_compile_error",
            "split": "train" if (int(source_id, 16) % 100) >= 13 else "validation",
            "group_id": f"bedrock_compile_bug:{source_id}",
            "bug_type": "bedrock_compile_bug",
            "bug_label": row.get("label"),
            "source": "bedrock_compile_bug_from_compile_validated_correct",
            "source_id": source_id,
            "slug": row.get("slug"),
            "tier": row.get("tier"),
            "variant_idx": row.get("variant_idx"),
            "model": model,
            "synthetic": True,
            "response_format": "unified_diff",
            "has_real_error_log": True,
            "correct_compile_ok": True,
            "buggy_compile_ok": False,
            "buggy_error_class": validation.get("error_class"),
            "v5_bucket": "bedrock_compile_error_fix_pair",
        },
    }


def make_prompt(row: dict) -> str:
    return MUTATION_TEMPLATE.format(
        label=row.get("label") or row.get("slug") or "AIE bug",
        slug=row.get("slug") or "unknown_slug",
        tier=row.get("tier") or "unknown",
        variant_idx=row.get("variant_idx"),
        correct=str(row.get("correct") or "").rstrip(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION))
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--max-budget-usd", type=float, default=0.0, help="0 disables budget cap")
    parser.add_argument("--limit", type=int, default=0, help="0 means all rows")
    parser.add_argument("--slugs", nargs="+")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-buggy-compile-fail", action="store_true",
                        help="Validate each mutation with WSL and accept only rows where buggy code does not compile.")
    parser.add_argument("--wsl-distro", default="Ubuntu-24.04")
    parser.add_argument("--validate-timeout", type=int, default=60)
    parser.add_argument("--v5-output", default=str(DEFAULT_V5_OUTPUT),
                        help="Optional V5-format JSONL output for accepted compile-failing mutations.")
    parser.add_argument("--no-v5-output", action="store_true", help="Do not write V5-format rows.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    slugs = set(args.slugs) if args.slugs else None
    rows = load_compile_validated_corrects(input_path, slugs, args.limit)
    done = load_done_ids(output_path) if args.resume else set()
    jobs = [row for row in rows if row["source_id"] not in done]

    print(f"Input compile-valid correct rows: {len(rows)}")
    print(f"Already mutated via --resume: {len(done)}")
    print(f"Jobs to run: {len(jobs)}")
    print(f"Workers: {args.workers}")
    print(f"Output: {output_path}")
    if args.require_buggy_compile_fail:
        print(f"Buggy validation: required compile failure via WSL distro {args.wsl_distro}")
    if not args.no_v5_output:
        print(f"V5 rows output: {Path(args.v5_output)}")

    if args.dry_run:
        if jobs:
            print("\n--- Prompt preview ---")
            print(make_prompt(jobs[0])[:4000])
        return

    client = make_client(args.region)
    write_lock = threading.Lock()
    counter_lock = threading.Lock()
    budget_lock = threading.Lock()
    completed = 0
    successes = 0
    failures = 0
    total_in = 0
    total_out = 0
    total_cost = 0.0
    budget_stopped = False

    def process(row: dict) -> dict:
        nonlocal total_in, total_out, total_cost
        with budget_lock:
            current_cost = total_cost
        if args.max_budget_usd > 0 and current_cost >= args.max_budget_usd:
            return {"status": "budget", "row": row, "cost": current_cost}

        prompt = make_prompt(row)
        try:
            text, in_tok, out_tok = call_with_retry(client, args.model, prompt, args.temperature, args.max_tokens)
        except Exception as exc:
            out_row = {
                "source_id": row["source_id"],
                "slug": row.get("slug"),
                "label": row.get("label"),
                "tier": row.get("tier"),
                "variant_idx": row.get("variant_idx"),
                "buggy": None,
                "correct": row.get("correct"),
                "raw_response": "",
                "model": args.model,
                "ts": datetime.now(timezone.utc).isoformat(),
                "parse_ok": False,
                "full_code_ok": False,
                "full_code_reasons": {"buggy": [f"bedrock_error: {exc}"], "correct": []},
                "bedrock_error": str(exc),
            }
            with write_lock:
                append_jsonl(output_path, out_row)
            return {"status": "fail", "row": row, "reasons": [str(exc)], "in_tok": 0, "out_tok": 0}

        cost = estimate_cost(args.model, in_tok, out_tok)
        with budget_lock:
            total_in += in_tok
            total_out += out_tok
            total_cost += cost

        correct = str(row.get("correct") or "")
        buggy, reasons = parse_buggy_project(text, correct)
        parse_ok = buggy is not None
        full_code_ok = parse_ok and not reasons
        buggy_validation = None
        v5_written = False
        if parse_ok and args.require_buggy_compile_fail:
            buggy_validation = validate_buggy_project_wsl(
                str(buggy),
                wsl_distro=args.wsl_distro,
                timeout_s=args.validate_timeout,
            )
            if buggy_validation.get("compile_ok") is True:
                parse_ok = False
                full_code_ok = False
                reasons = list(reasons) + ["buggy_compiled_clean"]

        out_row = {
            "source_id": row["source_id"],
            "slug": row.get("slug"),
            "label": row.get("label"),
            "tier": row.get("tier"),
            "variant_idx": row.get("variant_idx"),
            "buggy": buggy if parse_ok else None,
            "correct": correct,
            "preferred_bug_type": row.get("slug"),
            "preferred_bug_label": row.get("label"),
            "model": args.model,
            "ts": datetime.now(timezone.utc).isoformat(),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "estimated_cost_usd": round(cost, 6),
            "parse_ok": parse_ok,
            "quality_ok": bool(parse_ok),
            "full_code_ok": bool(full_code_ok),
            "full_code_reasons": {"buggy": reasons, "correct": []},
            "correct_compile_ok": True,
            "buggy_compile_ok": None if buggy_validation is None else bool(buggy_validation.get("compile_ok")),
            "buggy_compile_validation": buggy_validation,
            "mutation_source": "bedrock_compile_validated_correct",
        }
        if not parse_ok:
            out_row["raw_response"] = text[:8000]
        with write_lock:
            append_jsonl(output_path, out_row)
            if parse_ok and buggy_validation is not None and not args.no_v5_output:
                append_jsonl(Path(args.v5_output), make_v5_row(row, str(buggy), correct, buggy_validation, args.model))
                v5_written = True
        return {
            "status": "ok" if parse_ok else "fail",
            "row": row,
            "reasons": reasons,
            "in_tok": in_tok,
            "out_tok": out_tok,
            "cost": cost,
            "v5_written": v5_written,
        }

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(process, row): row for row in jobs}
        for future in as_completed(futures):
            result = future.result()
            row = result["row"]
            with counter_lock:
                completed += 1
                if result["status"] == "ok":
                    successes += 1
                    print(f"[{completed}/{len(jobs)}] {row.get('slug')} v={row.get('variant_idx')} MUTATED")
                elif result["status"] == "budget":
                    budget_stopped = True
                    print(f"[{completed}/{len(jobs)}] budget cap reached (${result['cost']:.4f})")
                else:
                    failures += 1
                    print(f"[{completed}/{len(jobs)}] {row.get('slug')} v={row.get('variant_idx')} FAIL {result.get('reasons', [])[:2]}")

    print("\n--- Summary ---")
    print(f"Mutated pairs: {successes}  Failures: {failures}  Budget stopped: {budget_stopped}")
    print(f"Tokens: {total_in:,} in / {total_out:,} out")
    print(f"Estimated cost: ${total_cost:.4f}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
