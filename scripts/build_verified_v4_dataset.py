#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = ROOT / "data" / "processed" / "archive_v2_and_synthpairs_20260425_014921"
V3_DIR = ROOT / "data" / "processed" / "v3"
V4_DIR = ROOT / "data" / "processed" / "v4"
AIE_DATASET_DIR = ROOT / "aie_dataset"
CACHE_CORPUS_DIR = ROOT / ".cache" / "aie_source_corpus"

SOURCE_SUFFIXES = {".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"}
CLEAN_CORPUS_MAX_BLOB_SIZE = 20_000
NEGATIVE_BUG_TYPE_CAP = 80
REAL_NEGATIVE_VARIANTS = {
    "bug_fix_pair",
    "bug_fix_pair_cropped",
    "bug_fix_pair_compiler_error",
    "taxonomy_debug_scenario",
    "taxonomy_multi_file_debug_scenario",
    "multi_file_bug_fix_pair",
}
SEED_DIRS = ("debug_pairs", "dsp_fir", "dsp_beamforming", "matrix_ops", "graphs")


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
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


def stable_hash(text: str) -> int:
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest(), 16)


def assign_split(group_key: str, validation_pct: int = 20) -> str:
    return "validation" if (stable_hash(group_key) % 100) < validation_pct else "train"


def strip_fence(text: str) -> str:
    if not text:
        return ""
    match = re.match(r"^```[a-zA-Z0-9_+\-]*\n(.*?)\n```\s*$", text, flags=re.DOTALL)
    return match.group(1) if match else text


def fence_cpp(text: str) -> str:
    body = strip_fence(text).rstrip()
    return f"```cpp\n{body}\n```"


def humanize_bug_label(row_or_meta: dict) -> str:
    if isinstance(row_or_meta.get("metadata"), dict):
        meta = row_or_meta.get("metadata") or {}
    else:
        meta = row_or_meta
    label = meta.get("bug_label") or row_or_meta.get("label") or meta.get("bug_type") or row_or_meta.get("bug_type") or "bug"
    label = str(label).replace("_", " ").strip()
    label = re.sub(r"\s+", " ", label)
    return label


def is_real_provenance(meta: dict) -> bool:
    source = str(meta.get("source") or "")
    source_repo = str(meta.get("source_repo") or "")
    source_path = str(meta.get("source_path") or "")
    relative_path = str(meta.get("relative_path") or "")
    category = str(meta.get("category") or "")
    if source_repo.startswith("synthetic_taxonomy:"):
        return False
    if source_path.startswith("synthetic/") or source.startswith("synthetic/"):
        return False
    if category == "synthetic_taxonomy":
        return False
    if source.startswith("http://") or source.startswith("https://"):
        return True
    if source_path.startswith("aie_dataset/") or relative_path.startswith("aie_dataset/"):
        return True
    return any(relative_path.startswith(prefix + "/") for prefix in SEED_DIRS)


def row_key(row: dict) -> tuple[str, str, str]:
    meta = row.get("metadata", {}) or {}
    return (
        str(meta.get("group_id") or ""),
        str(row.get("instruction") or ""),
        str(row.get("context") or ""),
    )


def is_clean_corpus_source_path(path: str) -> bool:
    path_obj = Path(path)
    if path_obj.suffix.lower() not in SOURCE_SUFFIXES:
        return False
    low = str(path).replace("\\", "/").lower()
    if "/test/" in low or "/tests/" in low or low.startswith("test/") or low.startswith("tests/"):
        return False
    if "/experimental/" in low or "/extern/" in low or "/third_party/" in low:
        return False
    if "/.autopilot/" in low or "/_x/" in low:
        return False
    if "buggy" in low or "debug_pairs" in low:
        return False
    return (
        "/aie/" in low
        or low.startswith("aie/")
        or "aie_" in low
        or "/aiesrc/" in low
        or "/aie_src/" in low
    )


def load_blob_text(blob_path: Path) -> str:
    blob = json.loads(blob_path.read_text(encoding="utf-8"))
    content = str(blob.get("content") or "")
    if not content:
        return ""
    if blob.get("encoding") == "base64":
        return base64.b64decode(content.encode("ascii")).decode("utf-8", errors="replace")
    return content


def repo_full_name_from_tree(tree: dict) -> str:
    url = str(tree.get("url") or "")
    match = re.search(r"/repos/([^/]+/[^/]+)/git/trees/", url)
    return match.group(1) if match else ""


def cached_blob_path(corpus_dir: Path, full_name: str, blob_sha: str) -> Path:
    url = f"https://api.github.com/repos/{full_name}/git/blobs/{blob_sha}"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return corpus_dir / "blobs" / f"{digest}.json"


def normalize_input_path(path: str | Path) -> str:
    text = str(path).strip().replace("\\", "/")
    if text.lower().startswith("/mnt/") and len(text) > 6 and text[5].isalpha() and text[6] == "/":
        text = f"{text[5].upper()}:/{text[7:]}"
    return text.lower()


def dataset_keys_for_path(input_path: str | Path, row_index: int) -> list[tuple[str, int]]:
    normalized = normalize_input_path(input_path)
    keys = [(normalized, int(row_index))]
    basename = Path(str(input_path)).name.lower()
    if basename and basename != normalized:
        keys.append((basename, int(row_index)))
    return keys


def load_dataset_map(paths: list[Path]) -> dict[tuple[str, int], dict]:
    dataset_map: dict[tuple[str, int], dict] = {}
    for path in paths:
        rows = load_jsonl(path)
        for row_index, row in enumerate(rows):
            for key in dataset_keys_for_path(path.resolve(), row_index):
                dataset_map.setdefault(key, row)
    return dataset_map


def make_compile_proven_rows(results_path: Path, dataset_paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    dataset_map = load_dataset_map(dataset_paths)
    for result_row in load_jsonl(results_path):
        if not result_row.get("compile_ok"):
            continue
        row_index = int(result_row.get("row_index", -1))
        orig_row = None
        for key in dataset_keys_for_path(result_row.get("input_path", ""), row_index):
            orig_row = dataset_map.get(key)
            if orig_row is not None:
                break
        if orig_row is None:
            continue
        new_row = dict(orig_row)
        meta = dict(new_row.get("metadata") or {})
        meta["v4_bucket"] = "compile_validated_original"
        meta["original_compile_ok"] = True
        meta["original_error_class"] = result_row.get("error_class")
        new_row["metadata"] = meta
        rows.append(new_row)
    return rows


def make_validated_replacement_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        for row in load_jsonl(path):
            meta = dict(row.get("metadata") or {})
            meta["v4_bucket"] = "compile_validated_replacement"
            row["metadata"] = meta
            rows.append(row)
    return rows


def make_real_negative_rows(v2_paths: list[Path], validated_keys: set[tuple[str, str, str]]) -> list[dict]:
    rows: list[dict] = []
    for path in v2_paths:
        for row in load_jsonl(path):
            meta = row.get("metadata") or {}
            if row_key(row) in validated_keys:
                continue
            if not is_real_provenance(meta):
                continue
            if str(meta.get("variant") or "") not in REAL_NEGATIVE_VARIANTS:
                continue
            if not meta.get("bug_type"):
                continue
            correct = strip_fence(str(row.get("response") or "")).strip()
            if not correct:
                continue
            label = humanize_bug_label(meta)
            split = str(meta.get("split") or assign_split(str(meta.get("group_id") or label)))
            rows.append({
                "instruction": f"Does this AIE source exhibit the {label} issue? Inspect the code and answer yes or no.",
                "context": correct,
                "response": f"No - the {label} pattern is not present in this source.",
                "metadata": {
                    "variant": "v4_negative_from_unvalidated_real_debug_issue",
                    "split": split,
                    "group_id": str(meta.get("group_id") or ""),
                    "bug_type": meta.get("bug_type"),
                    "bug_label": label,
                    "source": meta.get("source"),
                    "source_repo": meta.get("source_repo"),
                    "source_path": meta.get("source_path"),
                    "relative_path": meta.get("relative_path"),
                    "difficulty_tier": meta.get("difficulty_tier"),
                    "synthetic": False,
                    "verdict": "not_present",
                    "v4_bucket": "negative_from_unvalidated_real_debug_issue",
                    "v4_parent_variant": meta.get("variant"),
                },
            })
    return rows


def make_seed_rows(aie_dataset_dir: Path) -> list[dict]:
    rows: list[dict] = []
    seed_files: list[Path] = []
    for seed_dir in SEED_DIRS:
        root = aie_dataset_dir / seed_dir
        if not root.exists():
            continue
        seed_files.extend(
            file_path for file_path in root.rglob("*")
            if file_path.is_file() and file_path.suffix.lower() in SOURCE_SUFFIXES
        )
    seed_files = sorted(seed_files)
    file_text: dict[Path, str] = {
        path: path.read_text(encoding="utf-8", errors="replace") for path in seed_files
    }

    paired_bug_files = {
        "data_shuffle_BUGGY_deadlock.cc": "data_shuffle_CORRECT.cc",
        "peak_detect_BUGGY_oob.cc": "peak_detect.cc",
    }
    by_name = {path.name: path for path in seed_files}

    for buggy_name, correct_name in paired_bug_files.items():
        buggy_path = by_name.get(buggy_name)
        correct_path = by_name.get(correct_name)
        if not buggy_path or not correct_path:
            continue
        group_key = f"seed_pair:{buggy_name}"
        rows.append({
            "instruction": "Fix the bug in this curated AIE source and return the full corrected file.",
            "context": file_text[buggy_path],
            "response": fence_cpp(file_text[correct_path]),
            "metadata": {
                "variant": "v4_seed_bug_fix",
                "split": assign_split(group_key),
                "group_id": group_key,
                "bug_type": "curated_seed_debug_pair",
                "bug_label": buggy_name.replace("_", " "),
                "source_repo": "aie_dataset",
                "source_path": str(buggy_path.relative_to(ROOT)).replace("\\", "/"),
                "relative_path": str(buggy_path.relative_to(AIE_DATASET_DIR)).replace("\\", "/"),
                "synthetic": False,
                "v4_bucket": "curated_seed_bug_fix",
            },
        })

    for path in seed_files:
        if "BUGGY" in path.name:
            continue
        rel = str(path.relative_to(AIE_DATASET_DIR)).replace("\\", "/")
        group_key = f"seed_clean:{rel}"
        rows.append({
            "instruction": "Review this curated AIE source and tell me whether it is correct or has a defect.",
            "context": file_text[path],
            "response": "This source appears correct as written. I do not see a defect.",
            "metadata": {
                "variant": "v4_seed_clean_code",
                "split": assign_split(group_key),
                "group_id": group_key,
                "bug_type": None,
                "source_repo": "aie_dataset",
                "source_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "relative_path": rel,
                "synthetic": False,
                "verdict": "correct",
                "v4_bucket": "curated_seed_clean",
            },
        })
    return rows


def make_clean_corpus_rows(corpus_dir: Path, max_blob_size: int) -> list[dict]:
    rows: list[dict] = []
    seen_shas: set[str] = set()
    trees_dir = corpus_dir / "trees"
    blobs_dir = corpus_dir / "blobs"
    if not trees_dir.exists() or not blobs_dir.exists():
        return rows

    for tree_path in sorted(trees_dir.glob("*.json")):
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        full_name = repo_full_name_from_tree(tree)
        if not full_name:
            continue
        for entry in tree.get("tree", []):
            if entry.get("type") != "blob":
                continue
            relative_path = str(entry.get("path") or "").replace("\\", "/")
            if not is_clean_corpus_source_path(relative_path):
                continue
            blob_sha = str(entry.get("sha") or "")
            if not blob_sha or blob_sha in seen_shas:
                continue
            blob_size = int(entry.get("size") or 0)
            if blob_size <= 0 or blob_size > max_blob_size:
                continue
            blob_path = cached_blob_path(corpus_dir, full_name, blob_sha)
            if not blob_path.exists():
                continue
            source_text = load_blob_text(blob_path).strip()
            if not source_text:
                continue
            group_key = f"clean_corpus:{blob_sha}"
            rows.append({
                "instruction": "Use this known-good AIE source as a clean reference example and return the full file.",
                "context": f"Known-good AIE reference path: {relative_path}",
                "response": fence_cpp(source_text),
                "metadata": {
                    "variant": "v4_clean_corpus_reference",
                    "split": assign_split(group_key),
                    "group_id": group_key,
                    "bug_type": None,
                    "bug_label": None,
                    "source_repo": "aie_source_corpus",
                    "source_path": relative_path,
                    "relative_path": relative_path,
                    "synthetic": False,
                    "verdict": "correct_reference",
                    "blob_sha": blob_sha,
                    "blob_size": blob_size,
                    "v4_bucket": "clean_corpus_reference",
                },
            })
            seen_shas.add(blob_sha)
    return rows


def dedup_rows(rows: list[dict]) -> list[dict]:
    kept: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = json.dumps(
            {
                "instruction": row.get("instruction"),
                "context": row.get("context"),
                "response": row.get("response"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def cap_negative_rows_per_bug_type(rows: list[dict], per_bug_type_cap: int) -> list[dict]:
    if per_bug_type_cap <= 0:
        return rows
    kept: list[dict] = []
    counts: Counter[str] = Counter()
    for row in rows:
        meta = row.get("metadata") or {}
        bug_type = str(meta.get("bug_type") or "")
        if not bug_type:
            continue
        if counts[bug_type] >= per_bug_type_cap:
            continue
        counts[bug_type] += 1
        kept.append(row)
    return kept


def summarize(rows: list[dict]) -> dict:
    variant_counts = Counter(str((row.get("metadata") or {}).get("variant") or "<none>") for row in rows)
    bucket_counts = Counter(str((row.get("metadata") or {}).get("v4_bucket") or "<none>") for row in rows)
    split_counts = Counter(str((row.get("metadata") or {}).get("split") or "<none>") for row in rows)
    return {
        "rows": len(rows),
        "bucket_counts": dict(bucket_counts),
        "split_counts": dict(split_counts),
        "variant_counts": dict(variant_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a verified V4 dataset from compile-proven positives plus safe negatives and clean baseline references.")
    parser.add_argument("--compile-results", default=str(V3_DIR / "aie_instruction_v2_all_64w.jsonl"))
    parser.add_argument("--validated", nargs="+", default=[
        str(V3_DIR / "bedrock_fixed_synth_validated.jsonl"),
        str(V3_DIR / "bedrock_fixed_real_validated.jsonl"),
    ])
    parser.add_argument("--v2-base", nargs="+", default=[
        str(ARCHIVE_DIR / "aie_instruction_v2_all.jsonl"),
        str(ARCHIVE_DIR / "aie_instruction_v2_train.jsonl"),
        str(ARCHIVE_DIR / "aie_instruction_v2_validation.jsonl"),
    ])
    parser.add_argument("--clean-corpus-dir", default=str(CACHE_CORPUS_DIR))
    parser.add_argument("--clean-corpus-max-size", type=int, default=CLEAN_CORPUS_MAX_BLOB_SIZE)
    parser.add_argument("--negative-bug-type-cap", type=int, default=NEGATIVE_BUG_TYPE_CAP)
    parser.add_argument("--out-dir", default=str(V4_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    v2_base_paths = [Path(path) for path in args.v2_base]
    compile_proven_rows = make_compile_proven_rows(Path(args.compile_results), v2_base_paths)
    validated_rows = make_validated_replacement_rows([Path(path) for path in args.validated])
    validated_keys = {row_key(row) for row in validated_rows}
    negative_rows = cap_negative_rows_per_bug_type(
        dedup_rows(make_real_negative_rows(v2_base_paths, validated_keys)),
        args.negative_bug_type_cap,
    )
    seed_rows = make_seed_rows(AIE_DATASET_DIR)
    clean_corpus_rows = make_clean_corpus_rows(Path(args.clean_corpus_dir), args.clean_corpus_max_size)

    all_rows = dedup_rows(
        compile_proven_rows + validated_rows + negative_rows + seed_rows + clean_corpus_rows
    )
    train_rows = [row for row in all_rows if (row.get("metadata") or {}).get("split") == "train"]
    validation_rows = [row for row in all_rows if (row.get("metadata") or {}).get("split") == "validation"]

    write_jsonl(out_dir / "aie_instruction_v4_all.jsonl", all_rows)
    write_jsonl(out_dir / "aie_instruction_v4_train.jsonl", train_rows)
    write_jsonl(out_dir / "aie_instruction_v4_validation.jsonl", validation_rows)

    summary = {
        "compile_proven_rows": len(compile_proven_rows),
        "validated_replacement_rows": len(validated_rows),
        "negative_rows": len(negative_rows),
        "negative_bug_type_cap": args.negative_bug_type_cap,
        "seed_rows": len(seed_rows),
        "clean_corpus_rows": len(clean_corpus_rows),
        "all": summarize(all_rows),
        "train": summarize(train_rows),
        "validation": summarize(validation_rows),
    }
    (out_dir / "aie_instruction_v4_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()