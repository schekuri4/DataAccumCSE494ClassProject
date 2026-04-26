#!/usr/bin/env python3
"""
Use AWS Bedrock (DeepSeek) to fix AIE code that failed to compile.

Outputs TWO separate JSONL files:
  --out-synth   rows where the buggy code was AI-generated (synthetic taxonomy)
  --out-real    rows where the buggy code came from real GitHub source repos

Usage example:
  set AWS_BEARER_TOKEN_BEDROCK=bedrock-api-key-...
  python scripts/bedrock_fix_compile_failures.py \\
      --results  data/processed/v3/aie_instruction_v2_all_64w.jsonl \\
      --dataset  data/processed/archive_v2_and_synthpairs_20260425_014921/aie_instruction_v2_all.jsonl \\
      --out-synth data/processed/v3/bedrock_fixed_synth.jsonl \\
      --out-real  data/processed/v3/bedrock_fixed_real.jsonl \\
      --limit 500

Environment variables:
  AWS_BEARER_TOKEN_BEDROCK   Bearer token from Bedrock (bedrock-api-key-...)
  BEDROCK_REGION             AWS region (default: us-east-1)
"""

import argparse
import threading
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
BEARER_TOKEN = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")

BEDROCK_ENDPOINT = f"https://bedrock.{REGION}.amazonaws.com"
RUNTIME_ENDPOINT = f"https://bedrock-runtime.{REGION}.amazonaws.com"

# DeepSeek model candidates (tried in order; first working one is used)
DEEPSEEK_V3_CANDIDATES = [
    "deepseek.v3.2",          # DeepSeek V3.2 (confirmed on Bedrock)
    "us.deepseek.v3-0324-v1:0",
    "deepseek.deepseek-v3-20250324-v1:0",
]
DEEPSEEK_R1_CANDIDATES = [
    "deepseek.r1-v1:0",       # DeepSeek R1 (confirmed on Bedrock)
    "us.deepseek.r1-v1:0",
]

# Error strings we explicitly skip (toolchain env issues, not dataset problems)
SKIP_PATTERNS = [
    "PS data directory versal not found",
]

ALLOWED_ERROR_CLASSES = {
    "aie_api_compile_error",
    "api_version_mismatch",
}

MAX_CANDIDATE_ERROR_LINES = 80
MAX_PROMPT_ERROR_LINES = 12
MAX_PROMPT_ERROR_CHARS = 1500

COMPILE_ERROR_INCLUDE_PATTERNS = [
    r"no matching function for call",
    r"no viable conversion",
    r"call to non-static member function without an object argument",
    r"static assertion failed",
    r"implicit instantiation of undefined template 'aie::",
    r"not a direct or virtual base of 'aie::vector",
    r"no template named '[^']+' in namespace 'aie'",
    r"no member named '[^']+' in namespace 'aie'",
    r"candidate function not viable",
]

COMPILE_ERROR_EXCLUDE_PATTERNS = [
    r"fatal error:",
    r"use of undeclared identifier",
    r"unknown type name",
    r"expected namespace name",
    r"error: expected unqualified-id",
    r"adf::PLIO",
    r"kernel::create",
    r"connect<",
]

AIE_API_INCLUDE_PATTERNS = [
    r"no matching function for call",
    r"no viable conversion",
    r"call to non-static member function without an object argument",
    r"no template named '[^']+' in namespace 'aie'",
    r"no member named '[^']+' in namespace 'aie'",
    r"implicit instantiation of undefined template 'aie::",
    r"not a direct or virtual base of 'aie::vector",
]

AIE_API_EXCLUDE_PATTERNS = [
    r"invalid explicitly-specified argument for template parameter 'Resource'",
    r"constraints not satisfied for class template",
    r"use of undeclared identifier",
    r"unknown type name",
    r"error: expected unqualified-id",
]

MEANINGFUL_ERROR_PATTERNS = [
    r"\berror:",
    r"static assertion failed",
    r"no matching function for call",
    r"candidate function not viable",
    r"no viable conversion",
    r"call to non-static member function without an object argument",
    r"implicit instantiation of undefined template",
    r"no template named '[^']+' in namespace 'aie'",
    r"no member named '[^']+' in namespace 'aie'",
    r"not a direct or virtual base of 'aie::vector",
]

DEFAULT_WSL_DISTRO = "Ubuntu-24.04"
DEFAULT_WSL_VALIDATE_SCRIPT = Path("scripts/run_validate_wsl.sh")

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict:
    token = BEARER_TOKEN.strip()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _normalize_input_path(path: str | Path) -> str:
    text = str(path).strip().replace("\\", "/")
    m = text.lower()
    if m.startswith("/mnt/") and len(text) > 6 and text[5].isalpha() and text[6] == "/":
        text = f"{text[5].upper()}:/{text[7:]}"
    return text.lower()


def _dataset_key(input_path: str | Path, row_index: int) -> tuple[str, int]:
    return _normalize_input_path(input_path), int(row_index)


def _dataset_keys_for_path(input_path: str | Path, row_index: int) -> list[tuple[str, int]]:
    normalized = _normalize_input_path(input_path)
    keys = [_dataset_key(normalized, row_index)]
    basename = Path(str(input_path)).name.lower()
    if basename and basename != normalized:
        keys.append((basename, int(row_index)))
    return keys


def _to_wsl_path(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/")
    if len(text) >= 3 and text[1:3] == ":/":
        drive = text[0].lower()
        return f"/mnt/{drive}/{text[3:]}"
    return text


def _strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _format_cpp_response(code: str) -> str:
    body = _strip_code_fences(code)
    return f"```cpp\n{body}\n```"


def _tail(text: str, limit: int = 2000) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return "...\n" + text[-limit:]


def _line_count(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if line.strip())


def _extract_meaningful_error_excerpt(
    stderr: str,
    stdout: str,
    max_lines: int = MAX_PROMPT_ERROR_LINES,
    max_chars: int = MAX_PROMPT_ERROR_CHARS,
) -> str:
    primary = (stderr or "").strip()
    fallback = (stdout or "").strip()
    source = primary if primary else fallback
    if not source:
        return ""

    lines = [line.rstrip() for line in source.splitlines() if line.strip()]
    if not lines:
        return ""

    selected: list[str] = []
    for line in lines:
        if re.search(r"^(In file included from|from )", line):
            continue
        if re.search(r"\bnote:\b", line, re.IGNORECASE):
            continue
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in MEANINGFUL_ERROR_PATTERNS):
            selected.append(line)
        if len(selected) >= max_lines:
            break

    excerpt_lines = selected or lines[:max_lines]
    excerpt = "\n".join(excerpt_lines)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip()
    return excerpt


def _make_diff_excerpt(original_code: str, candidate_code: str, max_lines: int = 200) -> str:
    diff_lines = list(
        difflib.unified_diff(
            (original_code or "").splitlines(),
            (candidate_code or "").splitlines(),
            fromfile="original",
            tofile="candidate",
            lineterm="",
        )
    )
    if len(diff_lines) > max_lines:
        diff_lines = diff_lines[:max_lines] + ["... diff truncated ..."]
    return "\n".join(diff_lines)


def _append_attempt_log(log_fh, payload: dict) -> None:
    if log_fh is None:
        return
    log_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    log_fh.flush()


def discover_model(prefer_v3: bool = True) -> str:
    """Try to discover an available DeepSeek model on Bedrock."""
    try:
        resp = requests.get(
            f"{BEDROCK_ENDPOINT}/foundation-models",
            headers=_auth_headers(),
            params={"byProvider": "deepseek"},
            timeout=20,
        )
        if resp.ok:
            summaries = resp.json().get("modelSummaries", [])
            v3 = [m["modelId"] for m in summaries if "v3" in m["modelId"].lower()]
            r1 = [m["modelId"] for m in summaries if "r1" in m["modelId"].lower()]
            if prefer_v3 and v3:
                return v3[0]
            if r1:
                return r1[0]
            if summaries:
                return summaries[0]["modelId"]
    except Exception as exc:
        print(f"[warn] model discovery failed: {exc}", file=sys.stderr)

    candidates = DEEPSEEK_V3_CANDIDATES if prefer_v3 else DEEPSEEK_R1_CANDIDATES
    return candidates[0]


def probe_model(model_id: str) -> bool:
    """Send a tiny request to verify the model works."""
    url = f"{RUNTIME_ENDPOINT}/model/{model_id}/converse"
    body = {
        "messages": [{"role": "user", "content": [{"text": "hi"}]}],
        "inferenceConfig": {"maxTokens": 5},
    }
    try:
        resp = requests.post(url, headers=_auth_headers(), json=body, timeout=30)
        return resp.ok
    except Exception:
        return False


def converse(
    model_id: str,
    prompt: str,
    max_retries: int = 4,
    timeout_s: int = 180,
) -> tuple[Optional[str], Optional[str]]:
    """Call Bedrock Converse API. Returns (text, error_message)."""
    url = f"{RUNTIME_ENDPOINT}/model/{model_id}/converse"
    body = {
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {
            "maxTokens": 4096,
            "temperature": 0.05,
        },
    }
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                url,
                headers=_auth_headers(),
                json=body,
                timeout=(20, timeout_s),
            )
            if resp.ok:
                data = resp.json()
                text = data["output"]["message"]["content"][0]["text"]
                return text, None
            if resp.status_code == 429:
                wait = (2 ** attempt) * 5
                print(f"  [throttle] waiting {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code in (400, 404, 422):
                return None, f"HTTP {resp.status_code}: {resp.text[:300]}"
            # Other server error – retry
            time.sleep(2 ** attempt)
        except requests.Timeout:
            if attempt == max_retries - 1:
                return None, "timeout"
            time.sleep(2 ** attempt)
        except requests.RequestException as exc:
            if attempt == max_retries - 1:
                return None, str(exc)
            time.sleep(2 ** attempt)
        except Exception as exc:
            if attempt == max_retries - 1:
                return None, str(exc)
            time.sleep(2 ** attempt)
    return None, "max retries exceeded"


# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------

def load_original_dataset(*paths: str) -> dict[tuple[str, int], dict]:
    """Load original dataset rows keyed by (normalized input_path, row_index)."""
    mapping: dict[tuple[str, int], dict] = {}
    for p in paths:
        path = Path(p)
        if not path.exists():
            print(f"[warn] dataset file not found: {path}", file=sys.stderr)
            continue
        with path.open(encoding="utf-8") as fh:
            for idx, line in enumerate(fh):
                line = line.strip()
                if line:
                    row = json.loads(line)
                    for key in _dataset_keys_for_path(path.resolve(), idx):
                        mapping[key] = row
    print(f"Loaded {len(mapping)} original dataset rows", file=sys.stderr)
    return mapping


def is_synthetic(orig_row: dict) -> bool:
    """Return True if the row came from AI-synthesized code, not a real repo."""
    meta = orig_row.get("metadata") or {}
    source = (meta.get("source") or "").lower()
    repo = (meta.get("source_repo") or "").lower()
    category = (meta.get("category") or "").lower()
    return (
        bool(meta.get("synthetic"))
        or source.startswith("synthetic/")
        or "synthetic_taxonomy" in repo
        or "synthetic_taxonomy" in category
        or "bedrock_synth" in source
        or repo is None  # paranoia guard
    )


def should_skip(result_row: dict) -> bool:
    """Return True if this failure is a toolchain environment issue we want to ignore."""
    combined = (result_row.get("stdout_tail") or "") + (result_row.get("stderr_tail") or "")
    return any(pat in combined for pat in SKIP_PATTERNS)


def is_high_yield_candidate(result_row: dict) -> bool:
    error_class = (result_row.get("error_class") or "").strip()
    combined = ((result_row.get("stderr_tail") or "") + "\n" + (result_row.get("stdout_tail") or "")).strip()
    if not combined:
        return False
    if _line_count(combined) > MAX_CANDIDATE_ERROR_LINES:
        return False

    error_excerpt = _extract_meaningful_error_excerpt(
        result_row.get("stderr_tail") or "",
        result_row.get("stdout_tail") or "",
        max_lines=4,
        max_chars=600,
    )
    if not error_excerpt:
        return False

    if error_class in ALLOWED_ERROR_CLASSES:
        if any(re.search(pattern, error_excerpt, re.IGNORECASE) for pattern in AIE_API_EXCLUDE_PATTERNS):
            return False
        return any(re.search(pattern, error_excerpt, re.IGNORECASE) for pattern in AIE_API_INCLUDE_PATTERNS)
    if error_class != "compile_error":
        return False
    if (result_row.get("file_type") or "").strip() != "kernel":
        return False
    if any(re.search(pattern, combined, re.IGNORECASE) for pattern in COMPILE_ERROR_EXCLUDE_PATTERNS):
        return False
    return any(re.search(pattern, combined, re.IGNORECASE) for pattern in COMPILE_ERROR_INCLUDE_PATTERNS)


def lookup_original_row(orig_map: dict[tuple[str, int], dict], result_row: dict) -> dict | None:
    row_idx = result_row.get("row_index", -1)
    for orig_key in _dataset_keys_for_path(result_row.get("input_path", ""), row_idx):
        orig_row = orig_map.get(orig_key)
        if orig_row is not None:
            return orig_row
    return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_fix_prompt(
    result_row: dict,
    orig_row: dict,
    previous_fix: str | None = None,
    previous_validation_error: str | None = None,
) -> str:
    """Build a prompt asking DeepSeek to fix the failing AIE code."""
    code = (orig_row.get("response") or "").strip()
    instruction = (orig_row.get("instruction") or "").strip()

    # Prefer stderr if it has actual error lines, otherwise use stdout
    stderr = (result_row.get("stderr_tail") or "").strip()
    stdout = (result_row.get("stdout_tail") or "").strip()
    error_text = _extract_meaningful_error_excerpt(stderr, stdout)

    file_type = result_row.get("file_type", "kernel")
    target = result_row.get("target", "AIE")

    prompt = (
        f"You are an expert in AMD/Xilinx Versal AI Engine ({target}) C++ programming.\n\n"
        f"The following {file_type} code fails to compile. "
        f"Fix ALL compiler errors and return ONLY the complete fixed C++ code — "
        f"no explanations, no markdown fences, no extra text.\n\n"
        f"Original task description:\n{instruction}\n\n"
        f"Compiler error output:\n```\n{error_text}\n```\n\n"
        f"Code to fix:\n```cpp\n{code}\n```"
    )
    if previous_fix and previous_validation_error:
        prompt += (
            "\n\nYour previous fix attempt still failed local compilation. "
            "Revise it and return ONLY the full corrected C++ code.\n\n"
            f"Previous attempted fix:\n```cpp\n{previous_fix}\n```\n\n"
            f"Validation compile error:\n```\n{_extract_meaningful_error_excerpt(previous_validation_error or '', '', max_chars=1500)}\n```"
        )
    return prompt


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def make_output_row(result_row: dict, orig_row: dict, fixed_code: str, model_id: str) -> dict:
    """Construct the final output row in instruction-tuning format."""
    orig_meta = (orig_row.get("metadata") or {}).copy()
    return {
        "instruction": orig_row.get("instruction", ""),
        "context": orig_row.get("context", ""),
        "response": fixed_code,
        "metadata": {
            **orig_meta,
            "bedrock_fix": {
                "model": model_id,
                "original_compile_ok": False,
                "original_error_class": result_row.get("error_class"),
                "file_type": result_row.get("file_type"),
                "target": result_row.get("target"),
                "compiler": result_row.get("compiler"),
                "row_index": result_row.get("row_index"),
            },
        },
    }


def validate_fixed_code(
    result_row: dict,
    orig_row: dict,
    fixed_code: str,
    args: argparse.Namespace,
) -> dict:
    temp_root = Path("data/processed/v3/.bedrock_validate_tmp")
    temp_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bedrock_validate_", dir=str(temp_root.resolve())) as td:
        temp_dir = Path(td)
        input_path = temp_dir / "candidate.jsonl"
        out_path = temp_dir / "candidate_results.jsonl"

        candidate_row = dict(orig_row)
        candidate_row["response"] = _format_cpp_response(fixed_code)
        input_path.write_text(json.dumps(candidate_row, ensure_ascii=False) + "\n", encoding="utf-8")

        validate_script = Path(args.wsl_validate_script)
        if not validate_script.is_absolute():
            validate_script = Path.cwd() / validate_script

        cmd = [
            "wsl",
            "-d",
            args.wsl_distro,
            "--",
            "bash",
            _to_wsl_path(validate_script),
            "--input",
            _to_wsl_path(input_path),
            "--out",
            _to_wsl_path(out_path),
            "--workers",
            "1",
            "--scope",
            "correct",
            "--target",
            result_row.get("target", "AIE"),
            "--timeout",
            str(args.validate_timeout),
            "--limit",
            "1",
        ]

        try:
            cp = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=args.validate_timeout + 180,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "compile_ok": False,
                "error_class": "validation_timeout",
                "compiler": "wsl-validator",
                "return_code": -9,
                "stdout_tail": _tail(exc.stdout or ""),
                "stderr_tail": f"validation timeout after {args.validate_timeout + 180}s\n{_tail(exc.stderr or '')}",
            }

        if out_path.exists():
            with out_path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        return json.loads(line)

        return {
            "compile_ok": False,
            "error_class": "validation_driver_error",
            "compiler": "wsl-validator",
            "return_code": cp.returncode,
            "stdout_tail": _tail(cp.stdout),
            "stderr_tail": _tail(cp.stderr),
        }


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as fh:
        return sum(1 for l in fh if l.strip())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True,
                        help="Compile results JSONL (e.g. v3/aie_instruction_v2_all_64w.jsonl)")
    parser.add_argument("--dataset", required=True, nargs="+",
                        help="Original dataset JSONL(s) to look up row content")
    parser.add_argument("--out-synth", required=True,
                        help="Output JSONL for AI-synthesized rows fixed by DeepSeek")
    parser.add_argument("--out-real", required=True,
                        help="Output JSONL for real-source rows fixed by DeepSeek")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process at most this many rows (0 = all)")
    parser.add_argument("--model", default="",
                        help="Explicit Bedrock model ID to use (skips auto-discovery)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip rows already present in output files")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of parallel threads (default 4)")
    parser.add_argument("--fix-retries", type=int, default=1,
                        help="Retries after the initial fix attempt when local validation fails (default: 1 retry = 2 total attempts).")
    parser.add_argument("--bedrock-timeout", type=int, default=180,
                        help="Bedrock read timeout in seconds per request.")
    parser.add_argument("--wsl-distro", default=DEFAULT_WSL_DISTRO,
                        help="WSL distro name used for local compile validation.")
    parser.add_argument("--wsl-validate-script", default=str(DEFAULT_WSL_VALIDATE_SCRIPT),
                        help="Path to the WSL validation wrapper script.")
    parser.add_argument("--validate-timeout", type=int, default=60,
                        help="Per-row local compile validation timeout in seconds.")
    parser.add_argument("--attempt-log", default="",
                        help="Optional JSONL file to record each fix attempt, diff, and validation error.")
    args = parser.parse_args()

    if not BEARER_TOKEN:
        print("ERROR: AWS_BEARER_TOKEN_BEDROCK environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    # --- Discover model -------------------------------------------------------
    model_id = args.model or discover_model(prefer_v3=True)
    print(f"Using model: {model_id}")
    if not probe_model(model_id):
        # Try fallback candidates
        fallbacks = DEEPSEEK_V3_CANDIDATES + DEEPSEEK_R1_CANDIDATES
        for cand in fallbacks:
            if cand != model_id:
                print(f"  probing fallback: {cand} ...", end=" ", flush=True)
                if probe_model(cand):
                    model_id = cand
                    print("OK")
                    break
                print("FAIL")
        else:
            print("ERROR: No DeepSeek model responded successfully.", file=sys.stderr)
            sys.exit(1)
    print(f"Confirmed model: {model_id}")

    # --- Load data ------------------------------------------------------------
    orig_map = load_original_dataset(*args.dataset)

    results_path = Path(args.results)
    with results_path.open(encoding="utf-8") as fh:
        all_results = [json.loads(l) for l in fh if l.strip()]

    # Filter: failed AND not a toolchain-env skip AND we have original row
    candidates: list[dict] = []
    filtered_missing_original = 0
    filtered_non_synth = 0
    filtered_non_kernel = 0
    filtered_low_yield = 0
    for result_row in all_results:
        if result_row.get("compile_ok") or should_skip(result_row):
            continue
        orig_row = lookup_original_row(orig_map, result_row)
        if orig_row is None:
            filtered_missing_original += 1
            continue
        if not is_synthetic(orig_row):
            filtered_non_synth += 1
            continue
        if (result_row.get("file_type") or "").strip() != "kernel":
            filtered_non_kernel += 1
            continue
        if not is_high_yield_candidate(result_row):
            filtered_low_yield += 1
            continue
        candidates.append(result_row)

    print(f"Total compile results: {len(all_results)}")
    print(f"Skipped (PS data dir etc.): {sum(1 for r in all_results if should_skip(r))}")
    print(f"Filtered missing original rows: {filtered_missing_original}")
    print(f"Filtered non-synthetic rows: {filtered_non_synth}")
    print(f"Filtered non-kernel rows: {filtered_non_kernel}")
    print(f"Filtered low-yield failures: {filtered_low_yield}")
    print(f"Actionable failures: {len(candidates)}")

    if args.limit > 0:
        candidates = candidates[: args.limit]
        print(f"Limited to: {len(candidates)}")

    # --- Resume: collect already-done row_indices ----------------------------
    done_indices: set[int] = set()
    synth_path = Path(args.out_synth)
    real_path = Path(args.out_real)
    if args.resume:
        for out_p in (synth_path, real_path):
            if out_p.exists():
                with out_p.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            row = json.loads(line)
                            ri = (row.get("metadata") or {}).get("bedrock_fix", {}).get("row_index")
                            if ri is not None:
                                done_indices.add(ri)
        print(f"Resuming: {len(done_indices)} rows already done, skipping.")
        candidates = [c for c in candidates if c.get("row_index") not in done_indices]

    print(f"Rows to process: {len(candidates)}")

    # --- Open output files ---------------------------------------------------
    synth_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.resume else "w"
    synth_fh = synth_path.open(mode, encoding="utf-8")
    real_fh = real_path.open(mode, encoding="utf-8")
    attempt_log_fh = None
    if args.attempt_log:
        attempt_log_path = Path(args.attempt_log)
        attempt_log_path.parent.mkdir(parents=True, exist_ok=True)
        attempt_log_fh = attempt_log_path.open(mode, encoding="utf-8")

    # --- Process -------------------------------------------------------------
    ok_count = 0
    err_count = 0
    synth_count = 0
    real_count = 0
    interrupted = False

    def process_one(item: tuple[int, dict]) -> dict:
        index, result_row = item
        row_idx = result_row.get("row_index", -1)
        orig_row = lookup_original_row(orig_map, result_row)
        if orig_row is None:
            return {
                "index": index,
                "row_index": row_idx,
                "kind": "unknown",
                "file_type": result_row.get("file_type"),
                "attempt_logs": [],
                "fixed_code": None,
                "validation": None,
                "error": "missing original dataset row",
                "attempts": 0,
                "synthetic": False,
                "orig_row": None,
            }

        original_code = _strip_code_fences(orig_row.get("response") or "")
        synthetic = is_synthetic(orig_row)
        kind = "synth" if synthetic else "real"
        fixed_code = None
        validation = None
        error = None
        previous_fix = None
        previous_validation_error = None
        max_attempts = args.fix_retries + 1
        attempt_logs: list[dict] = []

        for attempt in range(1, max_attempts + 1):
            prompt = build_fix_prompt(
                result_row,
                orig_row,
                previous_fix=previous_fix,
                previous_validation_error=previous_validation_error,
            )
            try:
                candidate_code, error = converse(
                    model_id,
                    prompt,
                    timeout_s=args.bedrock_timeout,
                )
            except KeyboardInterrupt:
                raise

            if not candidate_code:
                attempt_logs.append({
                    "row_index": row_idx,
                    "attempt": attempt,
                    "source_kind": kind,
                    "file_type": result_row.get("file_type"),
                    "target": result_row.get("target"),
                    "compile_ok": False,
                    "bedrock_error": error or "empty_response",
                    "prompt_error_excerpt": _extract_meaningful_error_excerpt(
                        result_row.get("stderr_tail") or "",
                        result_row.get("stdout_tail") or "",
                    ),
                })
                continue

            candidate_code = _strip_code_fences(candidate_code)
            validation = validate_fixed_code(result_row, orig_row, candidate_code, args)
            attempt_logs.append({
                "row_index": row_idx,
                "attempt": attempt,
                "source_kind": kind,
                "file_type": result_row.get("file_type"),
                "target": result_row.get("target"),
                "compile_ok": bool(validation.get("compile_ok")),
                "validation_error_class": validation.get("error_class"),
                "validation_compiler": validation.get("compiler"),
                "validation_return_code": validation.get("return_code"),
                "prompt_error_excerpt": _extract_meaningful_error_excerpt(
                    result_row.get("stderr_tail") or "",
                    result_row.get("stdout_tail") or "",
                ),
                "validation_error_excerpt": _extract_meaningful_error_excerpt(
                    validation.get("stderr_tail") or "",
                    validation.get("stdout_tail") or "",
                ),
                "candidate_code": candidate_code,
                "candidate_diff": _make_diff_excerpt(original_code, candidate_code),
            })
            if validation.get("compile_ok"):
                fixed_code = candidate_code
                break

            previous_fix = candidate_code
            previous_validation_error = (
                (validation.get("stderr_tail") or "")
                or (validation.get("stdout_tail") or "")
                or f"validation failed with class={validation.get('error_class')}"
            )

        return {
            "index": index,
            "row_index": row_idx,
            "kind": kind,
            "file_type": result_row.get("file_type"),
            "attempt_logs": attempt_logs,
            "fixed_code": fixed_code,
            "validation": validation,
            "error": error,
            "attempts": len(attempt_logs),
            "synthetic": synthetic,
            "orig_row": orig_row,
            "result_row": result_row,
        }

    try:
        items = list(enumerate(candidates, 1))
        if args.workers <= 1:
            result_iter = (process_one(item) for item in items)
        else:
            pool = ThreadPoolExecutor(max_workers=args.workers)
            future_map = {pool.submit(process_one, item): item for item in items}
            result_iter = (future.result() for future in as_completed(future_map))

        processed = 0
        for outcome in result_iter:
            processed += 1
            row_idx = outcome["row_index"]
            kind = outcome["kind"]
            file_type = outcome["file_type"]
            validation = outcome["validation"]
            fixed_code = outcome["fixed_code"]
            error = outcome["error"]
            attempts = outcome["attempts"]
            synthetic = outcome["synthetic"]
            orig_row = outcome["orig_row"]
            result_row = outcome.get("result_row")

            for attempt_log in outcome["attempt_logs"]:
                _append_attempt_log(attempt_log_fh, attempt_log)

            if fixed_code and validation and result_row and orig_row:
                out_row = make_output_row(result_row, orig_row, fixed_code, model_id)
                out_row["metadata"]["bedrock_fix"].update({
                    "validated_compile_ok": True,
                    "validated_error_class": validation.get("error_class"),
                    "validated_compiler": validation.get("compiler"),
                    "validated_return_code": validation.get("return_code"),
                    "attempts": attempts,
                    "source_kind": kind,
                })
                line = json.dumps(out_row, ensure_ascii=False)
                if synthetic:
                    synth_fh.write(line + "\n")
                    synth_fh.flush()
                    synth_count += 1
                else:
                    real_fh.write(line + "\n")
                    real_fh.flush()
                    real_count += 1
                ok_count += 1
                detail = f"OK ({len(fixed_code)} chars, validated, tries={attempts})"
            else:
                err_count += 1
                if validation:
                    fail_class = validation.get("error_class") or "validation_failed"
                else:
                    fail_class = error or "bedrock_failed"
                detail = f"FAIL: {fail_class} (tries={attempts})"

            print(
                f"[{processed}/{len(candidates)}] row={row_idx} kind={kind} file_type={file_type} ... {detail}",
                flush=True,
            )

            if processed % 10 == 0:
                print(
                    f"  --- progress: {processed}/{len(candidates)} done, "
                    f"ok={ok_count} err={err_count} "
                    f"synth={synth_count} real={real_count} ---"
                )
                time.sleep(0.5)

        if interrupted:
            print("Run interrupted; partial validated outputs were preserved for --resume.")

    finally:
        if 'pool' in locals():
            pool.shutdown(wait=False, cancel_futures=True)
        synth_fh.close()
        real_fh.close()
        if attempt_log_fh is not None:
            attempt_log_fh.close()

    print()
    print("=" * 60)
    print(f"Done. Processed {ok_count + err_count} rows.")
    print(f"  OK:    {ok_count}  (synth={synth_count}, real={real_count})")
    print(f"  Error: {err_count}")
    print(f"  Output synth: {synth_path}  ({count_lines(synth_path)} rows)")
    print(f"  Output real:  {real_path}  ({count_lines(real_path)} rows)")


if __name__ == "__main__":
    main()
