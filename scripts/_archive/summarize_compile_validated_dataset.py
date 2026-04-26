#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from typing import Any


def first_error_line(validation: dict[str, Any]) -> str:
    reason = validation.get("error_reason") or ""
    if reason:
        return str(reason).strip()
    text = f"{validation.get('stderr_tail') or ''}\n{validation.get('stdout_tail') or ''}"
    for line in text.splitlines():
        if "error:" in line or "ERROR:" in line or "fatal error:" in line:
            return line.strip()
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return "(no compiler output)"


def coarse_cause(validation: dict[str, Any]) -> str:
    cls = validation.get("error_class") or "<missing>"
    text = f"{validation.get('error_reason') or ''}\n{validation.get('stderr_tail') or ''}\n{validation.get('stdout_tail') or ''}"
    if validation.get("compile_ok"):
        return "ok"
    if cls in {"missing_dependency", "missing_dependency_after_stub"}:
        return "missing project/local header"
    if "No such device" in text and not re.search(r"error:|fatal error:", text):
        return "toolchain/device failure"
    if "TIMEOUT" in text or validation.get("return_code") == -9:
        return "timeout"
    if "no member named" in text and "namespace 'aie'" in text:
        return "invalid/unsupported AIE API call"
    if "no matching function for call" in text:
        return "invalid API signature/types"
    if "undeclared identifier" in text or "was not declared" in text:
        return "missing symbol/context"
    if "undefined template" in text or "constraints not satisfied" in text:
        return "unsupported vector/accum type or lane count"
    if cls == "aie_api_compile_error":
        return "invalid/unsupported AIE API call"
    if cls == "compile_error":
        return "general compile error"
    return str(cls)


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize embedded compile_validation failures in a dataset JSONL.")
    ap.add_argument("path", help="Dataset JSONL with metadata.compile_validation")
    ap.add_argument("--samples", type=int, default=8, help="Samples per coarse cause")
    args = ap.parse_args()

    path = Path(args.path)
    total = 0
    ok = 0
    missing_validation = 0
    error_classes: collections.Counter[str] = collections.Counter()
    compiler_classes: collections.Counter[tuple[str, str]] = collections.Counter()
    causes: collections.Counter[str] = collections.Counter()
    by_variant: collections.Counter[tuple[str, str]] = collections.Counter()
    by_bug: collections.Counter[tuple[str, str]] = collections.Counter()
    samples: dict[str, list[tuple[int, str, str, str, str]]] = collections.defaultdict(list)

    with path.open("r", encoding="utf-8") as fp:
        for row_index, line in enumerate(fp):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                error_classes["json_decode_error"] += 1
                causes["json decode error"] += 1
                continue
            metadata = row.get("metadata") or row.get("metadata_keys") or {}
            validation = metadata.get("compile_validation") or row.get("compile_validation") or {}
            if not validation and "compile_ok" in row:
                validation = row
            if not validation:
                missing_validation += 1
                error_classes["<missing>"] += 1
                causes["missing validation metadata"] += 1
                continue
            cls = str(validation.get("error_class") or ("ok" if validation.get("compile_ok") else "compile_error"))
            compiler = str(validation.get("compiler") or "<missing>")
            cause = coarse_cause(validation)
            error_classes[cls] += 1
            compiler_classes[(compiler, cls)] += 1
            causes[cause] += 1
            by_variant[(cause, str(metadata.get("variant") or "<none>"))] += 1
            by_bug[(cause, str(metadata.get("bug_type") or "<none>"))] += 1
            if validation.get("compile_ok"):
                ok += 1
            elif len(samples[cause]) < args.samples:
                samples[cause].append((
                    row_index,
                    str(metadata.get("variant") or "<none>"),
                    str(metadata.get("bug_type") or "<none>"),
                    cls,
                    first_error_line(validation)[:260],
                ))

    fail = total - ok - missing_validation
    print(f"file={path}")
    print(f"rows={total} ok={ok} fail={fail} missing_validation={missing_validation}")
    if total:
        print(f"ok_pct={ok / total * 100:.2f} fail_pct={fail / total * 100:.2f}")

    print("\nerror_class_counts:")
    for key, count in error_classes.most_common():
        print(f"  {key}: {count}")

    print("\ncoarse_causes:")
    for key, count in causes.most_common():
        print(f"  {key}: {count}")

    print("\ncompiler_error_class_counts:")
    for (compiler, cls), count in compiler_classes.most_common(20):
        print(f"  {compiler} | {cls}: {count}")

    print("\ntop_variant_by_cause:")
    for (cause, variant), count in by_variant.most_common(20):
        print(f"  {cause} | {variant}: {count}")

    print("\ntop_bug_type_by_cause:")
    for (cause, bug_type), count in by_bug.most_common(30):
        print(f"  {cause} | {bug_type}: {count}")

    print("\nsamples:")
    for cause, rows in samples.items():
        print(f"  [{cause}]")
        for row_index, variant, bug_type, cls, err in rows:
            print(f"    row={row_index} variant={variant} bug_type={bug_type} class={cls}")
            print(f"      {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())