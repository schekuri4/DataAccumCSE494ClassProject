#!/usr/bin/env python3
"""
validate_aie_logical_local.py
=============================

Open-source local checker for AIE dataset snippets.

Purpose
-------
Provide a practical "is this snippet logically plausible?" signal without relying
on Vitis/aiecompiler. The checker combines:

1) Structural checks (balanced delimiters, non-trivial code)
2) Optional syntax-only compile with clang++/g++ (open-source compilers)
3) Automatic retry with lightweight shim stubs for unresolved AIE/project symbols
4) Diff sanity between buggy and correct snippets (when both are available)

Output is JSONL with per-sample verdicts and confidence, intended for filtering
or triaging rows before expensive Vitis validation.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


# Reuse extraction logic from compile validator.
from validate_aie_compile import extract_buggy_code, extract_correct_code


UNDECLARED_RE = re.compile(r"use of undeclared identifier '([^']+)'")
WAS_NOT_DECLARED_RE = re.compile(r"'([^']+)' was not declared in this scope")
HAS_NOT_BEEN_DECLARED_RE = re.compile(r"'([^']+)' has not been declared")
NO_MEMBER_AIE_RE = re.compile(r"no member named '([^']+)' in namespace 'aie'")
NOT_MEMBER_ADF_RE = re.compile(r"'([^']+)' is not a member of 'adf'")
MISSING_INCLUDE_RE = re.compile(r"fatal error: ([^\s:]+): No such file or directory")


BASE_SHIM = """
#include <stdint.h>
#include <stddef.h>

using int4 = int8_t;
using uint4 = uint8_t;
using int8 = int8_t;
using uint8 = uint8_t;
using int16 = int16_t;
using uint16 = uint16_t;
using int32 = int32_t;
using uint32 = uint32_t;
using int64 = int64_t;
using uint64 = uint64_t;
using float16 = uint16_t;
using bfloat16 = uint16_t;
using float32 = float;

struct cint16 { int16_t real; int16_t imag; };
struct cint32 { int32_t real; int32_t imag; };
struct cfloat { float real; float imag; };

namespace aie {
    template <typename T, int N = 1> struct vector { T data[N]; };
    template <typename... Args> int begin_restrict_vector(Args...) { return 0; }
}

namespace adf {
    struct graph {};
    struct input_plio { template <typename... Args> static input_plio create(Args...) { return input_plio(); } int out[1]; };
    struct output_plio { template <typename... Args> static output_plio create(Args...) { return output_plio(); } int in[1]; };
    enum plio_width { plio_32_bits = 32, plio_64_bits = 64 };
    struct kernel {
        template <typename T> static kernel create(T) { return kernel(); }
    };
    template <typename T, int N = 1> struct port { T data[N]; };
    template <typename... Args> void connect(Args...) {}
    template <typename... Args> void source(Args...) {}
    template <typename... Args> void runtime(Args...) {}
}

template <typename T> struct input_stream {};
template <typename T> struct output_stream {};
template <typename T, typename Ext = int> struct input_buffer {};
template <typename T, typename Ext = int> struct output_buffer {};
template <int N> struct extents {};

using input_stream_cint16 = input_stream<cint16>;
using input_stream_cacc48 = input_stream<int64_t>;
using output_stream_cacc48 = output_stream<int64_t>;
using output_stream_cint16 = output_stream<cint16>;
""".strip()


@dataclass
class LogicalResult:
    input_path: str
    row_index: int
    scope: str
    bug_type: str
    logical_ok: bool
    confidence: float
    verdict: str
    compiler: str
    compile_attempts: int
    include_missing_count: int
    undeclared_count: int
    changed_vs_buggy: bool
    changed_lines: int
    likely_fixed_issue: bool
    fix_evidence: str
    elapsed_sec: float
    reason: str


def find_cpp_compiler() -> tuple[str | None, list[str]]:
    candidates = []
    cxx = os.environ.get("CXX")
    if cxx:
        candidates.append(cxx)
    candidates.extend([
        "clang++", "g++", "c++", "clang",
        "clang++.exe", "g++.exe",
    ])

    seen: set[str] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        path = shutil.which(c)
        if path:
            return path, [path, "-std=c++17", "-fsyntax-only", "-x", "c++"]
    return None, []


def strip_includes(code: str) -> str:
    out = []
    for line in code.splitlines():
        if re.match(r"\s*#\s*include\s+[<\"].*[>\"]", line):
            # Missing project headers should not block local logical checks.
            continue
        out.append(line)
    return "\n".join(out)


def build_stub_block(
    type_stubs: Iterable[str],
    fn_stubs: Iterable[str],
    aie_member_stubs: Iterable[str],
    adf_member_stubs: Iterable[str],
) -> str:
    lines = [BASE_SHIM, ""]
    for t in sorted(set(type_stubs)):
        if re.match(r"^[A-Za-z_]\w*$", t):
            lines.append(f"using {t} = int;")
    for fn in sorted(set(fn_stubs)):
        if re.match(r"^[A-Za-z_]\w*$", fn):
            lines.append(f"template <typename... Args> int {fn}(Args...);")
    if aie_member_stubs:
        lines.append("namespace aie {")
        for m in sorted(set(aie_member_stubs)):
            if re.match(r"^[A-Za-z_]\w*$", m):
                lines.append(f"template <typename... Args> int {m}(Args...);")
        lines.append("}")
    if adf_member_stubs:
        lines.append("namespace adf {")
        for m in sorted(set(adf_member_stubs)):
            if re.match(r"^[A-Za-z_]\w*$", m):
                lines.append(f"template <typename... Args> int {m}(Args...);")
        lines.append("}")
    return "\n".join(lines)


def add_stubs_from_errors(
    stderr: str,
    type_stubs: set[str],
    fn_stubs: set[str],
    aie_member_stubs: set[str],
    adf_member_stubs: set[str],
) -> int:
    before = len(type_stubs) + len(fn_stubs) + len(aie_member_stubs) + len(adf_member_stubs)

    names = []
    names.extend(UNDECLARED_RE.findall(stderr))
    names.extend(WAS_NOT_DECLARED_RE.findall(stderr))
    names.extend(HAS_NOT_BEEN_DECLARED_RE.findall(stderr))

    for name in names:
        if not re.match(r"^[A-Za-z_]\w*$", name):
            continue
        # Heuristic: scalar-ish names likely types, otherwise function/identifier.
        if re.search(r"^(u?int\d+|cint\d+|float\d*|bfloat\d*)$", name):
            type_stubs.add(name)
        elif name.endswith("_stream") or name.endswith("_buffer"):
            type_stubs.add(name)
        elif name in {"input_stream", "output_stream", "input_buffer", "output_buffer", "extents"}:
            type_stubs.add(name)
        elif name[:1].isupper():
            type_stubs.add(name)
        else:
            fn_stubs.add(name)

    for member in NO_MEMBER_AIE_RE.findall(stderr):
        if re.match(r"^[A-Za-z_]\w*$", member):
            aie_member_stubs.add(member)

    for member in NOT_MEMBER_ADF_RE.findall(stderr):
        if re.match(r"^[A-Za-z_]\w*$", member):
            adf_member_stubs.add(member)

    after = len(type_stubs) + len(fn_stubs) + len(aie_member_stubs) + len(adf_member_stubs)
    return after - before


def has_balanced_delimiters(code: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = set(pairs.values())
    closes = set(pairs.keys())
    stack: list[str] = []
    in_single = False
    in_double = False
    escape = False

    for ch in code:
        if in_single:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "'":
                in_single = False
            continue
        if in_double:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_double = False
            continue

        if ch == "'":
            in_single = True
            continue
        if ch == '"':
            in_double = True
            continue

        if ch in opens:
            stack.append(ch)
        elif ch in closes:
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()

    return not stack and not in_single and not in_double


def normalize_for_compare(code: str) -> str:
    code = re.sub(r"//.*", "", code)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"\s+", "", code)
    return code.strip()


def changed_line_count(a: str, b: str) -> int:
    if not a or not b:
        return 0
    da = a.splitlines()
    db = b.splitlines()
    changes = 0
    for line in difflib.ndiff(da, db):
        if line.startswith("- ") or line.startswith("+ "):
            changes += 1
    return changes


def is_aie_dsl_only_failure(stderr_tail: str) -> bool:
    """Best-effort classifier for errors caused by missing AIE DSL semantics.

    These indicate the snippet may still be logically plausible for AIE even when
    a generic C++ compiler cannot fully type-check it.
    """
    if not stderr_tail:
        return False
    patterns = [
        r"chess_prepare_for_pipelining",
        r"is not a member of 'aie'",
        r"is not a member of 'adf'",
        r"'input_stream' was not declared",
        r"'output_stream' was not declared",
        r"runtime<ratio>",
        r"readincr_v<",
        r"writeincr_v<",
        r"expected ';' before 'for'",
        r"statement cannot resolve address of overloaded function",
    ]
    return any(re.search(p, stderr_tail) for p in patterns)


def quality_score(logical_ok: bool, verdict: str, changed: bool) -> float:
    """Convert verdict into a coarse quality score for buggy-vs-correct deltas."""
    score = 0.0
    if logical_ok:
        score += 1.0
    if verdict == "logical_likely":
        score += 1.0
    elif verdict == "dsl_unresolved_but_plausible":
        score += 0.5
    elif verdict == "structure_only_no_compiler":
        score += 0.25
    if changed:
        score += 0.25
    return score


def bugtype_fix_cues(bug_type: str, buggy: str, correct: str) -> list[str]:
    """Heuristic bug-type-specific cues that the presented issue was addressed."""
    cues: list[str] = []
    bt = (bug_type or "").lower()
    b = buggy or ""
    c = correct or ""

    # Missing iterator increment
    if "missing_iterator_increment" in bt:
        if ("++" in c or "+=" in c) and not ("++" in b or "+=" in b):
            cues.append("iterator_increment_added")

    # Arithmetic operator swap bugs
    if "subtraction_instead_of_addition" in bt:
        if "+" in c and "-" in b:
            cues.append("operator_sub_to_add")
    if "addition_instead_of_subtraction" in bt:
        if "-" in c and "+" in b:
            cues.append("operator_add_to_sub")

    # Loop count / bound issues
    if "loop_count" in bt or "loop_bound" in bt:
        num_b = re.findall(r"\b\d+\b", b)
        num_c = re.findall(r"\b\d+\b", c)
        if num_b != num_c:
            cues.append("loop_numeric_bound_changed")

    # Missing runtime line in graph setup
    if "missing_adf_runtime_line" in bt:
        if "runtime<ratio>" in c and "runtime<ratio>" not in b:
            cues.append("runtime_ratio_added")

    # Stream deadlock / unconsumed stream classes: require visible stream-side edits.
    if "deadlock" in bt or "unconsumed_stream" in bt or "unbalanced_tokens" in bt:
        stream_terms = ["readincr", "writeincr", "input_stream", "output_stream", "connect<"]
        b_count = sum(b.count(t) for t in stream_terms)
        c_count = sum(c.count(t) for t in stream_terms)
        if b_count != c_count:
            cues.append("stream_io_balance_changed")

    return cues


def run_syntax_with_retries(compiler_cmd: list[str], code: str, max_retries: int) -> tuple[bool, int, int, int, str]:
    if not compiler_cmd:
        return False, 0, 0, 0, "no compiler found"

    cleaned = strip_includes(code)
    type_stubs: set[str] = set()
    fn_stubs: set[str] = set()
    aie_member_stubs: set[str] = set()
    adf_member_stubs: set[str] = set()

    missing_include_count = 0
    undeclared_count = 0
    attempts = 0
    last_err = ""

    with tempfile.TemporaryDirectory(prefix="aie_logic_") as td:
        src = Path(td) / "snippet.cpp"

        for _ in range(max_retries):
            attempts += 1
            stub_block = build_stub_block(type_stubs, fn_stubs, aie_member_stubs, adf_member_stubs)
            src.write_text(stub_block + "\n\n" + cleaned + "\n", encoding="utf-8")

            cp = subprocess.run(
                compiler_cmd + [str(src)],
                capture_output=True,
                text=True,
                check=False,
            )
            if cp.returncode == 0:
                return True, attempts, missing_include_count, undeclared_count, "ok"

            stderr = cp.stderr or ""
            last_err = stderr[-3000:]

            missing = MISSING_INCLUDE_RE.findall(stderr)
            missing_include_count += len(missing)

            undeclared = UNDECLARED_RE.findall(stderr)
            undeclared_count += len(undeclared)

            added = add_stubs_from_errors(
                stderr,
                type_stubs,
                fn_stubs,
                aie_member_stubs,
                adf_member_stubs,
            )
            if added <= 0:
                break

    return False, attempts, missing_include_count, undeclared_count, last_err or "syntax check failed"


def evaluate_one(
    input_path: str,
    row_index: int,
    scope: str,
    row: dict,
    compiler_name: str,
    compiler_cmd: list[str],
    retries: int,
) -> LogicalResult:
    t0 = time.time()

    md = row.get("metadata") or {}
    bug_type = str(md.get("bug_type") or md.get("category") or "unknown")

    if scope == "correct":
        code = extract_correct_code(row)
    elif scope == "buggy":
        code = extract_buggy_code(row)
    else:
        raise ValueError("invalid scope")

    buggy = extract_buggy_code(row)
    changed = False
    changed_lines = 0

    if code and buggy and scope == "correct":
        changed = normalize_for_compare(code) != normalize_for_compare(buggy)
        changed_lines = changed_line_count(buggy, code)

    if not code or len(code.strip()) < 20:
        fix_cues = bugtype_fix_cues(bug_type, buggy or "", code or "") if scope == "correct" else []
        return LogicalResult(
            input_path=input_path,
            row_index=row_index,
            scope=scope,
            bug_type=bug_type,
            logical_ok=False,
            confidence=0.0,
            verdict="empty_or_tiny",
            compiler=compiler_name,
            compile_attempts=0,
            include_missing_count=0,
            undeclared_count=0,
            changed_vs_buggy=changed,
            changed_lines=changed_lines,
            likely_fixed_issue=False,
            fix_evidence=",".join(fix_cues) if fix_cues else "none",
            elapsed_sec=round(time.time() - t0, 3),
            reason="no usable code extracted",
        )

    if not has_balanced_delimiters(code):
        fix_cues = bugtype_fix_cues(bug_type, buggy or "", code or "") if scope == "correct" else []
        return LogicalResult(
            input_path=input_path,
            row_index=row_index,
            scope=scope,
            bug_type=bug_type,
            logical_ok=False,
            confidence=0.05,
            verdict="unbalanced_delimiters",
            compiler=compiler_name,
            compile_attempts=0,
            include_missing_count=0,
            undeclared_count=0,
            changed_vs_buggy=changed,
            changed_lines=changed_lines,
            likely_fixed_issue=False,
            fix_evidence=",".join(fix_cues) if fix_cues else "none",
            elapsed_sec=round(time.time() - t0, 3),
            reason="basic structural check failed",
        )

    ok, attempts, miss_inc, undecl, reason = run_syntax_with_retries(compiler_cmd, code, retries)

    if ok:
        if scope == "correct" and buggy and not changed:
            verdict = "compiles_but_unchanged_from_buggy"
            logical_ok = False
            confidence = 0.35
            reason = "passes syntax but appears unchanged from buggy code"
        else:
            verdict = "logical_likely"
            logical_ok = True
            confidence = 0.8 if changed else 0.7
    else:
        # If we do not have a compiler, keep a medium-confidence structural verdict.
        if not compiler_cmd:
            verdict = "structure_only_no_compiler"
            logical_ok = True
            confidence = 0.45
            reason = "no local open-source compiler found; structural checks only"
        elif scope == "correct" and changed and is_aie_dsl_only_failure(reason):
            verdict = "dsl_unresolved_but_plausible"
            logical_ok = True
            confidence = 0.55
            reason = "generic compiler failed on AIE DSL constructs; snippet changed and is structurally plausible"
        else:
            verdict = "syntax_unresolved"
            logical_ok = False
            confidence = 0.2

    likely_fixed_issue = False
    fix_evidence_items: list[str] = []
    if scope == "correct" and buggy:
        # Measure whether "correct" improved over "buggy" under the same local checks.
        buggy_balanced = has_balanced_delimiters(buggy)
        buggy_ok, _, _, _, buggy_reason = run_syntax_with_retries(compiler_cmd, buggy, retries)
        buggy_verdict = "logical_likely" if buggy_ok else (
            "dsl_unresolved_but_plausible" if is_aie_dsl_only_failure(buggy_reason) else "syntax_unresolved"
        )

        correct_score = quality_score(logical_ok, verdict, changed)
        buggy_score = quality_score(
            buggy_ok or (buggy_balanced and is_aie_dsl_only_failure(buggy_reason)),
            buggy_verdict,
            False,
        )
        improvement = round(correct_score - buggy_score, 3)
        fix_evidence_items.append(f"delta_score={improvement}")
        if improvement >= 0.5:
            fix_evidence_items.append("quality_improved")

        fix_cues = bugtype_fix_cues(bug_type, buggy, code)
        if fix_cues:
            fix_evidence_items.extend(fix_cues)

        # Require a real edit plus either quality improvement or bug-type cue.
        likely_fixed_issue = bool(changed and (improvement >= 0.5 or fix_cues))
    else:
        likely_fixed_issue = bool(logical_ok)
        fix_evidence_items.append("single_scope_evaluation")

    return LogicalResult(
        input_path=input_path,
        row_index=row_index,
        scope=scope,
        bug_type=bug_type,
        logical_ok=logical_ok,
        confidence=confidence,
        verdict=verdict,
        compiler=compiler_name,
        compile_attempts=attempts,
        include_missing_count=miss_inc,
        undeclared_count=undecl,
        changed_vs_buggy=changed,
        changed_lines=changed_lines,
        likely_fixed_issue=likely_fixed_issue,
        fix_evidence=",".join(fix_evidence_items) if fix_evidence_items else "none",
        elapsed_sec=round(time.time() - t0, 3),
        reason=reason,
    )


def iter_rows(path: Path):
    with path.open("r", encoding="utf-8") as fp:
        for i, line in enumerate(fp):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", nargs="+", required=True, help="JSONL file(s)")
    ap.add_argument("--out", required=True, help="Output JSONL path")
    ap.add_argument("--scope", choices=["correct", "buggy", "both"], default="correct")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-retries", type=int, default=4, help="Max syntax retry attempts per sample")
    args = ap.parse_args(argv)

    compiler_name, compiler_cmd = find_cpp_compiler()
    compiler_display = compiler_name or "<none>"
    print(f"[logical] compiler={compiler_display}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scopes = ["buggy", "correct"] if args.scope == "both" else [args.scope]

    total = 0
    ok_count = 0
    bad_count = 0
    verdict_counts: dict[str, int] = {}

    with out_path.open("w", encoding="utf-8") as ofp:
        for ip in [Path(p) for p in args.input]:
            if not ip.exists():
                print(f"[logical] missing input: {ip}")
                continue
            for row_index, row in iter_rows(ip):
                for scope in scopes:
                    if args.limit is not None and total >= args.limit:
                        break

                    res = evaluate_one(
                        input_path=str(ip),
                        row_index=row_index,
                        scope=scope,
                        row=row,
                        compiler_name=compiler_display,
                        compiler_cmd=compiler_cmd,
                        retries=args.max_retries,
                    )
                    total += 1
                    if res.logical_ok:
                        ok_count += 1
                    else:
                        bad_count += 1
                    verdict_counts[res.verdict] = verdict_counts.get(res.verdict, 0) + 1

                    ofp.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
                    ofp.flush()

                    if total % 25 == 0:
                        print(f"[logical] {total} done | ok={ok_count} bad={bad_count}")

                if args.limit is not None and total >= args.limit:
                    break

    print(f"[logical] total={total} ok={ok_count} bad={bad_count} out={out_path}")
    for v, c in sorted(verdict_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"[logical] verdict {v}: {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
