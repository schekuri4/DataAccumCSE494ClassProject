#!/usr/bin/env python3
"""Audit golden AIE project folders for unresolved local file references."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
LOCAL_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)
QUOTED_STRING_RE = re.compile(r'"([^"]+)"')
IGNORED_PREFIXES = (
    "adf",
    "aie_api/",
    "experimental/",
    "cardano/",
)
IGNORED_NAMES = {
    "assert.h",
    "stdint.h",
    "stdlib.h",
    "stdio.h",
    "string.h",
    "math.h",
    "complex.h",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="golden corpus root to audit")
    parser.add_argument("--copy-good-to", help="optional folder to receive projects with zero missing refs")
    parser.add_argument("--summary", help="optional JSON summary path")
    parser.add_argument("--manifest", help="optional JSONL audit manifest path")
    return parser.parse_args()


def local_file_references(text: str) -> list[str]:
    references = [match.group(1).strip() for match in LOCAL_INCLUDE_RE.finditer(text)]
    for line in text.splitlines():
        if "adf::source" in line or "adf::headers" in line:
            references.extend(match.group(1).strip() for match in QUOTED_STRING_RE.finditer(line))
    return [reference for reference in references if reference and not ignored_reference(reference)]


def ignored_reference(reference: str) -> bool:
    normalized = reference.replace("\\", "/").lstrip("./")
    if normalized in IGNORED_NAMES:
        return True
    return any(normalized.startswith(prefix) for prefix in IGNORED_PREFIXES)


def resolve_reference(source: Path, reference: str, project_root: Path) -> Path | None:
    candidate = (source.parent / reference).resolve()
    if is_inside(candidate, project_root) and candidate.exists():
        return candidate
    basename = Path(reference).name
    parent = source.parent
    while is_inside(parent.resolve(), project_root):
        candidate = parent / basename
        if candidate.exists():
            return candidate.resolve()
        if parent == project_root:
            break
        parent = parent.parent
    return None


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def audit_project(project: Path) -> dict:
    project = project.resolve()
    missing = []
    file_count = 0
    for path in sorted(project.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        file_count += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for reference in local_file_references(text):
            if resolve_reference(path, reference, project) is None:
                missing.append({
                    "file": path.relative_to(project).as_posix(),
                    "reference": reference,
                })
    return {
        "project": project.name,
        "path": str(project),
        "files": file_count,
        "missing": missing,
        "missing_count": len(missing),
    }


def copy_good_projects(records: list[dict], destination: Path) -> None:
    import shutil

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for record in records:
        if record["missing_count"]:
            continue
        shutil.copytree(record["path"], destination / record["project"])


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    projects = sorted(path for path in root.iterdir() if path.is_dir())
    records = [audit_project(project) for project in projects]

    summary = {
        "projects": len(records),
        "files": sum(record["files"] for record in records),
        "projects_with_missing_local_refs": sum(1 for record in records if record["missing_count"]),
        "missing_local_refs": sum(record["missing_count"] for record in records),
        "clean_projects": sum(1 for record in records if not record["missing_count"]),
    }

    if args.copy_good_to:
        destination = Path(args.copy_good_to)
        copy_good_projects(records, destination)
        summary["copied_clean_projects_to"] = str(destination)

    if args.manifest:
        manifest = Path(args.manifest)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

    if args.summary:
        summary_path = Path(args.summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())