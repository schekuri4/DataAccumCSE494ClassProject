#!/usr/bin/env python3
"""Prepare a cleaner v10 dataset from the v9 repair JSONL.

This pass addresses the most important issues from the v9 audit:

* split by group_id instead of random row split
* remove duplicate code/log/patch patterns before splitting
* quarantine tool/environment failure rows from normal repair training
* strip noisy local environment lines from compiler logs used in context
* add explicit metadata for synthetic mutation provenance
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ERROR_LOG_MARKER = "\n--- Error Log ---\n"

DEFAULT_QUARANTINE_CLASSES = {
    "no_compile_result",
    "validation_timeout",
    "exception",
}

TOOL_FAILURE_PATTERNS = (
    re.compile(r"\bSegmentation fault\b", re.IGNORECASE),
    re.compile(r"\bbad_alloc\b", re.IGNORECASE),
    re.compile(r"\bcore dumped\b", re.IGNORECASE),
)

NOISY_LOG_PATTERNS = (
    re.compile(r"your \d+x\d+ screen size is bogus\. expect trouble", re.IGNORECASE),
    re.compile(r"^\[wsl-validate\]\s+license:.*$", re.IGNORECASE),
    re.compile(r"^\[wsl-validate\]\s+project:.*$", re.IGNORECASE),
    re.compile(r"^\[validate\]\s+.*root\s*:.*$", re.IGNORECASE),
    re.compile(r"^\[validate\]\s+xchesscc\s*:.*$", re.IGNORECASE),
    re.compile(r"^\[validate\]\s+aiecompiler\s*:.*$", re.IGNORECASE),
    re.compile(r"^\[validate\]\s+v\+\+ AIE mode\s*:.*$", re.IGNORECASE),
)

DIAGNOSTIC_RE = re.compile(
    r"(?P<line>.*(?:error:|fatal error:|warning:|undefined reference|ld:|aiecompiler).*)",
    re.IGNORECASE,
)

WINDOWS_PATH_RE = re.compile(r"[A-Z]:\\[^\s'\"<>]+")
WSL_PROJECT_PATH_RE = re.compile(r"/mnt/c/Users/[^/\s]+/[^\s'\"<>]+")
HOME_PATH_RE = re.compile(r"/home/[^/\s]+/[^\s'\"<>]+")


@dataclass
class PreparedRows:
    train: list[dict[str, Any]]
    validation: list[dict[str, Any]]
    test: list[dict[str, Any]]
    quarantine: list[dict[str, Any]]
    removed_duplicates: list[dict[str, Any]]
    stats: dict[str, Any]


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def normalize_text(text: str) -> str:
    text = WINDOWS_PATH_RE.sub("<WINDOWS_PATH>", text)
    text = WSL_PROJECT_PATH_RE.sub("<WSL_PROJECT_PATH>", text)
    text = HOME_PATH_RE.sub("<HOME_PATH>", text)
    return text


def split_context(context: str) -> tuple[str, str]:
    if ERROR_LOG_MARKER not in context:
        return context, ""
    code, log = context.split(ERROR_LOG_MARKER, 1)
    return code.rstrip(), log.strip()


def clean_log(raw_log: str) -> str:
    kept: list[str] = []
    for line in raw_log.splitlines():
        if any(p.search(line) for p in NOISY_LOG_PATTERNS):
            continue
        line = normalize_text(line.rstrip())
        if line:
            kept.append(line)
    return "\n".join(kept).strip()


def first_diagnostic(cleaned_log: str) -> str | None:
    for line in cleaned_log.splitlines():
        match = DIAGNOSTIC_RE.search(line)
        if match:
            return match.group("line").strip()
    return None


def has_tool_failure(raw_log: str) -> bool:
    return any(p.search(raw_log) for p in TOOL_FAILURE_PATTERNS)


def rewrite_context(context: str, cleaned_log: str) -> str:
    code, _ = split_context(context)
    if not cleaned_log:
        return code.rstrip()
    return f"{code.rstrip()}{ERROR_LOG_MARKER}{cleaned_log}"


def row_group(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return metadata.get("group_id") or metadata.get("source") or "unknown_group"


def split_groups_by_rows(
    rows: list[dict[str, Any]],
    train_ratio: float,
    validation_ratio: float,
    seed: int,
) -> dict[str, str]:
    by_group: dict[str, int] = Counter(row_group(row) for row in rows)
    groups = list(by_group)
    rng = random.Random(seed)
    rng.shuffle(groups)

    total = sum(by_group.values())
    targets = {
        "train": int(round(total * train_ratio)),
        "validation": int(round(total * validation_ratio)),
    }
    targets["test"] = max(0, total - targets["train"] - targets["validation"])

    assigned_counts = {"train": 0, "validation": 0, "test": 0}
    assignment: dict[str, str] = {}

    # Greedy row-balanced assignment with group isolation. Prefer the split
    # whose current fill fraction is furthest below target.
    for group in sorted(groups, key=lambda g: by_group[g], reverse=True):
        deficits: list[tuple[float, str]] = []
        for split, target in targets.items():
            if target <= 0:
                deficits.append((-999.0, split))
                continue
            fill = assigned_counts[split] / target
            deficits.append((fill, split))
        _, chosen = min(deficits, key=lambda item: item[0])
        assignment[group] = chosen
        assigned_counts[chosen] += by_group[group]

    return assignment


def prepare_rows(
    input_path: Path,
    train_ratio: float,
    validation_ratio: float,
    seed: int,
    quarantine_classes: set[str],
    quarantine_tool_failures: bool,
) -> PreparedRows:
    raw_rows = [
        json.loads(line)
        for line in input_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    seen_full_context: set[str] = set()
    seen_code_context: set[str] = set()
    seen_response: set[str] = set()
    seen_group_bug_files_response: set[str] = set()

    kept: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    removed_duplicates: list[dict[str, Any]] = []
    duplicate_reasons: Counter[str] = Counter()
    quarantine_reasons: Counter[str] = Counter()

    for index, row in enumerate(raw_rows):
        metadata = dict(row.get("metadata") or {})
        context = row.get("context") or ""
        response = row.get("response") or ""
        code_context, raw_log = split_context(context)
        cleaned_log = clean_log(raw_log)
        diagnostic = first_diagnostic(cleaned_log)

        full_context_hash = stable_hash(context)
        code_context_hash = stable_hash(code_context)
        response_hash = stable_hash(response)
        group_bug_files_response_hash = stable_hash(
            json.dumps(
                {
                    "group_id": metadata.get("group_id"),
                    "bug_types": metadata.get("bug_types") or [],
                    "changed_files": metadata.get("changed_files") or [],
                    "response": response,
                },
                sort_keys=True,
            )
        )

        duplicate_reason = None
        if full_context_hash in seen_full_context:
            duplicate_reason = "full_context"
        elif code_context_hash in seen_code_context:
            duplicate_reason = "code_context_without_log"
        elif response_hash in seen_response:
            duplicate_reason = "response"
        elif group_bug_files_response_hash in seen_group_bug_files_response:
            duplicate_reason = "group_bug_files_response"

        if duplicate_reason:
            duplicate_reasons[duplicate_reason] += 1
            duplicate_meta = dict(metadata)
            duplicate_meta.update(
                {
                    "v10_removed_reason": duplicate_reason,
                    "v10_original_index": index,
                }
            )
            removed_duplicates.append(
                {
                    "instruction": row.get("instruction"),
                    "context": rewrite_context(context, cleaned_log),
                    "response": response,
                    "metadata": duplicate_meta,
                }
            )
            continue

        seen_full_context.add(full_context_hash)
        seen_code_context.add(code_context_hash)
        seen_response.add(response_hash)
        seen_group_bug_files_response.add(group_bug_files_response_hash)

        compile_error_class = metadata.get("compile_error_class")
        tool_failure = has_tool_failure(raw_log) if raw_log else False
        should_quarantine = compile_error_class in quarantine_classes or (
            quarantine_tool_failures and tool_failure
        )

        metadata.update(
            {
                "dataset_version": "v10",
                "source_dataset_version": "v9",
                "task_type": "repair_diff",
                "synthetic": True,
                "mutation_source": "generated_from_compile_clean_golden_project",
                "raw_log_available": bool(raw_log),
                "clean_log_available": bool(cleaned_log),
                "first_diagnostic": diagnostic,
                "tool_failure_marker": tool_failure,
                "dedupe_hashes": {
                    "full_context": full_context_hash,
                    "code_context_without_log": code_context_hash,
                    "response": response_hash,
                    "group_bug_files_response": group_bug_files_response_hash,
                },
            }
        )

        prepared = {
            "instruction": row.get("instruction"),
            "context": rewrite_context(context, cleaned_log),
            "response": response,
            "metadata": metadata,
        }

        if should_quarantine:
            reason = (
                "tool_failure_marker"
                if tool_failure and compile_error_class not in quarantine_classes
                else str(compile_error_class)
            )
            quarantine_reasons[reason] += 1
            prepared["metadata"]["split"] = "quarantine"
            prepared["metadata"]["v10_quarantine_reason"] = reason
            quarantine.append(prepared)
        else:
            kept.append(prepared)

    assignment = split_groups_by_rows(kept, train_ratio, validation_ratio, seed)
    splits = {"train": [], "validation": [], "test": []}
    for row in kept:
        split = assignment[row_group(row)]
        row["metadata"]["split"] = split
        splits[split].append(row)

    group_by_split = defaultdict(set)
    for split, split_rows in splits.items():
        for row in split_rows:
            group_by_split[split].add(row_group(row))

    stats = {
        "source_file": str(input_path),
        "source_rows": len(raw_rows),
        "kept_repair_rows": len(kept),
        "train_rows": len(splits["train"]),
        "validation_rows": len(splits["validation"]),
        "test_rows": len(splits["test"]),
        "quarantine_rows": len(quarantine),
        "removed_duplicate_rows": len(removed_duplicates),
        "duplicate_reasons": dict(duplicate_reasons),
        "quarantine_reasons": dict(quarantine_reasons),
        "group_counts": {split: len(groups) for split, groups in group_by_split.items()},
        "group_overlap_train_validation": len(group_by_split["train"] & group_by_split["validation"]),
        "group_overlap_train_test": len(group_by_split["train"] & group_by_split["test"]),
        "group_overlap_validation_test": len(group_by_split["validation"] & group_by_split["test"]),
        "compile_error_classes": dict(Counter(row["metadata"].get("compile_error_class") for row in kept)),
        "bug_count_distribution": dict(Counter(row["metadata"].get("bug_count") for row in kept)),
        "targets": dict(Counter(row["metadata"].get("target") for row in kept)),
        "unique_bug_types": len(
            {
                bug
                for row in kept
                for bug in (row["metadata"].get("bug_types") or [])
            }
        ),
    }

    return PreparedRows(
        train=splits["train"],
        validation=splits["validation"],
        test=splits["test"],
        quarantine=quarantine,
        removed_duplicates=removed_duplicates,
        stats=stats,
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="data/processed/v9_dataset_40variants/aie_instruction_v9_all.jsonl",
        help="Input v9 JSONL.",
    )
    parser.add_argument(
        "--out-dir",
        default="data/processed/v10_group_holdout",
        help="Output directory for v10 JSONL files.",
    )
    parser.add_argument("--train-ratio", type=float, default=0.80)
    parser.add_argument("--validation-ratio", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=494)
    parser.add_argument(
        "--keep-tool-failures",
        action="store_true",
        help="Do not quarantine rows whose raw logs contain tool-crash markers.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    prepared = prepare_rows(
        input_path=input_path,
        train_ratio=args.train_ratio,
        validation_ratio=args.validation_ratio,
        seed=args.seed,
        quarantine_classes=set(DEFAULT_QUARANTINE_CLASSES),
        quarantine_tool_failures=not args.keep_tool_failures,
    )

    all_rows = prepared.train + prepared.validation + prepared.test
    write_jsonl(out_dir / "aie_instruction_v10_all.jsonl", all_rows)
    write_jsonl(out_dir / "aie_instruction_v10_train.jsonl", prepared.train)
    write_jsonl(out_dir / "aie_instruction_v10_validation.jsonl", prepared.validation)
    write_jsonl(out_dir / "aie_instruction_v10_test.jsonl", prepared.test)
    write_jsonl(out_dir / "aie_instruction_v10_quarantine.jsonl", prepared.quarantine)
    write_jsonl(out_dir / "aie_instruction_v10_removed_duplicates.jsonl", prepared.removed_duplicates)

    manifest = {
        "dataset_version": "v10",
        "description": "Group-held-out, deduplicated, clean-log repair dataset derived from v9.",
        "files": {
            "all": "aie_instruction_v10_all.jsonl",
            "train": "aie_instruction_v10_train.jsonl",
            "validation": "aie_instruction_v10_validation.jsonl",
            "test": "aie_instruction_v10_test.jsonl",
            "quarantine": "aie_instruction_v10_quarantine.jsonl",
            "removed_duplicates": "aie_instruction_v10_removed_duplicates.jsonl",
        },
        **prepared.stats,
    }
    (out_dir / "manifest_summary_v10.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
