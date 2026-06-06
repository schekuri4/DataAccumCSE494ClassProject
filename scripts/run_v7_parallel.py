#!/usr/bin/env python3
"""Run the v7 dataset build across multiple corpus shards in parallel and merge the results.

This is a higher-throughput wrapper around build_v7_bug_dataset.py. It splits the corpus into
project shards, runs one build per shard in parallel, and then merges the shard outputs into the
final dataset directory.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
PYTHON = sys.executable


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-root", default=str(ROOT / "Work" / "clean_golden_repos"))
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "processed" / "v7"))
    ap.add_argument("--shards", type=int, default=7)
    ap.add_argument("--total-workers", type=int, default=28)
    ap.add_argument("--variants-per-project", type=int, default=20)
    ap.add_argument("--min-bugs", type=int, default=1)
    ap.add_argument("--max-bugs", type=int, default=4)
    ap.add_argument("--mutation-source", choices=["builtin", "generated", "all"], default="builtin")
    ap.add_argument("--use-all-mutations", action="store_true")
    ap.add_argument("--keep-baseline-dependency-failures", action="store_true")
    ap.add_argument("--validator-backend", choices=["wsl", "windows"], default="wsl")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--keep-workdir", action="store_true")
    ap.add_argument("--keep-shards", action="store_true", help="Keep per-shard dataset output directories after the merged dataset is written.")
    ap.add_argument("--skip-baseline-validation", action="store_true")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--generated-mutator-dir", default=None)
    ap.add_argument("--project-list", default=None)
    ap.add_argument("--wsl-distro", default=None)
    ap.add_argument("--wsl-validate-script", default=None)
    return ap.parse_args()


def run_shard(
    shard_index: int,
    shard_count: int,
    workers_per_shard: int,
    args: argparse.Namespace,
    shard_out_dir: Path,
) -> int:
    cmd = [
        PYTHON,
        str(SCRIPT_DIR / "build_v7_bug_dataset.py"),
        "--corpus-root",
        args.corpus_root,
        "--out-dir",
        str(shard_out_dir),
        "--variants-per-project",
        str(args.variants_per_project),
        "--min-bugs",
        str(args.min_bugs),
        "--max-bugs",
        str(args.max_bugs),
        "--mutation-source",
        args.mutation_source,
        "--validator-backend",
        args.validator_backend,
        "--workers",
        str(workers_per_shard),
        "--project-shards",
        str(shard_count),
        "--project-shard-index",
        str(shard_index),
        "--timeout",
        str(args.timeout),
    ]
    if args.keep_workdir:
        cmd.append("--keep-workdir")
    if args.skip_baseline_validation:
        cmd.append("--skip-baseline-validation")
    if args.no_resume:
        cmd.append("--no-resume")
    if args.use_all_mutations:
        cmd.append("--use-all-mutations")
    if args.keep_baseline_dependency_failures:
        cmd.append("--keep-baseline-dependency-failures")
    if args.generated_mutator_dir:
        cmd.extend(["--generated-mutator-dir", args.generated_mutator_dir])
    if args.project_list:
        cmd.extend(["--project-list", args.project_list])
    if args.wsl_distro:
        cmd.extend(["--wsl-distro", args.wsl_distro])
    if args.wsl_validate_script:
        cmd.extend(["--wsl-validate-script", args.wsl_validate_script])

    print(f"[parallel-v7] shard {shard_index + 1}/{shard_count} -> {shard_out_dir} workers={workers_per_shard}", flush=True)
    result = subprocess.run(cmd, cwd=str(ROOT), check=False)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result.returncode


def main() -> int:
    args = parse_args()
    if args.shards < 1:
        raise SystemExit("--shards must be >= 1")
    if args.total_workers < 1:
        raise SystemExit("--total-workers must be >= 1")
    if args.variants_per_project < 0:
        raise SystemExit("--variants-per-project must be >= 0")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shard_root = out_dir.parent / f"{out_dir.name}_shards"
    shard_root.mkdir(parents=True, exist_ok=True)

    workers_per_shard = max(1, math.ceil(args.total_workers / args.shards))
    shard_dirs = [shard_root / f"shard_{index:02d}" for index in range(args.shards)]
    for shard_dir in shard_dirs:
        shard_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=args.shards) as executor:
        futures = {
            executor.submit(run_shard, index, args.shards, workers_per_shard, args, shard_dirs[index]): index
            for index in range(args.shards)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - report shard failures after all jobs finish.
                failures.append(f"shard {index}: {type(exc).__name__}: {exc}")

    if failures:
        print("[parallel-v7] failures:")
        for failure in failures:
            print(f"[parallel-v7]   {failure}")
        return 1

    merge_cmd = [
        PYTHON,
        str(SCRIPT_DIR / "merge_v7_datasets.py"),
        "--out-dir",
        str(out_dir),
    ]
    for shard_dir in shard_dirs:
        merge_cmd.extend(["--input-dir", str(shard_dir)])

    print(f"[parallel-v7] merging {len(shard_dirs)} shard outputs into {out_dir}", flush=True)
    merge_result = subprocess.run(merge_cmd, cwd=str(ROOT), check=False)
    if merge_result.returncode != 0:
        return merge_result.returncode

    if not args.keep_shards:
        shutil.rmtree(shard_root, ignore_errors=True)
        print(f"[parallel-v7] removed shard outputs -> {shard_root}", flush=True)

    print(f"[parallel-v7] complete -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
