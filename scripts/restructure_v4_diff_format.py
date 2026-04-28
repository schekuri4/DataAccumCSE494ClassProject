#!/usr/bin/env python3
"""Restructure V4 dataset to diff-based format.

New format:
  instruction: Standard prompt asking to identify and fix the AIE hardware error
  context:     Buggy AIE C++ code + Error Log section
  response:    Unified diff (- / + lines only, no full file)

Passthrough rows (no bug, correct code in both sides) are dropped — an empty
diff gives the model nothing to learn.

Steps performed:
  1. Parse buggy code from all context formats (Scenario, Buggy version:, fenced, bare)
  2. Parse correct code from response
  3. Strip hint comments (// case: / // intent: / // BUG: etc.)
  4. Compute unified diff; skip if empty or near-whole-file replacement
  5. Build Error Log from:
     a. metadata['symptom'] if present
     b. Bug-type heuristic mapping otherwise
  6. Write new V4 files; keep backup of previous

WSL error log enrichment:
  After running this script, optionally enrich with real xchesscc error logs by
  running: python scripts/enrich_v4_with_wsl_errors.py  (run separately, slow)
"""
from __future__ import annotations

import difflib
import json
import re
import random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4_ALL = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_all.jsonl"
V4_TRAIN = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_train.jsonl"
V4_VAL = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_validation.jsonl"

# ---------------------------------------------------------------------------
# Instruction phrasings — all reference error log in context
# ---------------------------------------------------------------------------
INSTRUCTIONS = [
    "An AIE kernel or ADF graph has a hardware-specific error. The error log is in the context below. Return a unified diff that fixes the root cause — only changed lines, no full file.",
    "This AIE source triggers the error shown below. Identify the root cause and return a unified diff with the minimal fix.",
    "A Versal AIE build is failing with the error below. Return a unified diff that resolves it.",
    "The AIE code below produces the reported error. Return a unified diff showing only the lines that must change.",
    "Diagnose the AIE bug from the error log and return a unified diff with the surgical fix.",
    "The following AIE source has an architectural defect logged below. Return a unified diff — removals with -, additions with +.",
    "An xchesscc / aiesimulator error is shown below for this AIE source. Provide the unified diff that eliminates the root cause.",
    "This AIE kernel or graph has a bug manifesting as the error below. Return only the unified diff needed to fix it.",
]

# ---------------------------------------------------------------------------
# Per-bug-type heuristic error messages (used when no real log available)
# ---------------------------------------------------------------------------
_BUG_ERROR_HINTS: dict[str, str] = {
    # Graph-level
    "reversed_connect_direction":
        "Error: [AIE 77-1] Connection direction mismatch: source port connected to source, or sink to sink.",
    "missing_adf_source_assignment":
        "Error: [AIE 77-4] Kernel source file not set — adf::source() must be called before simulation.",
    "missing_adf_runtime_line_entirely":
        "Error: [AIE 77-9] Runtime ratio not set for kernel — adf::runtime<adf::ratio>() required.",
    "runtime_ratio_zero":
        "Warning: [AIE 77-9] Runtime ratio is 0.0 — kernel will never be scheduled.",
    "mismatched_plio_width":
        "Error: [AIE 77-203] PLIO width mismatch: graph declares plio_32_bits but data width is 64 bits.",
    "graph_buffer_dimension_mismatch":
        "Error: [AIE 77-305] Window size mismatch between producer and consumer — kernel expects different byte count.",
    "stream_deadlock_unbalanced_tokens":
        "Error: [AIE_SIM 75-603] Deadlock detected — stream consumer stalled waiting for tokens not produced by any kernel.",
    # Kernel arithmetic
    "acc48_instead_of_acc80_for_int32xint32":
        "Runtime: accumulator overflow — int32 x int32 product exceeds acc48 capacity (48-bit); use acc80.",
    "accumulator_initialized_with_aie_mul_garbage_instead_of_aie_zeros":
        "Runtime: output contains garbage values — accumulator not zero-initialized before MAC loop.",
    "aie_add_used_instead_of_aie_mul":
        "Runtime: filter output is a running sum, not a convolution — aie::add used instead of aie::mul.",
    "subtraction_instead_of_addition":
        "Runtime: output inverted — subtraction operator used where addition is required.",
    "missing_output_write":
        "Error: [AIE_SIM 75-100] Output stream stalled — no writeincr call in kernel; consumer blocked.",
    "missing_iterator_increment":
        "Runtime: infinite loop / stall — iterator not incremented, kernel never advances past first element.",
    "wrong_loop_count_16_instead_of_32":
        "Runtime: output truncated — loop processes 16 samples but buffer contains 32.",
    "off_by_one_oob":
        "Error: [AIE_SIM 75-201] Out-of-bounds memory access at final loop iteration.",
    "readincr_from_output_stream_instead_of_input_stream":
        "Error: [xchesscc] cannot read from output stream — readincr called on output_stream<T>*.",
    "broadcast_width_does_not_match_vector_width_broadcast_int16_4_with_vector_int16_8":
        "Error: [xchesscc] vector width mismatch — aie::broadcast<int16,4> result assigned to aie::vector<int16,8>.",
    "to_vector_output_type_does_not_match_buffer_type_int32_vs_int16":
        "Error: [xchesscc] type mismatch — acc.to_vector<int32>() assigned to int16 output buffer.",
    "wrong_vector_lane_width":
        "Error: [xchesscc] unsupported vector lane count — aie::vector<T,N> with N not a power of 2.",
    "missing_chess_prepare_for_pipelining":
        "Warning: [xchesscc] loop not pipelined — chess_prepare_for_pipelining pragma missing; throughput degraded.",
    "stream_consumption_mismatch":
        "Error: [AIE_SIM 75-603] Stream token count mismatch — producer writes N tokens per invocation but consumer reads M.",
}

_GENERIC_ERRORS = [
    "Error: [xchesscc] compilation failed — semantic error in AIE kernel or graph.",
    "Error: [AIE_SIM 75-000] Simulation failed — runtime assertion triggered by kernel.",
    "Runtime: incorrect output values observed — kernel produces wrong results under simulation.",
    "Error: [AIE 77-0] Graph validation failed — structural error in ADF graph definition.",
]


def _error_log_for_row(row: dict) -> str:
    """Return the best error log string we can produce for this row."""
    # Real compile error stored by previous WSL pass
    wsl_err = (row.get("metadata") or {}).get("wsl_error_log", "")
    if wsl_err:
        return wsl_err.strip()
    # Symptom from metadata
    symptom = (row.get("metadata") or {}).get("symptom", "")
    bug_type = (row.get("metadata") or {}).get("bug_type", "")
    # Known heuristic
    for key, msg in _BUG_ERROR_HINTS.items():
        if key in bug_type:
            return msg
    # Fall back to symptom
    if symptom and symptom not in ("stalls, corrupted output, or throughput collapse",):
        return f"Runtime symptom: {symptom}"
    # Generic
    return random.choice(_GENERIC_ERRORS)


# ---------------------------------------------------------------------------
# Code extraction helpers
# ---------------------------------------------------------------------------

_HINT_COMMENT_RE = re.compile(
    r"^\s*//\s*(case:|intent:|BUG:|BUG_INJECTED:|ORIGINAL:).*$",
    re.MULTILINE,
)


def _strip_hint_comments(code: str) -> str:
    """Remove // case: / // intent: / // BUG*: lines that leak bug identity."""
    return _HINT_COMMENT_RE.sub("", code).strip()


def _strip_fence(text: str) -> str:
    text = text.strip()
    for lang in ("```cpp", "```c++", "```c", "```"):
        if text.startswith(lang):
            text = text[len(lang):].strip()
            break
    if text.endswith("```"):
        text = text[:-3].strip()
    return text.strip()


def _extract_section(text: str, header: str) -> str | None:
    """Extract text between a labeled section header and the next labeled section."""
    pattern = re.compile(
        rf"^{re.escape(header)}\s*\n(.*?)(?=\n(?:Buggy version:|Correct version:|Scenario bug pattern:|Source:)\s*\n|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def extract_buggy_correct(row: dict) -> tuple[str, str] | None:
    """Return (buggy_code, correct_code) stripped of fences and hint comments.

    Returns None if extraction fails or the pair is a no-bug passthrough.
    """
    ctx = row.get("context", "").strip()
    resp = row.get("response", "").strip()

    # --- Determine buggy code from context ---
    buggy: str | None = None

    # Format 1: "Scenario bug pattern: ...\nSource: ...\n\nBuggy version:\n...\n\nCorrect version:\n..."
    if ctx.startswith("Scenario bug pattern:"):
        buggy = _extract_section(ctx, "Buggy version:")
        if buggy is None:
            # Try simpler split
            if "Buggy version:" in ctx:
                parts = ctx.split("Buggy version:", 1)[1]
                if "Correct version:" in parts:
                    buggy = parts.split("Correct version:")[0].strip()
                else:
                    buggy = parts.strip()

    # Format 2: "Buggy version:\n..."
    elif ctx.startswith("Buggy version:"):
        rest = ctx[len("Buggy version:"):].strip()
        if "Correct version:" in rest:
            buggy = rest.split("Correct version:")[0].strip()
        else:
            buggy = rest

    # Format 3: fenced or bare code
    else:
        buggy = _strip_fence(ctx)

    if not buggy:
        return None

    # --- Determine correct code from response ---
    correct = _strip_fence(resp)
    if not correct:
        # Some responses are bare
        correct = resp.strip()
    if not correct:
        return None

    # Strip hint comments from both
    buggy = _strip_hint_comments(buggy)
    correct = _strip_hint_comments(correct)

    # Sanity: both must look like code
    for code in (buggy, correct):
        if not re.search(r"#include|void\s+\w|class\s+\w", code):
            return None

    return buggy, correct


# ---------------------------------------------------------------------------
# Diff generation
# ---------------------------------------------------------------------------

def make_diff(buggy: str, correct: str, filename: str = "aie_source") -> str | None:
    """Generate unified diff. Returns None if diff is empty or replaces everything."""
    buggy_lines = buggy.splitlines()
    correct_lines = correct.splitlines()

    diff = list(difflib.unified_diff(
        buggy_lines, correct_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
        n=3,
    ))

    if not diff:
        return None  # identical — no bug

    # Reject diffs that replace >70% of lines (whole-file replacement = not surgical)
    changed = sum(1 for l in diff if l.startswith(("+", "-")) and not l.startswith(("---", "+++")))
    total = max(len(buggy_lines), len(correct_lines), 1)
    if changed / total > 0.70:
        return None

    return "\n".join(diff)


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------

def convert_row(row: dict, rng: random.Random) -> dict | None:
    """Convert a fix-task row to the new diff format. Returns None to skip."""
    pair = extract_buggy_correct(row)
    if pair is None:
        return None
    buggy, correct = pair

    diff = make_diff(buggy, correct)
    if diff is None:
        return None  # no-bug passthrough or whole-file replacement

    error_log = _error_log_for_row(row)
    instruction = rng.choice(INSTRUCTIONS)

    new_context = f"{buggy}\n\n--- Error Log ---\n{error_log}"

    new_row = dict(row)
    new_row["instruction"] = instruction
    new_row["context"] = new_context
    new_row["response"] = diff
    meta = dict(row.get("metadata") or {})
    meta["response_format"] = "unified_diff"
    meta["has_real_error_log"] = bool(meta.get("wsl_error_log"))
    new_row["metadata"] = meta
    return new_row


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    rng = random.Random(42)

    print("Loading V4 all rows...")
    all_rows = load_jsonl(V4_ALL)
    print(f"  Loaded {len(all_rows)} rows")

    # Backup
    backup = V4_ALL.with_suffix(".jsonl.prediffformat")
    if not backup.exists():
        print(f"  Backing up to {backup.name}")
        write_jsonl(backup, all_rows)
    else:
        print(f"  Backup already exists: {backup.name}")

    # Separate passthrough rows from fix rows
    passthrough = [r for r in all_rows if r.get("metadata", {}).get("instruction_converted")]
    fix_rows = [r for r in all_rows if not r.get("metadata", {}).get("instruction_converted")]
    print(f"\n  Fix rows: {len(fix_rows)}")
    print(f"  Passthrough rows (dropping): {len(passthrough)}")

    # Convert fix rows
    converted = []
    skipped_no_extract = 0
    skipped_no_diff = 0

    for r in fix_rows:
        result = convert_row(r, rng)
        if result is None:
            pair = extract_buggy_correct(r)
            if pair is None:
                skipped_no_extract += 1
            else:
                skipped_no_diff += 1
        else:
            converted.append(result)

    print(f"\nConversion results:")
    print(f"  Converted to diff format: {len(converted)}")
    print(f"  Skipped — extraction failed: {skipped_no_extract}")
    print(f"  Skipped — diff empty/whole-file: {skipped_no_diff}")
    print(f"  Dropped passthrough: {len(passthrough)}")

    # Stats on error log quality
    real_logs = sum(1 for r in converted if r["metadata"].get("has_real_error_log"))
    heuristic = len(converted) - real_logs
    print(f"\n  Error log source: {real_logs} real WSL logs, {heuristic} heuristic/symptom")

    # Sample output
    print("\n=== Sample converted row ===")
    sample = converted[0]
    print("INSTRUCTION:", sample["instruction"])
    print("CONTEXT (first 600 chars):\n", sample["context"][:600])
    print("RESPONSE:\n", sample["response"][:400])

    # Write
    print(f"\nWriting {V4_ALL.name} ({len(converted)} rows)...")
    write_jsonl(V4_ALL, converted)

    train_rows = [r for r in converted if r.get("metadata", {}).get("split") == "train"]
    val_rows = [r for r in converted if r.get("metadata", {}).get("split") == "validation"]
    unassigned = len(converted) - len(train_rows) - len(val_rows)
    if unassigned:
        print(f"  WARNING: {unassigned} rows have no split assignment")

    print(f"Writing train ({len(train_rows)}) and val ({len(val_rows)})...")
    write_jsonl(V4_TRAIN, train_rows)
    write_jsonl(V4_VAL, val_rows)

    print("\nDone.")
    print(f"\nNext steps:")
    print(f"  1. Run python scripts/enrich_v4_with_wsl_errors.py  (optional, ~18h, adds real error logs)")
    print(f"  2. After Bedrock generation finishes: python scripts/add_bedrock_mutations_to_v4.py")
    print(f"  3. Train: python scripts/train_unsloth_windows.py")


if __name__ == "__main__":
    main()
