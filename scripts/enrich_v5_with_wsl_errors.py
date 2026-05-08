#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V5_ALL = ROOT / "data" / "processed" / "v5" / "aie_instruction_v5_all.jsonl"
V5_TRAIN = ROOT / "data" / "processed" / "v5" / "aie_instruction_v5_train.jsonl"
V5_VAL = ROOT / "data" / "processed" / "v5" / "aie_instruction_v5_validation.jsonl"

SCRATCH_DIR = ROOT / "data" / "processed" / "v5" / ".wsl_enrich_tmp"
INPUT_FILE = SCRATCH_DIR / "enrich_input.jsonl"
OUTPUT_FILE = SCRATCH_DIR / "enrich_output.jsonl"
ERROR_LOG_SEPARATOR = "\n\n--- Error Log ---\n"

DEFAULT_WORKERS = 20
DEFAULT_WSL_DISTRO = "Ubuntu-24.04"
DEFAULT_TIMEOUT = 60

ACTIONABLE_APIS = [
    "readincr_v",
    "writeincr_v",
    "readincr",
    "writeincr",
    "window_readincr",
    "window_writeincr",
    "shuffle_up",
    "shuffle_up_fill",
    "srs",
    "mac",
    "mul",
    "to_vector",
    "input_buffer",
    "output_buffer",
    "input_stream",
    "output_stream",
]


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
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


def split_context(context: str) -> tuple[str, str]:
    if ERROR_LOG_SEPARATOR in context:
        code, elog = context.split(ERROR_LOG_SEPARATOR, 1)
        return code.strip(), elog.strip()
    if "--- Error Log ---" in context:
        code, elog = context.split("--- Error Log ---", 1)
        return code.strip(), elog.strip()
    return context.strip(), ""


def has_high_conf_actionable_api_mismatch(code: str, elog: str) -> bool:
    import re

    mentioned = [api for api in ACTIONABLE_APIS if re.search(rf"\b{re.escape(api)}\b", elog)]
    if len(mentioned) < 2:
        return False
    absent = [api for api in mentioned if not re.search(rf"\b{re.escape(api)}\b", code)]
    return len(absent) == len(mentioned)


def format_error_log(compile_result: dict) -> str | None:
    if compile_result.get("compile_ok"):
        return None

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

    if stderr:
        fallback = [l.strip() for l in stderr.splitlines() if l.strip()][:8]
        if fallback:
            return "\n".join(fallback)

    if error_class and error_class not in ("ok", ""):
        return f"Compile error: {error_class}"
    return None


def patch_error_log(row: dict, error_log: str) -> dict:
    code, _ = split_context(str(row.get("context") or ""))
    out = dict(row)
    out["context"] = code + ERROR_LOG_SEPARATOR + error_log
    meta = dict(row.get("metadata") or {})
    meta["has_real_error_log"] = True
    out["metadata"] = meta
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--wsl-distro", default=DEFAULT_WSL_DISTRO)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--limit", type=int, default=0, help="0 means all mismatch rows")
    ap.add_argument("--all-rows", action="store_true", help="Revalidate all rows instead of mismatch-only selection")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reuse-output", action="store_true")
    args = ap.parse_args()

    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    rows = load_jsonl(V5_ALL)
    needs: list[tuple[int, dict]] = []
    for i, row in enumerate(rows):
        context = str(row.get("context") or "")
        code, elog = split_context(context)
        if not code or not elog:
            continue
        if args.all_rows or has_high_conf_actionable_api_mismatch(code, elog):
            needs.append((i, row))

    if args.limit > 0:
        needs = needs[: args.limit]

    print(json.dumps({
        "total_rows": len(rows),
        "needs_wsl_rematch": len(needs),
        "selection_mode": "all_rows" if args.all_rows else "mismatch_only",
        "workers": args.workers,
        "timeout_s": args.timeout,
    }, indent=2))

    if args.dry_run or not needs:
        return

    pos_to_orig: dict[int, int] = {}
    validator_rows: list[dict] = []
    for pos, (orig_idx, row) in enumerate(needs):
        code, _ = split_context(str(row.get("context") or ""))
        pos_to_orig[pos] = orig_idx
        validator_rows.append({
            "instruction": row.get("instruction", "Fix this AIE code."),
            "context": f"Buggy version:\n{code}",
            "response": "",
            "metadata": row.get("metadata") or {},
        })

    write_jsonl(INPUT_FILE, validator_rows)

    if not args.reuse_output:
        if OUTPUT_FILE.exists():
            OUTPUT_FILE.unlink()

        cmd = [
            "wsl", "-d", args.wsl_distro, "--",
            "bash", "scripts/run_validate_wsl.sh",
            "--input", "data/processed/v5/.wsl_enrich_tmp/enrich_input.jsonl",
            "--out", "data/processed/v5/.wsl_enrich_tmp/enrich_output.jsonl",
            "--scope", "buggy",
            "--target", "AIE",
            "--workers", str(args.workers),
            "--timeout", str(args.timeout),
        ]
        t0 = time.time()
        cp = subprocess.run(cmd, cwd=str(ROOT), check=False)
        print(f"wsl_exit_code={cp.returncode} elapsed_s={int(time.time()-t0)}")

    results_by_pos: dict[int, dict] = {}
    if OUTPUT_FILE.exists():
        for line in OUTPUT_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            pos = obj.get("row_index")
            if pos is not None:
                results_by_pos[int(pos)] = obj

    patched = 0
    compile_ok = 0
    no_result = 0
    for pos, orig_idx in pos_to_orig.items():
        res = results_by_pos.get(pos)
        if res is None:
            no_result += 1
            continue
        real_log = format_error_log(res)
        if real_log:
            rows[orig_idx] = patch_error_log(rows[orig_idx], real_log)
            patched += 1
        else:
            compile_ok += 1

    write_jsonl(V5_ALL, rows)
    write_jsonl(V5_TRAIN, [r for r in rows if (r.get("metadata") or {}).get("split") == "train"])
    write_jsonl(V5_VAL, [r for r in rows if (r.get("metadata") or {}).get("split") == "validation"])

    print(json.dumps({
        "attempted": len(needs),
        "received_results": len(results_by_pos),
        "patched_with_real_errors": patched,
        "compile_ok_rows": compile_ok,
        "no_result": no_result,
    }, indent=2))


if __name__ == "__main__":
    main()
