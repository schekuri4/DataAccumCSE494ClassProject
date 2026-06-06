#!/usr/bin/env python3
"""Collect compact AIE graph/kernel examples from trusted GitHub repos.

The scraper scans repository trees, finds directories that contain both an AIE
graph header and a kernel/source file, downloads only files under a configurable
line limit, and writes a manifest for provenance.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_REPOS = [
    "Xilinx/Vitis-Tutorials",
    "Xilinx/Vitis_Libraries",
]
DEFAULT_BRANCHES = ["main", "master", "2025.2", "2025.1", "2024.2", "2024.1"]
SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx"}
HEADER_EXTENSIONS = {".h", ".hh", ".hpp", ".hxx"}
LOCAL_INCLUDE_RE = re.compile(r"^\s*#\s*include\s*\"([^\"]+)\"", re.MULTILINE)
QUOTED_STRING_RE = re.compile(r'"([^"]+)"')
PATTERN_REFERENCE_PREFIX = "__pattern__:"


@dataclass(frozen=True)
class RepoFile:
    repo: str
    branch: str
    path: str


@dataclass(frozen=True)
class CandidateGroup:
    repo: str
    branch: str
    directory: str
    files: tuple[RepoFile, ...]


def request_json(url: str, token: str | None) -> dict:
    request = Request(url, headers=github_headers(token))
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def request_text(url: str, token: str | None) -> str:
    request = Request(url, headers=github_headers(token))
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def github_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "aie-golden-file-scraper",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def find_branch_files(repo: str, branches: Iterable[str], token: str | None) -> tuple[str, list[RepoFile]] | None:
    for branch in branches:
        try:
            repo_files = list_repo_files(repo, branch, token)
            return branch, repo_files
        except HTTPError as error:
            if error.code == 404:
                continue
            raise
    return None


def list_repo_files(repo: str, branch: str, token: str | None) -> list[RepoFile]:
    owner_repo = quote(repo, safe="/")
    branch_ref = quote(branch, safe="")
    url = f"https://api.github.com/repos/{owner_repo}/git/trees/{branch_ref}?recursive=1"
    payload = request_json(url, token)
    if payload.get("truncated"):
        print(f"warning: tree for {repo}@{branch} was truncated by GitHub", file=sys.stderr)
    files: list[RepoFile] = []
    for item in payload.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if file_extension(path) in SOURCE_EXTENSIONS | HEADER_EXTENSIONS:
            files.append(RepoFile(repo=repo, branch=branch, path=path))
    return files


def discover_groups(files: Iterable[RepoFile]) -> list[CandidateGroup]:
    by_dir: dict[tuple[str, str, str], list[RepoFile]] = {}
    for file in files:
        directory = str(Path(file.path).parent).replace("\\", "/")
        by_dir.setdefault((file.repo, file.branch, directory), []).append(file)

    groups: list[CandidateGroup] = []
    for (repo, branch, directory), group_files in by_dir.items():
        graph_files = [file for file in group_files if is_graph_header(file.path)]
        source_files = [file for file in group_files if file_extension(file.path) in SOURCE_EXTENSIONS]
        if not graph_files or not source_files:
            continue
        if not looks_like_aie_group(group_files):
            continue
        selected = select_files(graph_files, source_files, group_files)
        groups.append(CandidateGroup(repo=repo, branch=branch, directory=directory, files=tuple(selected)))
    groups.sort(key=lambda group: (group.repo, group.directory))
    return groups


def select_files(graph_files: list[RepoFile], source_files: list[RepoFile], all_files: list[RepoFile]) -> list[RepoFile]:
    selected: list[RepoFile] = []
    selected.extend(sorted(graph_files, key=lambda file: file.path))
    selected.extend(sorted(source_files, key=lambda file: file.path))

    # Keep every header in the candidate directory, even when the basename does
    # not resemble the graph/kernel file. AIE examples often put required macros
    # and dimensions in generic support headers such as para_L0.h, config.h, or
    # data_helpers.h. Dropping those headers makes otherwise correct examples
    # fail baseline compilation before any injected bug is reached.
    for file in sorted(all_files, key=lambda item: item.path):
        if file in selected:
            continue
        if file_extension(file.path) in HEADER_EXTENSIONS:
            selected.append(file)
    return selected


def looks_like_aie_group(files: Iterable[RepoFile]) -> bool:
    joined = "\n".join(file.path.lower() for file in files)
    markers = ["aie", "adf", "kernel", "graph", "vitis"]
    return any(marker in joined for marker in markers)


def is_graph_header(path: str) -> bool:
    lower = path.lower()
    if file_extension(lower) not in HEADER_EXTENSIONS:
        return False
    name = Path(lower).name
    stem = Path(lower).stem
    graph_markers = (
        "graph",
        "project",
        "subsystem",
        "system",
        "xgemm",
    )
    return any(marker in name or marker == stem for marker in graph_markers)


def file_extension(path: str) -> str:
    return Path(path).suffix.lower()


def raw_url(file: RepoFile) -> str:
    return f"https://raw.githubusercontent.com/{file.repo}/{file.branch}/{quote(file.path)}"


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def safe_name(text: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip("/"))
    name = re.sub(r"_+", "_", name).strip("._")
    return name or "example"


def output_dir_for_group(root: Path, group: CandidateGroup) -> Path:
    base = bounded_safe_name(group.directory)
    repo_prefix = safe_name(group.repo.replace("/", "_"))
    return root / f"{repo_prefix}__{base}"


def bounded_safe_name(text: str, limit: int = 120) -> str:
    name = safe_name(text)
    if len(name) <= limit:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    return f"{name[: limit - 13]}_{digest}"


def save_group(
    group: CandidateGroup,
    output_root: Path,
    max_lines: int,
    token: str | None,
    force: bool,
    sleep_seconds: float,
    repo_file_index: dict[tuple[str, str, str], RepoFile],
) -> dict:
    target_dir = output_dir_for_group(output_root, group)
    if target_dir.exists() and not force:
        return {"status": "skipped_exists", "directory": str(target_dir), "repo": group.repo, "source_path": group.directory}

    downloaded: list[tuple[RepoFile, str, int]] = []
    downloaded_paths: set[str] = set()
    pending = list(group.files)
    while pending:
        file = pending.pop(0)
        if file.path in downloaded_paths:
            continue
        try:
            text = request_text(raw_url(file), token)
        except (HTTPError, URLError) as error:
            return failure_record(group, "download_failed", str(error))
        lines = count_lines(text)
        if max_lines > 0 and lines > max_lines:
            return failure_record(group, "over_line_limit", f"{file.path} has {lines} lines")
        downloaded.append((file, text, lines))
        downloaded_paths.add(file.path)
        for include_path in local_file_references(text):
            include_files = resolve_repo_references(file, include_path, repo_file_index)
            for include_file in include_files:
                if include_file.path not in downloaded_paths:
                    pending.append(include_file)
        if sleep_seconds:
            time.sleep(sleep_seconds)

    has_graph = any(is_graph_header(file.path) for file, _, _ in downloaded)
    has_source = any(file_extension(file.path) in SOURCE_EXTENSIONS for file, _, _ in downloaded)
    if not has_graph or not has_source:
        return failure_record(group, "incomplete_group", "missing graph header or source file")

    target_dir.mkdir(parents=True, exist_ok=True)
    layout_root = compute_layout_root([file.path for file, _, _ in downloaded])
    files_manifest = []
    for file, text, lines in downloaded:
        relative_path = relative_output_path(group, file, layout_root)
        destination = target_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8", newline="")
        files_manifest.append({
            "name": relative_path.as_posix(),
            "lines": lines,
            "source_url": raw_url(file),
            "source_path": file.path,
        })

    return {
        "status": "saved",
        "repo": group.repo,
        "branch": group.branch,
        "source_path": group.directory,
        "directory": str(target_dir),
        "files": files_manifest,
    }


def failure_record(group: CandidateGroup, status: str, reason: str) -> dict:
    return {
        "status": status,
        "repo": group.repo,
        "branch": group.branch,
        "source_path": group.directory,
        "reason": reason,
    }


def local_file_references(text: str) -> list[str]:
    references = [match.group(1).strip() for match in LOCAL_INCLUDE_RE.finditer(text)]
    for line in text.splitlines():
        if "adf::headers" in line:
            references.extend(match.group(1).strip() for match in QUOTED_STRING_RE.finditer(line))
            continue
        if "adf::source" in line or "source(" in line:
            references.extend(source_file_references_from_line(line))
    return [reference for reference in references if reference]


def source_file_references_from_line(line: str) -> list[str]:
    fragments = [match.group(1).strip() for match in QUOTED_STRING_RE.finditer(line) if match.group(1).strip()]
    if not fragments:
        return []
    if len(fragments) == 1:
        return fragments
    return [PATTERN_REFERENCE_PREFIX + "|".join(fragments)]


def resolve_repo_references(
    including_file: RepoFile,
    reference_path: str,
    repo_file_index: dict[tuple[str, str, str], RepoFile],
) -> list[RepoFile]:
    if reference_path.startswith(PATTERN_REFERENCE_PREFIX):
        return resolve_pattern_reference(including_file, reference_path, repo_file_index)

    normalized = normalize_repo_path(f"{Path(including_file.path).parent.as_posix()}/{reference_path}")
    exact = repo_file_index.get((including_file.repo, including_file.branch, normalized))
    if exact:
        return [exact]

    parent = Path(including_file.path).parent
    while parent.as_posix() not in {"", "."}:
        candidate = normalize_repo_path(f"{parent.as_posix()}/{reference_path}")
        found = repo_file_index.get((including_file.repo, including_file.branch, candidate))
        if found:
            return [found]
        candidate = normalize_repo_path(f"{parent.as_posix()}/{Path(reference_path).name}")
        found = repo_file_index.get((including_file.repo, including_file.branch, candidate))
        if found:
            return [found]
        parent = parent.parent
    repo_wide_matches = resolve_repo_reference_fallback(including_file, reference_path, repo_file_index)
    if repo_wide_matches:
        return repo_wide_matches
    return []


def resolve_pattern_reference(
    including_file: RepoFile,
    pattern_reference: str,
    repo_file_index: dict[tuple[str, str, str], RepoFile],
) -> list[RepoFile]:
    fragments = [fragment for fragment in pattern_reference[len(PATTERN_REFERENCE_PREFIX):].split("|") if fragment]
    if not fragments:
        return []

    ancestor_prefixes: list[str] = []
    parent = Path(including_file.path).parent
    while True:
        ancestor_prefixes.append(normalize_repo_path(parent.as_posix()))
        if parent.as_posix() in {"", "."}:
            break
        parent = parent.parent

    matches: list[RepoFile] = []
    seen_paths: set[str] = set()
    for prefix in ancestor_prefixes:
        prefix_with_fragment = normalize_repo_path("/".join(part for part in [prefix, fragments[0]] if part))
        for (repo, branch, path), repo_file in repo_file_index.items():
            if repo != including_file.repo or branch != including_file.branch:
                continue
            if prefix_with_fragment and not path.startswith(prefix_with_fragment):
                continue
            if ordered_fragments_match(path, fragments) and path not in seen_paths:
                seen_paths.add(path)
                matches.append(repo_file)
        if matches:
            return sort_repo_matches(matches, including_file)
    fallback_matches: list[RepoFile] = []
    seen_paths: set[str] = set()
    for repo_file in iter_repo_branch_files(including_file, repo_file_index):
        if ordered_fragments_match(repo_file.path, fragments) and repo_file.path not in seen_paths:
            seen_paths.add(repo_file.path)
            fallback_matches.append(repo_file)
    if fallback_matches:
        return sort_repo_matches(fallback_matches, including_file)
    return []


def resolve_repo_reference_fallback(
    including_file: RepoFile,
    reference_path: str,
    repo_file_index: dict[tuple[str, str, str], RepoFile],
) -> list[RepoFile]:
    normalized_reference = normalize_repo_path(reference_path)
    basename = Path(normalized_reference).name
    suffix_matches: list[RepoFile] = []
    basename_matches: list[RepoFile] = []
    seen_paths: set[str] = set()
    reference_parts = [part for part in normalized_reference.split("/") if part]

    for repo_file in iter_repo_branch_files(including_file, repo_file_index):
        normalized_path = normalize_repo_path(repo_file.path)
        if normalized_reference and (normalized_path == normalized_reference or normalized_path.endswith(f"/{normalized_reference}")):
            if normalized_path not in seen_paths:
                seen_paths.add(normalized_path)
                suffix_matches.append(repo_file)
            continue
        if basename and Path(normalized_path).name == basename:
            if reference_parts and not ordered_path_parts_match(normalized_path, reference_parts[-2:]):
                continue
            if normalized_path not in seen_paths:
                seen_paths.add(normalized_path)
                basename_matches.append(repo_file)

    if suffix_matches:
        return sort_repo_matches(suffix_matches, including_file)
    if basename_matches:
        return sort_repo_matches(basename_matches, including_file)
    return []


def iter_repo_branch_files(
    including_file: RepoFile,
    repo_file_index: dict[tuple[str, str, str], RepoFile],
) -> list[RepoFile]:
    return [
        repo_file
        for (repo, branch, _), repo_file in repo_file_index.items()
        if repo == including_file.repo and branch == including_file.branch
    ]


def ordered_path_parts_match(path: str, parts: list[str]) -> bool:
    if not parts:
        return True
    normalized_parts = [part for part in Path(path).parts if part not in {".", ""}]
    start = 0
    for part in parts:
        try:
            index = normalized_parts.index(part, start)
        except ValueError:
            return False
        start = index + 1
    return True


def sort_repo_matches(matches: list[RepoFile], including_file: RepoFile) -> list[RepoFile]:
    including_parent = normalize_repo_path(Path(including_file.path).parent.as_posix())
    return sorted(
        matches,
        key=lambda file: (
            -shared_prefix_parts(including_parent, normalize_repo_path(Path(file.path).parent.as_posix())),
            len(Path(file.path).parts),
            file.path,
        ),
    )


def shared_prefix_parts(left: str, right: str) -> int:
    left_parts = [part for part in left.split("/") if part]
    right_parts = [part for part in right.split("/") if part]
    count = 0
    for left_part, right_part in zip(left_parts, right_parts):
        if left_part != right_part:
            break
        count += 1
    return count


def ordered_fragments_match(path: str, fragments: list[str]) -> bool:
    start = 0
    for fragment in fragments:
        index = path.find(fragment, start)
        if index < 0:
            return False
        start = index + len(fragment)
    return True


def normalize_repo_path(path: str) -> str:
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


def compute_layout_root(paths: Iterable[str]) -> str:
    normalized_paths = [normalize_repo_path(path) for path in paths if normalize_repo_path(path)]
    if not normalized_paths:
        return ""
    split_paths = [path.split("/")[:-1] for path in normalized_paths]
    if not split_paths:
        return ""
    shared_parts = split_paths[0][:]
    for parts in split_paths[1:]:
        shared_len = min(len(shared_parts), len(parts))
        index = 0
        while index < shared_len and shared_parts[index] == parts[index]:
            index += 1
        shared_parts = shared_parts[:index]
        if not shared_parts:
            break
    return "/".join(shared_parts)


def relative_output_path(group: CandidateGroup, file: RepoFile, layout_root: str) -> Path:
    root = layout_root.strip("/")
    normalized = normalize_repo_path(file.path)
    if not root:
        return Path(normalized)
    prefix = f"{root}/"
    if normalized == root:
        return Path(Path(normalized).name)
    if normalized.startswith(prefix):
        return Path(normalized[len(prefix):])
    group_dir = group.directory.strip("/")
    if group_dir and normalized.startswith(f"{group_dir}/"):
        return Path(normalized[len(group_dir) + 1:])
    return Path(normalized)


def write_manifests(output_root: Path, records: list[dict], dry_run: bool) -> None:
    if dry_run:
        return
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.jsonl"
    summary_path = output_root / "manifest_summary.json"
    with manifest_path.open("w", encoding="utf-8") as manifest:
        for record in records:
            manifest.write(json.dumps(record, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    summary_path.write_text(json.dumps({"counts": counts, "total": len(records)}, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="golden file generation", help="folder where examples are written")
    parser.add_argument("--repo", action="append", dest="repos", help="GitHub repo in owner/name form; can be repeated")
    parser.add_argument("--branch", action="append", dest="branches", help="branch names to try, in order")
    parser.add_argument("--limit", type=int, default=100, help="maximum candidate groups to save")
    parser.add_argument("--max-lines", type=int, default=250, help="maximum lines allowed in any saved file; use 0 for no limit")
    parser.add_argument("--force", action="store_true", help="overwrite existing generated example directories")
    parser.add_argument("--dry-run", action="store_true", help="discover candidates without downloading or writing files")
    parser.add_argument("--sleep", type=float, default=0.0, help="seconds to sleep between raw downloads")
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN"), help="GitHub token; defaults to GITHUB_TOKEN")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repos = args.repos or DEFAULT_REPOS
    branches = args.branches or DEFAULT_BRANCHES
    output_root = Path(args.output)

    all_files: list[RepoFile] = []
    for repo in repos:
        try:
            branch_files = find_branch_files(repo, branches, args.github_token)
            if not branch_files:
                print(f"warning: no branch found for {repo}; tried {branches}", file=sys.stderr)
                continue
            branch, repo_files = branch_files
            all_files.extend(repo_files)
            print(f"found {len(repo_files)} source/header files in {repo}@{branch}")
        except (HTTPError, URLError) as error:
            print(f"warning: failed to scan {repo}: {error}", file=sys.stderr)

    groups = discover_groups(all_files)
    print(f"discovered {len(groups)} candidate graph/source groups")
    repo_file_index = {(file.repo, file.branch, file.path): file for file in all_files}

    selected_groups = groups[: max(args.limit, 0)]
    if args.dry_run:
        for group in selected_groups:
            print(json.dumps({
                "repo": group.repo,
                "branch": group.branch,
                "source_path": group.directory,
                "file_count": len(group.files),
            }, sort_keys=True))
        return 0

    records = []
    for group in selected_groups:
        record = save_group(
            group=group,
            output_root=output_root,
            max_lines=args.max_lines,
            token=args.github_token,
            force=args.force,
            sleep_seconds=args.sleep,
            repo_file_index=repo_file_index,
        )
        records.append(record)
        print(f"{record['status']}: {record.get('directory', record.get('source_path'))}")

    write_manifests(output_root, records, args.dry_run)
    saved = sum(1 for record in records if record["status"] == "saved")
    print(f"saved {saved} example groups out of {len(records)} attempted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())