#!/usr/bin/env python3
"""Audit baseline compile health for golden projects before mutation."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import tempfile
import time
from pathlib import Path

from build_v7_bug_dataset import (
    compile_project,
    detect_toolchain,
    extract_error_log,
    extract_missing_headers,
    format_marked_project,
    iter_corpus_projects,
    serialize_toolchain,
)


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DEFAULT_WSL_VALIDATE_SCRIPT = ROOT / "scripts" / "run_validate_wsl.sh"
DEFAULT_WSL_DISTRO = "Ubuntu-24.04"
DEFAULT_AIE_PART = "xcvc1902-vsva2197-2MP-e-S"
DEFAULT_AIEML_PART = "xcve2802-vsvh1760-2MP-e-S"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-root", default=str(ROOT / "golden repos"))
    ap.add_argument("--out", default=str(ROOT / "outputs" / "baseline_audit_v9.jsonl"))
    ap.add_argument("--summary-out", default=str(ROOT / "outputs" / "baseline_audit_v9_summary.json"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--project-contains",
        action="append",
        default=[],
        help="Only audit projects whose directory name contains this text. Can be passed multiple times.",
    )
    ap.add_argument("--target", choices=["auto", "AIE", "AIE-ML"], default="auto")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--baseline-max-dependency-retries", type=int, default=4)
    # aiecompiler graph checks fan out internally (often make -j4), so 16
    # project workers saturates this 16-core/32-thread workstation without the
    # no-result flakes seen at 32 concurrent projects.
    ap.add_argument("--workers", type=int, default=min(16, max(1, os.cpu_count() or 1)))
    ap.add_argument("--progress-every", type=int, default=10)
    ap.add_argument("--validator-backend", choices=["wsl", "windows"], default="wsl")
    ap.add_argument("--aie-part", default=DEFAULT_AIE_PART)
    ap.add_argument("--aieml-part", default=DEFAULT_AIEML_PART)
    ap.add_argument("--aietools", default=None)
    ap.add_argument("--vitis", default=None)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--keep-workdir", action="store_true")
    ap.add_argument("--wsl-distro", default=DEFAULT_WSL_DISTRO)
    ap.add_argument("--wsl-validate-script", default=str(DEFAULT_WSL_VALIDATE_SCRIPT))
    return ap.parse_args()


def add_stub_headers(file_text: dict[str, str], headers: list[str]) -> int:
    added = 0
    for header in headers:
        rel = Path(header).as_posix().lstrip("/")
        if rel in file_text:
            continue
        guard = "".join(ch if ch.isalnum() else "_" for ch in rel.upper())
        file_text[rel] = (
            f"#ifndef __AUTO_V9_BASELINE_STUB_{guard}__\n"
            f"#define __AUTO_V9_BASELINE_STUB_{guard}__\n"
            "// Auto-generated baseline dependency stub during audit.\n"
            "#endif\n"
        )
        added += 1
    return added


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def audit_project(project, args: argparse.Namespace, tc_payload: dict | None, workdir_root: Path) -> dict:
    baseline_files = dict(project.file_text)
    correct_code = format_marked_project(baseline_files)
    part = args.aie_part if project.target == "AIE" else args.aieml_part
    seen_missing_headers: set[str] = set()
    retry_count = 0
    added_count_total = 0
    baseline = None
    final_missing_headers: list[str] = []

    for retry_index in range(max(0, args.baseline_max_dependency_retries) + 1):
        baseline = compile_project(
            tc_payload=tc_payload,
            project_code=correct_code,
            project=project,
            timeout_s=args.timeout,
            workdir_root=workdir_root,
            keep_workdir=args.keep_workdir,
            project_key=project.project_dir.name,
            part=part,
            args=args,
        )
        if baseline.get("compile_ok"):
            break

        error_log = extract_error_log(baseline)
        missing_headers = [
            header
            for header in extract_missing_headers(error_log)
            if header not in seen_missing_headers
        ]
        final_missing_headers = missing_headers
        error_class = str(baseline.get("error_class") or "compile_error")
        if error_class not in {"missing_dependency", "missing_dependency_after_stub"}:
            break
        if retry_index >= args.baseline_max_dependency_retries:
            break
        if not missing_headers:
            break

        added_count = add_stub_headers(baseline_files, missing_headers)
        if added_count <= 0:
            break

        retry_count += 1
        added_count_total += added_count
        seen_missing_headers.update(missing_headers)
        correct_code = format_marked_project(baseline_files)

    if baseline is None:
        baseline = {"compile_ok": False, "error_class": "compile_error"}

    compile_ok = bool(baseline.get("compile_ok"))
    error_class = None if compile_ok else str(baseline.get("error_class") or "compile_error")
    return {
        "project": project.project_dir.name,
        "target": project.target,
        "file_type": project.file_type,
        "compile_ok": compile_ok,
        "error_class": error_class,
        "retry_count": retry_count,
        "stub_headers_added": added_count_total,
        "final_missing_headers": final_missing_headers,
        "stderr_tail": baseline.get("stderr_tail"),
        "stdout_tail": baseline.get("stdout_tail"),
    }


def main() -> int:
    args = parse_args()
    corpus_root = Path(args.corpus_root)
    out_path = Path(args.out)
    summary_path = Path(args.summary_out)
    workdir_root = Path(args.workdir) if args.workdir else Path(tempfile.gettempdir()) / "aie_v9_audit"
    workdir_root.mkdir(parents=True, exist_ok=True)

    tc_payload = None
    if args.validator_backend == "windows":
        tc_payload = serialize_toolchain(detect_toolchain(args.aietools, args.vitis))

    projects = iter_corpus_projects(corpus_root, args.target)
    if args.project_contains:
        needles = [needle.lower() for needle in args.project_contains if needle]
        projects = [
            project for project in projects
            if any(needle in project.project_dir.name.lower() for needle in needles)
        ]
    if args.limit is not None:
        projects = projects[: args.limit]

    rows: list[dict] = []
    summary = {
        "projects_seen": len(projects),
        "compile_ok": 0,
        "compile_failed": 0,
        "error_classes": {},
        "stub_retries": 0,
        "stub_headers_added": 0,
    }
    started_at = time.time()
    completed = 0

    def record_row(row: dict) -> None:
        nonlocal completed
        rows.append(row)
        completed += 1
        if row["compile_ok"]:
            summary["compile_ok"] += 1
        else:
            summary["compile_failed"] += 1
            error_class = row["error_class"]
            summary["error_classes"][error_class] = summary["error_classes"].get(error_class, 0) + 1
        summary["stub_retries"] += int(row["retry_count"])
        summary["stub_headers_added"] += int(row["stub_headers_added"])
        if completed == len(projects) or completed % max(1, args.progress_every) == 0:
            elapsed = time.time() - started_at
            rate = completed / elapsed if elapsed > 0 else 0.0
            print(
                f"[audit progress] {completed}/{len(projects)} projects | ok={summary['compile_ok']} | fail={summary['compile_failed']} | {rate:.2f} proj/s",
                flush=True,
            )

    if args.workers <= 1:
        for project in projects:
            record_row(audit_project(project, args, tc_payload, workdir_root))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {
                executor.submit(audit_project, project, args, tc_payload, workdir_root): project.project_dir.name
                for project in projects
            }
            for future in concurrent.futures.as_completed(future_map):
                record_row(future.result())

    write_jsonl(out_path, rows)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
