#!/usr/bin/env python3
"""Enrich V4 rows with real xchesscc error logs via WSL.

Compiles every buggy code snippet through xchesscc (--scope buggy), then
replaces the heuristic "--- Error Log ---" section with the real stderr output.

The validator output format:
  One JSONL line per compile job with fields:
    row_index   - 0-based position in the input JSONL (our key for mapping back)
    compile_ok  - bool
    stderr_tail - compiler stderr (trimmed to 4000 chars)
    error_class - short error category string

Usage:
  python scripts/enrich_v4_with_wsl_errors.py
  python scripts/enrich_v4_with_wsl_errors.py --workers 30 --dry-run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4_ALL = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_all.jsonl"
V4_TRAIN = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_train.jsonl"
V4_VAL = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_validation.jsonl"

SCRATCH_DIR = ROOT / "data" / "processed" / "v4" / ".wsl_enrich_tmp"
INPUT_FILE = SCRATCH_DIR / "enrich_input.jsonl"
OUTPUT_FILE = SCRATCH_DIR / "enrich_output.jsonl"
ERROR_LOG_SEPARATOR = "\n\n--- Error Log ---\n"

DEFAULT_WORKERS = 30
DEFAULT_WSL_DISTRO = "Ubuntu-24.04"
DEFAULT_TIMEOUT = 60


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def extract_buggy_code(context: str) -> str:
    if ERROR_LOG_SEPARATOR in context:
        return context.split(ERROR_LOG_SEPARATOR, 1)[0].strip()
    return context.strip()


def format_error_log(compile_result: dict) -> str | None:
    """Extract meaningful error lines from a compile result.

    Returns None if compiled clean (keep heuristic log untouched).
    """
    if compile_result.get("compile_ok"):
        return None

    # The validator field is stderr_tail (not stderr)
    stderr = (compile_result.get("stderr_tail") or "").strip()
    error_class = (compile_result.get("error_class") or "").strip()

    lines = []
    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(kw in stripped for kw in ("error:", "Error:", "fatal error:", "ERROR:", "warning:")):
            lines.append(stripped)
            if len(lines) >= 8:
                break

    if lines:
        return "\n".join(lines)
    # Fall back to first 8 non-empty lines of stderr
    if stderr:
        fallback = [l.strip() for l in stderr.splitlines() if l.strip()][:8]
        if fallback:
            return "\n".join(fallback)
    if error_class and error_class not in ("ok", ""):
        return f"Compile error: {error_class}"
    return None


def patch_error_log(row: dict, error_log: str) -> dict:
    ctx = row.get("context", "")
    buggy_code = ctx.split(ERROR_LOG_SEPARATOR, 1)[0] if ERROR_LOG_SEPARATOR in ctx else ctx
    new_row = dict(row)
    new_row["context"] = buggy_code + ERROR_LOG_SEPARATOR + error_log
    meta = dict(row.get("metadata") or {})
    meta["has_real_error_log"] = True
    new_row["metadata"] = meta
    return new_row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--wsl-distro", default=DEFAULT_WSL_DISTRO)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reuse-output", action="store_true",
                    help="Skip WSL run and just re-parse existing output file")
    args = ap.parse_args()

    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading V4...")
    all_rows = load_jsonl(V4_ALL)
    print(f"  {len(all_rows)} total rows")

    needs_enrich = [
        (i, r) for i, r in enumerate(all_rows)
        if not r.get("metadata", {}).get("has_real_error_log")
        and r.get("metadata", {}).get("response_format") == "unified_diff"
        and extract_buggy_code(r.get("context", ""))
    ]
    print(f"  Already enriched: {len(all_rows) - len(needs_enrich)}")
    print(f"  Needs WSL compile: {len(needs_enrich)}")

    if not needs_enrich:
        print("Nothing to do.")
        return

    est_min = (len(needs_enrich) / args.workers) * args.timeout / 60
    print(f"  Estimated time @ {args.workers} workers: ~{est_min:.0f} min worst-case\n")

    if args.dry_run:
        print("[dry-run] Exiting.")
        return

    # Backup once
    backup = V4_ALL.with_suffix(".jsonl.preenrich")
    if not backup.exists():
        print(f"Backing up V4 to {backup.name}...")
        write_jsonl(backup, all_rows)

    # Build the input file mapping: position_in_file → orig_idx_in_v4
    # The validator keys results by row_index = position in input JSONL
    pos_to_orig: dict[int, int] = {}  # position_in_input_file → orig_idx_in_v4
    validator_rows = []
    for pos, (orig_idx, row) in enumerate(needs_enrich):
        buggy_code = extract_buggy_code(row.get("context", ""))
        pos_to_orig[pos] = orig_idx
        validator_rows.append({
            "instruction": row.get("instruction", "Fix this AIE code."),
            "context": f"Buggy version:\n{buggy_code}",
            "response": "",
            "metadata": row.get("metadata") or {},
        })

    if not args.reuse_output:
        if not INPUT_FILE.exists():
            print(f"Writing {len(validator_rows)} rows to {INPUT_FILE.name}...")
            write_jsonl(INPUT_FILE, validator_rows)
        else:
            print(f"Input file already exists ({INPUT_FILE.name}), reusing.")

        # Delete old output so --resume doesn't skip everything
        if OUTPUT_FILE.exists():
            OUTPUT_FILE.unlink()

        def win_to_wsl(p: Path) -> str:
            try:
                return p.resolve().relative_to(ROOT.resolve()).as_posix()
            except ValueError:
                return str(p)

        cmd = [
            "wsl", "-d", args.wsl_distro, "--",
            "bash", "scripts/run_validate_wsl.sh",
            "--input", win_to_wsl(INPUT_FILE),
            "--out", win_to_wsl(OUTPUT_FILE),
            "--scope", "buggy",
            "--target", "AIE",
            "--workers", str(args.workers),
            "--timeout", str(args.timeout),
        ]
        print(f"Running WSL validate ({args.workers} workers)...")
        t0 = time.time()
        subprocess.run(cmd, cwd=str(ROOT), check=False)
        print(f"WSL done in {(time.time()-t0):.0f}s")
    else:
        print(f"Reusing existing output: {OUTPUT_FILE}")

    # Parse results: key is row_index = position in input file
    print("Parsing results...")
    results_by_pos: dict[int, dict] = {}
    if OUTPUT_FILE.exists():
        with OUTPUT_FILE.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    pos = obj.get("row_index")
                    if pos is not None:
                        results_by_pos[int(pos)] = obj
                except Exception:
                    pass
    print(f"  Got {len(results_by_pos)} compile results for {len(needs_enrich)} rows")

    # Patch V4 rows
    enriched = 0
    real_errors = 0
    compile_ok = 0
    no_result = 0
    for pos, orig_idx in pos_to_orig.items():
        row = all_rows[orig_idx]
        res = results_by_pos.get(pos)
        if res is None:
            no_result += 1
            continue
        error_log = format_error_log(res)
        if error_log:
            all_rows[orig_idx] = patch_error_log(row, error_log)
            real_errors += 1
        else:
            # Compiled clean (semantic bug) — mark done, keep heuristic log
            new_row = dict(row)
            meta = dict(row.get("metadata") or {})
            meta["has_real_error_log"] = True
            meta["wsl_compile_ok"] = True
            new_row["metadata"] = meta
            all_rows[orig_idx] = new_row
            compile_ok += 1
        enriched += 1

    print(f"\nResults: {enriched} patched | {real_errors} real errors | "
          f"{compile_ok} compiled clean | {no_result} no result")

    print("Saving V4...")
    write_jsonl(V4_ALL, all_rows)
    write_jsonl(V4_TRAIN, [r for r in all_rows if r.get("metadata", {}).get("split") == "train"])
    write_jsonl(V4_VAL, [r for r in all_rows if r.get("metadata", {}).get("split") == "validation"])
    print("Done.")


if __name__ == "__main__":
    main()
