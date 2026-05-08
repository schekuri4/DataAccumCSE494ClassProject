#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V5_DIR = ROOT / "data" / "processed" / "v5"

INTRINSIC_RE = re.compile(r"\baie::[A-Za-z_][A-Za-z0-9_]*\b")
VECTOR_NUM_IN_CODE_RE = re.compile(r"vector[^\n]{0,140}?<(?:[^,>]+,\s*)?(\d+)>")
VECTOR_NUM_IN_ERR_RE = re.compile(r"native_vector_type<[^,>]+,\s*(\d+)>")
HAS_BACKEND_VECTOR_ERR_RE = re.compile(r"native_vector_type<|vector_storage<")
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
    with path.open("r", encoding="utf-8") as fh:
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


def extract_intrinsics(context: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in INTRINSIC_RE.findall(context):
        if match not in seen:
            seen.add(match)
            ordered.append(match)
    return ordered


def split_context(context: str) -> tuple[str, str]:
    marker = "\n\n--- Error Log ---\n"
    if marker in context:
        code, elog = context.split(marker, 1)
        return code, elog

    if "--- Error Log ---" in context:
        code, elog = context.split("--- Error Log ---", 1)
        return code.rstrip(), elog.strip()

    return context.rstrip(), ""


def has_vector_size_mismatch(code: str, elog: str) -> bool:
    nums_code = set(VECTOR_NUM_IN_CODE_RE.findall(code))
    nums_err = set(VECTOR_NUM_IN_ERR_RE.findall(elog))
    return bool(nums_code and nums_err and nums_code.isdisjoint(nums_err))


def is_scalarish_code(code: str) -> bool:
    # Treat code as scalar-oriented if it does not explicitly use AIE vector APIs.
    vector_markers = (
        "aie::vector",
        "aie::accum",
        "load_v",
        "store_v",
        "to_vector",
        "shuffle_",
    )
    return not any(tok in code for tok in vector_markers)


def sanitize_error_log(elog: str) -> str:
    lines = [ln.rstrip() for ln in elog.splitlines() if ln.strip()]
    kept = [
        ln
        for ln in lines
        if "native_vector_type<" not in ln and "vector_storage<" not in ln
    ]
    if not kept:
        kept = ["Compile error: AIE vector type instantiation failed."]
    return "\n".join(kept)


def has_high_conf_actionable_api_mismatch(code: str, elog: str) -> bool:
    mentioned = [api for api in ACTIONABLE_APIS if re.search(rf"\b{re.escape(api)}\b", elog)]
    if len(mentioned) < 2:
        return False
    absent = [api for api in mentioned if not re.search(rf"\b{re.escape(api)}\b", code)]
    return len(absent) == len(mentioned)


def generic_compile_error_log(elog: str) -> str:
    lines = [ln.rstrip() for ln in elog.splitlines() if ln.strip()]
    keep = [ln for ln in lines if re.search(r"(?i)\berror\b", ln)]
    if keep:
        first = keep[0]
        first = re.sub(r"^.*?:\d+:\d+:\s*error:\s*", "", first)
        first = first.strip()
        if first:
            return f"Compile error: {first}"
    return "Compile error: AIE compilation failed for this source."


def rebuild_context(code: str, elog: str) -> str:
    return f"{code.rstrip()}\n\n--- Error Log ---\n{elog.strip()}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v5-dir", default=str(V5_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    v5_dir = Path(args.v5_dir)
    all_path = v5_dir / "aie_instruction_v5_all.jsonl"
    train_path = v5_dir / "aie_instruction_v5_train.jsonl"
    validation_path = v5_dir / "aie_instruction_v5_validation.jsonl"

    rows = load_jsonl(all_path)

    fixed_literal_backslash_n = 0
    fixed_vector_mismatch = 0
    fixed_scalar_backend_vector_err = 0
    fixed_actionable_api_mismatch = 0
    added_intrinsics_missing = 0
    replaced_intrinsics_ellipsis = 0

    for row in rows:
        metadata = row.setdefault("metadata", {})
        context = str(row.get("context") or "")

        if "\\n" in context:
            context = context.replace("\\n", "\n")
            fixed_literal_backslash_n += 1

        code, elog = split_context(context)

        if elog and has_vector_size_mismatch(code, elog):
            elog = sanitize_error_log(elog)
            fixed_vector_mismatch += 1
        elif elog and is_scalarish_code(code) and HAS_BACKEND_VECTOR_ERR_RE.search(elog):
            elog = sanitize_error_log(elog)
            fixed_scalar_backend_vector_err += 1
        elif elog and has_high_conf_actionable_api_mismatch(code, elog):
            elog = generic_compile_error_log(elog)
            fixed_actionable_api_mismatch += 1

        context = rebuild_context(code, elog) if elog else code.rstrip()
        row["context"] = context

        intrinsics = metadata.get("intrinsics")
        if intrinsics is None:
            metadata["intrinsics"] = extract_intrinsics(code)
            added_intrinsics_missing += 1
        elif isinstance(intrinsics, list) and any(str(x).strip() == "..." for x in intrinsics):
            metadata["intrinsics"] = extract_intrinsics(code)
            replaced_intrinsics_ellipsis += 1

    train_rows = [r for r in rows if (r.get("metadata") or {}).get("split") == "train"]
    validation_rows = [r for r in rows if (r.get("metadata") or {}).get("split") == "validation"]

    summary = {
        "total_rows": len(rows),
        "fixed_literal_backslash_n": fixed_literal_backslash_n,
        "fixed_vector_mismatch": fixed_vector_mismatch,
        "fixed_scalar_backend_vector_err": fixed_scalar_backend_vector_err,
        "fixed_actionable_api_mismatch": fixed_actionable_api_mismatch,
        "added_intrinsics_missing": added_intrinsics_missing,
        "replaced_intrinsics_ellipsis": replaced_intrinsics_ellipsis,
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
    }
    print(json.dumps(summary, indent=2))

    if args.dry_run:
        return

    write_jsonl(all_path, rows)
    write_jsonl(train_path, train_rows)
    write_jsonl(validation_path, validation_rows)


if __name__ == "__main__":
    main()
