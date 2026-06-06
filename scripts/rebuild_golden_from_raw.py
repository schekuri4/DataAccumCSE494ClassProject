#!/usr/bin/env python3
"""Rebuild golden AIE example folders from local raw JSONL source records."""

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx"}
HEADER_EXTENSIONS = {".h", ".hh", ".hpp", ".hxx"}
LOCAL_INCLUDE_RE = re.compile(r"^\s*#\s*include\s*\"([^\"]+)\"", re.MULTILINE)
QUOTED_STRING_RE = re.compile(r'"([^"]+)"')


@dataclass(frozen=True)
class RawFile:
    repo: str
    branch: str
    path: str
    code: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", default="data/raw", help="folder containing raw JSONL files")
    parser.add_argument("--output", default="golden file generation raw_full", help="destination folder")
    parser.add_argument("--limit", type=int, default=2000, help="maximum groups to save")
    parser.add_argument("--max-lines", type=int, default=250, help="maximum lines allowed in any saved file")
    parser.add_argument("--force", action="store_true", help="replace output folder first")
    return parser.parse_args()


def load_raw_files(raw_root: Path) -> dict[tuple[str, str, str], RawFile]:
    files: dict[tuple[str, str, str], RawFile] = {}
    for jsonl_path in sorted(raw_root.glob("*.jsonl")):
        with jsonl_path.open(encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                repo = row.get("repo") or repo_from_url(row.get("source_url") or row.get("source") or "")
                branch = row.get("branch") or branch_from_url(row.get("source_url") or row.get("source") or "") or "main"
                code = row.get("code")
                path = source_path(row)
                if not repo or not path or not isinstance(code, str):
                    continue
                key = (repo, branch, path)
                files.setdefault(key, RawFile(repo=repo, branch=branch, path=path, code=code))
    return files


def repo_from_url(url: str) -> str | None:
    marker = "github.com/"
    if marker not in url:
        return None
    parts = url.split(marker, 1)[1].split("/")
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def branch_from_url(url: str) -> str | None:
    marker = "/blob/"
    if marker not in url:
        return None
    rest = url.split(marker, 1)[1]
    return rest.split("/", 1)[0] if rest else None


def source_path(row: dict) -> str | None:
    metadata = row.get("metadata") or {}
    for value in (metadata.get("local_path"), metadata.get("path")):
        normalized = normalize_path(value)
        if normalized:
            return strip_repo_prefix(row.get("repo"), normalized)
    url = row.get("source_url") or row.get("source") or ""
    marker = "/blob/"
    if marker in url:
        rest = url.split(marker, 1)[1]
        parts = rest.split("/", 1)
        if len(parts) == 2:
            return normalize_path(parts[1])
    filename = row.get("filename")
    return normalize_path(filename)


def normalize_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.replace("\\", "/").strip("/")


def strip_repo_prefix(repo: str | None, path: str) -> str:
    if not repo:
        return path
    repo_name = repo.rsplit("/", 1)[-1]
    prefix = f"{repo_name}/"
    if path.startswith(prefix):
        return path[len(prefix):]
    return path


def group_candidates(files: dict[tuple[str, str, str], RawFile]) -> list[tuple[str, str, str, list[RawFile]]]:
    by_dir: dict[tuple[str, str, str], list[RawFile]] = defaultdict(list)
    for file in files.values():
        directory = str(Path(file.path).parent).replace("\\", "/")
        by_dir[(file.repo, file.branch, directory)].append(file)

    groups = []
    for (repo, branch, directory), group_files in by_dir.items():
        if not any(is_graph_header(file.path) for file in group_files):
            continue
        if not any(Path(file.path).suffix.lower() in SOURCE_EXTENSIONS for file in group_files):
            continue
        if not looks_like_aie_group(group_files):
            continue
        groups.append((repo, branch, directory, sorted(group_files, key=lambda file: file.path)))
    groups.sort(key=lambda item: (item[0], item[2]))
    return groups


def is_graph_header(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix not in HEADER_EXTENSIONS:
        return False
    name = Path(path).name.lower()
    stem = Path(path).stem.lower()
    return any(marker in name or marker == stem for marker in ("graph", "project", "subsystem", "system", "xgemm"))


def looks_like_aie_group(files: list[RawFile]) -> bool:
    joined = "\n".join(file.path.lower() for file in files)
    return any(marker in joined for marker in ("aie", "adf", "kernel", "graph", "vitis"))


def expand_includes(seed_files: list[RawFile], index: dict[tuple[str, str, str], RawFile]) -> list[RawFile]:
    output: dict[str, RawFile] = {}
    pending = list(seed_files)
    while pending:
        file = pending.pop(0)
        if file.path in output:
            continue
        output[file.path] = file
        for reference in local_file_references(file.code):
            included = resolve_raw_reference(file, reference, index)
            if included and included.path not in output:
                pending.append(included)
    return sorted(output.values(), key=lambda file: file.path)


def local_file_references(text: str) -> list[str]:
    references = [match.group(1).strip() for match in LOCAL_INCLUDE_RE.finditer(text)]
    for line in text.splitlines():
        if "adf::source" in line or "adf::headers" in line:
            references.extend(match.group(1).strip() for match in QUOTED_STRING_RE.finditer(line))
    return [reference for reference in references if reference]


def resolve_raw_reference(
    including_file: RawFile,
    reference_path: str,
    index: dict[tuple[str, str, str], RawFile],
) -> RawFile | None:
    exact_path = normalize_repo_reference(f"{Path(including_file.path).parent.as_posix()}/{reference_path}")
    exact = index.get((including_file.repo, including_file.branch, exact_path))
    if exact:
        return exact

    parent = Path(including_file.path).parent
    while parent.as_posix() not in {"", "."}:
        candidate_path = normalize_repo_reference(f"{parent.as_posix()}/{Path(reference_path).name}")
        candidate = index.get((including_file.repo, including_file.branch, candidate_path))
        if candidate:
            return candidate
        parent = parent.parent
    return None


def normalize_repo_reference(path: str) -> str:
    parts: list[str] = []
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def safe_name(text: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip("/"))
    name = re.sub(r"_+", "_", name).strip("._")
    return name or "example"


def output_dir_for_group(root: Path, repo: str, directory: str) -> Path:
    base = bounded_safe_name(directory)
    return root / f"{safe_name(repo.replace('/', '_'))}__{base}"


def bounded_safe_name(text: str, limit: int = 120) -> str:
    name = safe_name(text)
    if len(name) <= limit:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    return f"{name[: limit - 13]}_{digest}"


def main() -> int:
    args = parse_args()
    raw_root = Path(args.raw)
    output_root = Path(args.output)
    if args.force and output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    index = load_raw_files(raw_root)
    groups = group_candidates(index)
    records = []
    counts: Counter[str] = Counter()
    for repo, branch, directory, seed_files in groups[: max(args.limit, 0)]:
        files = expand_includes(seed_files, index)
        oversized = [file for file in files if count_lines(file.code) > args.max_lines]
        if oversized:
            record = {
                "status": "over_line_limit",
                "repo": repo,
                "branch": branch,
                "source_path": directory,
                "reason": f"{oversized[0].path} has {count_lines(oversized[0].code)} lines",
            }
        else:
            target_dir = output_dir_for_group(output_root, repo, directory)
            target_dir.mkdir(parents=True, exist_ok=True)
            file_records = []
            for file in files:
                relative = Path(file.path).relative_to(directory) if directory != "." and file.path.startswith(f"{directory}/") else Path(file.path)
                destination = target_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(file.code, encoding="utf-8", newline="")
                file_records.append({"name": relative.as_posix(), "lines": count_lines(file.code), "source_path": file.path})
            record = {
                "status": "saved",
                "repo": repo,
                "branch": branch,
                "source_path": directory,
                "directory": str(target_dir),
                "files": file_records,
            }
        counts[record["status"]] += 1
        records.append(record)

    with (output_root / "manifest.jsonl").open("w", encoding="utf-8") as manifest:
        for record in records:
            manifest.write(json.dumps(record, sort_keys=True) + "\n")
    (output_root / "manifest_summary.json").write_text(
        json.dumps({"counts": dict(counts), "total": len(records), "raw_files": len(index)}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"loaded {len(index)} raw files")
    print(f"discovered {len(groups)} candidate groups")
    print(f"saved {counts.get('saved', 0)} groups out of {len(records)} attempted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())