#!/usr/bin/env python3
"""Add clean/pass examples from compile-clean golden corpus projects.

The v10 repair dataset teaches patches for already-buggy projects. This script
adds the complementary examples: compile-clean projects where the correct answer
is explicitly no change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".inc",
    ".tcl",
    ".cfg",
    ".json",
    ".txt",
    ".md",
    ".mk",
    ".mak",
    ".cmake",
    ".py",
    ".sh",
    ".csv",
}

SKIP_DIRS = {
    ".git",
    ".github",
    ".xil",
    "__pycache__",
    "work",
    "build",
    "x86simulator_output",
    "aiesimulator_output",
}

SKIP_SUFFIXES = {
    ".o",
    ".so",
    ".a",
    ".dll",
    ".exe",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".xz",
    ".7z",
    ".log",
}


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    mime, _ = mimetypes.guess_type(path.name)
    return bool(mime and mime.startswith("text/"))


def iter_project_files(project_dir: Path, max_file_bytes: int) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(project_dir).as_posix()
        parts = {part.lower() for part in path.relative_to(project_dir).parts[:-1]}
        if parts & SKIP_DIRS:
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if not is_probably_text(path):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text:
            continue
        files.append((rel, text.rstrip()))
    return files


def format_files(files: list[tuple[str, str]], max_total_chars: int) -> tuple[str, list[str], bool]:
    chunks: list[str] = []
    included: list[str] = []
    total = 0
    truncated = False
    for rel, text in files:
        chunk = f"// FILE: {rel}\n{text}\n"
        if chunks and total + len(chunk) > max_total_chars:
            truncated = True
            break
        chunks.append(chunk)
        included.append(rel)
        total += len(chunk)
    return "\n".join(chunks).rstrip(), included, truncated


def load_existing_split_groups(v10_dir: Path) -> dict[str, str]:
    split_by_group: dict[str, str] = {}
    for split in ("train", "validation", "test"):
        path = v10_dir / f"aie_instruction_v10_{split}.jsonl"
        if not path.exists():
            continue
        for row in read_jsonl(path):
            group = row.get("metadata", {}).get("group_id")
            if group:
                split_by_group[group] = split
    return split_by_group


def assign_missing_groups(
    groups: list[str],
    split_by_group: dict[str, str],
    seed: int,
) -> dict[str, str]:
    assigned = dict(split_by_group)
    counts = Counter(assigned.values())
    rng = random.Random(seed)
    missing = [group for group in groups if group not in assigned]
    rng.shuffle(missing)
    targets = {"train": 0.80, "validation": 0.10, "test": 0.10}
    total_after = len(groups)
    target_counts = {split: round(total_after * ratio) for split, ratio in targets.items()}
    target_counts["test"] = total_after - target_counts["train"] - target_counts["validation"]

    for group in missing:
        split = min(
            ("train", "validation", "test"),
            key=lambda s: counts[s] / max(1, target_counts[s]),
        )
        assigned[group] = split
        counts[split] += 1
    return assigned


def make_clean_row(
    project_name: str,
    project_dir: Path,
    split: str,
    max_file_bytes: int,
    max_total_chars: int,
) -> dict[str, Any]:
    files = iter_project_files(project_dir, max_file_bytes=max_file_bytes)
    context_files, included_files, truncated = format_files(files, max_total_chars=max_total_chars)
    context = (
        "Compile-clean golden project files:\n"
        f"{context_files}\n\n"
        "--- Compile Status ---\n"
        "status: passes\n"
        "fix_required: false"
    ).rstrip()
    return {
        "instruction": (
            "This Versal AIE project is expected to compile without changes. "
            "If no fix is required, return exactly NO_CHANGE."
        ),
        "context": context,
        "response": "NO_CHANGE",
        "metadata": {
            "dataset_version": "v10",
            "task_type": "clean_pass_no_change",
            "split": split,
            "group_id": project_name,
            "source": project_name,
            "target": "unknown",
            "bug_count": 0,
            "bug_types": [],
            "categories": [],
            "compile_error_class": None,
            "baseline_validated": True,
            "fix_required": False,
            "synthetic": False,
            "corpus_source": "compile_clean_golden_project",
            "response_format": "no_change",
            "changed_files": [],
            "included_files": included_files,
            "included_file_count": len(included_files),
            "available_file_count": len(files),
            "context_truncated": truncated,
            "context_hash": stable_hash(context),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--v10-dir",
        default="data/processed/v10_group_holdout",
        help="Existing v10 dataset directory.",
    )
    parser.add_argument(
        "--golden-root",
        default="golden repos",
        help="Hydrated clean golden project root.",
    )
    parser.add_argument(
        "--project-list",
        default="outputs/v9_corpus_build/manifests/compile_clean_v9_toward200_projects.txt",
        help="One compile-clean project directory name per line.",
    )
    parser.add_argument("--seed", type=int, default=494)
    parser.add_argument("--max-file-bytes", type=int, default=200_000)
    parser.add_argument("--max-total-chars", type=int, default=250_000)
    args = parser.parse_args()

    v10_dir = Path(args.v10_dir)
    golden_root = Path(args.golden_root)
    project_names = [
        line.strip()
        for line in Path(args.project_list).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    existing_split_by_group = load_existing_split_groups(v10_dir)
    split_by_group = assign_missing_groups(project_names, existing_split_by_group, args.seed)

    clean_rows: list[dict[str, Any]] = []
    missing_projects: list[str] = []
    for project_name in project_names:
        project_dir = golden_root / project_name
        if not project_dir.exists():
            missing_projects.append(project_name)
            continue
        clean_rows.append(
            make_clean_row(
                project_name=project_name,
                project_dir=project_dir,
                split=split_by_group[project_name],
                max_file_bytes=args.max_file_bytes,
                max_total_chars=args.max_total_chars,
            )
        )

    repairs = {
        "train": read_jsonl(v10_dir / "aie_instruction_v10_train.jsonl"),
        "validation": read_jsonl(v10_dir / "aie_instruction_v10_validation.jsonl"),
        "test": read_jsonl(v10_dir / "aie_instruction_v10_test.jsonl"),
    }
    clean_by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in clean_rows:
        clean_by_split[row["metadata"]["split"]].append(row)

    combined: dict[str, list[dict[str, Any]]] = {}
    for split in ("train", "validation", "test"):
        combined[split] = repairs[split] + clean_by_split[split]

    all_clean = clean_by_split["train"] + clean_by_split["validation"] + clean_by_split["test"]
    all_combined = combined["train"] + combined["validation"] + combined["test"]

    write_jsonl(v10_dir / "aie_instruction_v10_clean_pass.jsonl", all_clean)
    write_jsonl(v10_dir / "aie_instruction_v10_all_with_clean.jsonl", all_combined)
    for split in ("train", "validation", "test"):
        write_jsonl(v10_dir / f"aie_instruction_v10_{split}_with_clean.jsonl", combined[split])

    groups_by_split = defaultdict(set)
    for row in all_combined:
        groups_by_split[row["metadata"]["split"]].add(row["metadata"]["group_id"])

    manifest = {
        "dataset_version": "v10",
        "description": "v10 repair dataset plus compile-clean no-change examples.",
        "source_clean_project_list": str(Path(args.project_list)),
        "clean_rows": len(all_clean),
        "clean_rows_by_split": dict(Counter(row["metadata"]["split"] for row in all_clean)),
        "repair_rows_by_split": {split: len(rows) for split, rows in repairs.items()},
        "combined_rows": len(all_combined),
        "combined_rows_by_split": {split: len(rows) for split, rows in combined.items()},
        "combined_group_counts": {split: len(groups) for split, groups in groups_by_split.items()},
        "combined_group_overlap_train_validation": len(groups_by_split["train"] & groups_by_split["validation"]),
        "combined_group_overlap_train_test": len(groups_by_split["train"] & groups_by_split["test"]),
        "combined_group_overlap_validation_test": len(groups_by_split["validation"] & groups_by_split["test"]),
        "missing_projects": missing_projects,
        "files": {
            "clean_pass": "aie_instruction_v10_clean_pass.jsonl",
            "all_with_clean": "aie_instruction_v10_all_with_clean.jsonl",
            "train_with_clean": "aie_instruction_v10_train_with_clean.jsonl",
            "validation_with_clean": "aie_instruction_v10_validation_with_clean.jsonl",
            "test_with_clean": "aie_instruction_v10_test_with_clean.jsonl",
        },
    }
    (v10_dir / "manifest_summary_v10_with_clean.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
