#!/usr/bin/env python3
"""Hydrate missing project-local include files for golden AIE project folders.

For each project directory in `golden repos/`, this script:
1. Uses `golden repos/manifest.jsonl` to map the folder to repo/branch/source path.
2. Scans local includes (quoted `#include "..."`) recursively.
3. Resolves missing headers from a local repo mirror first, then GitHub raw URLs.
4. Writes recovered files into the same relative location under the project folder.

This improves compile coverage by filling dependency gaps in harvested project slices.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import posixpath
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

INCLUDE_RE = re.compile(r'^\s*#\s*include\s*([<"])([^>"]+)([>"])', re.MULTILINE)
KERNEL_SOURCE_RE = re.compile(
    r'\bsource\s*\([^)]*\)\s*=\s*"([^"]+\.(?:c|cc|cpp|cxx))"',
    re.IGNORECASE,
)
SOURCE_EXTS = {".h", ".hh", ".hpp", ".hxx", ".c", ".cc", ".cpp", ".cxx"}
SYSTEM_PREFIXES = ("asm/", "bits/", "gnu/", "linux/", "sys/")
BUILD_FILE_NAMES = (
    "Makefile",
    "makefile",
    "GNUmakefile",
    "CMakeLists.txt",
    "cmakelists.txt",
)
INCLUDE_FLAG_RE = re.compile(r"(?:^|[\s\"'])-I\s*([^\s\"']+)|(?:^|[\s\"'])-I([^\s\"']+)")
CMAKE_INCLUDE_RE = re.compile(
    r"(?:include_directories|target_include_directories)\s*\((.*?)\)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class ProjectMeta:
    name: str
    repo: str
    branch: str
    source_root: str


@dataclass
class RepoContext:
    repo: str
    branch: str
    local_root: Path | None
    files_set: set[str]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--golden-root", default="golden repos", help="Golden project root folder.")
    ap.add_argument("--manifest", default="golden repos/manifest.jsonl", help="Manifest JSONL with repo mappings.")
    ap.add_argument("--external-root", default="aie_dataset/external", help="Local upstream repo mirrors root.")
    ap.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN"), help="GitHub token for fallback API/raw requests.")
    ap.add_argument("--limit-projects", type=int, default=0, help="Limit number of project folders processed (0 = all).")
    ap.add_argument("--max-added-per-project", type=int, default=200, help="Safety cap for files added per project.")
    ap.add_argument("--workers", type=int, default=min(16, max(4, os.cpu_count() or 4)), help="Number of projects to hydrate in parallel.")
    ap.add_argument("--progress-every", type=int, default=10, help="Print aggregate progress every N completed projects.")
    ap.add_argument("--sleep", type=float, default=0.0, help="Sleep in seconds between GitHub raw fetches.")
    ap.add_argument(
        "--include-angle-includes",
        action="store_true",
        help="Also scan angle includes (#include <...>) for project-style paths.",
    )
    ap.add_argument(
        "--resolve-anywhere-in-repo",
        action="store_true",
        help="If include is not under source root, allow unique resolution anywhere in upstream repo.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Discover and report files without writing them.")
    return ap.parse_args()


def github_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "aie-golden-hydrator",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(url: str, token: str | None) -> dict:
    req = Request(url, headers=github_headers(token))
    with urlopen(req, timeout=90) as rsp:
        return json.loads(rsp.read().decode("utf-8"))


def request_text(url: str, token: str | None) -> str:
    req = Request(url, headers=github_headers(token))
    with urlopen(req, timeout=90) as rsp:
        return rsp.read().decode("utf-8", errors="replace")


def load_env_token(default_token: str | None = None) -> str | None:
    if default_token:
        return default_token
    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(env_name)
        if value:
            return value
    for candidate in (Path(".venv/.env"), Path(".env")):
        if not candidate.exists():
            continue
        try:
            for raw_line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() in {"GITHUB_TOKEN", "GH_TOKEN"} and value.strip():
                    return value.strip()
        except OSError:
            continue
    return None


def normalize_rel(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def is_stub_text(text: str) -> bool:
    markers = (
        "Auto-generated stub",
        "Replace with the real upstream file when available",
        "Auto-generated baseline dependency stub",
    )
    return any(marker in text for marker in text)


def is_stub_file(path: Path) -> bool:
    try:
        return is_stub_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False


def load_manifest(path: Path) -> dict[str, ProjectMeta]:
    mapping: dict[str, ProjectMeta] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") != "saved":
                continue
            directory = str(row.get("directory") or "")
            name = normalize_rel(directory).split("/")[-1]
            repo = str(row.get("repo") or "").strip()
            branch = str(row.get("branch") or "main").strip() or "main"
            source_root = normalize_rel(str(row.get("source_path") or ""))
            if not name or not repo or not source_root:
                continue
            mapping[name] = ProjectMeta(name=name, repo=repo, branch=branch, source_root=source_root)
    return mapping


def build_local_repo_lookup(external_root: Path) -> dict[str, Path]:
    lookup: dict[str, Path] = {}
    if not external_root.exists():
        return lookup
    for entry in external_root.iterdir():
        if not entry.is_dir():
            continue
        key = entry.name.lower()
        lookup[key] = entry
    return lookup


def choose_local_repo_root(repo: str, local_lookup: dict[str, Path]) -> Path | None:
    owner, name = repo.split("/", 1)
    candidates = [
        name,
        name.replace("-", "_"),
        name.replace("_", "-"),
        f"{owner}_{name}",
        f"{owner}-{name}",
        f"{owner}_{name.replace('-', '_')}",
        f"{owner}-{name.replace('_', '-')}",
    ]
    for cand in candidates:
        hit = local_lookup.get(cand.lower())
        if hit is not None:
            return hit
    return None


def list_local_repo_files(root: Path) -> set[str]:
    files: set[str] = set()
    for p in root.rglob("*"):
        if p.is_file():
            files.add(normalize_rel(str(p.relative_to(root))))
    return files


def list_remote_repo_files(repo: str, branch: str, token: str | None) -> set[str]:
    owner_repo = quote(repo, safe="/")
    branch_ref = quote(branch, safe="")
    url = f"https://api.github.com/repos/{owner_repo}/git/trees/{branch_ref}?recursive=1"
    payload = request_json(url, token)
    files: set[str] = set()
    for item in payload.get("tree", []):
        if item.get("type") != "blob":
            continue
        p = str(item.get("path") or "")
        if p:
            files.add(normalize_rel(p))
    return files


def iter_candidate_build_files(source_root: str, repo_files: set[str]) -> list[str]:
    candidates: list[str] = []
    current = normalize_rel(source_root)
    visited: set[str] = set()
    while current not in visited:
        visited.add(current)
        for build_name in BUILD_FILE_NAMES:
            candidate = normalize_rel(f"{current}/{build_name}" if current else build_name)
            if candidate in repo_files:
                candidates.append(candidate)
        if not current or "/" not in current:
            break
        current = current.rsplit("/", 1)[0]
    return candidates


def parse_include_roots_from_build_text(repo_rel_path: str, text: str) -> set[str]:
    roots: set[str] = set()
    base_dir = normalize_rel(str(Path(repo_rel_path).parent))

    def add_root(raw: str) -> None:
        candidate = raw.strip().strip("\"'()")
        if not candidate:
            return
        if "$(" in candidate or "${" in candidate:
            return
        if candidate.startswith(("/", "-")):
            return
        normalized = posixpath.normpath(
            f"{base_dir}/{candidate}" if base_dir and candidate not in {"", "."} else candidate
        ).replace("\\", "/")
        normalized = normalize_rel(normalized)
        if normalized:
            roots.add(normalized)

    for match in INCLUDE_FLAG_RE.finditer(text):
        add_root(match.group(1) or match.group(2) or "")

    for payload in CMAKE_INCLUDE_RE.findall(text):
        compact = payload.replace("\n", " ")
        for token in re.split(r"[\s;]+", compact):
            add_root(token)

    return roots


def discover_include_roots(
    repo: str,
    branch: str,
    source_root: str,
    repo_files: set[str],
    token: str | None,
    local_root: Path | None,
) -> tuple[str, ...]:
    roots: set[str] = {normalize_rel(source_root)} if source_root else set()
    for build_file in iter_candidate_build_files(source_root, repo_files):
        try:
            if local_root is not None:
                text = (local_root / Path(build_file)).read_text(encoding="utf-8", errors="replace")
            else:
                raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{quote(build_file)}"
                text = request_text(raw_url, token)
        except (OSError, HTTPError, URLError, TimeoutError):
            continue
        roots.update(parse_include_roots_from_build_text(build_file, text))
    return tuple(sorted(root for root in roots if root))


def get_repo_context(repo: str, branch: str, local_lookup: dict[str, Path], token: str | None, cache: dict[tuple[str, str], RepoContext]) -> RepoContext:
    key = (repo, branch)
    if key in cache:
        return cache[key]

    local_root = choose_local_repo_root(repo, local_lookup)
    if local_root is not None:
        files_set = list_local_repo_files(local_root)
    else:
        files_set = list_remote_repo_files(repo, branch, token)

    ctx = RepoContext(repo=repo, branch=branch, local_root=local_root, files_set=files_set)
    cache[key] = ctx
    return ctx


def parse_includes(text: str, include_angle: bool) -> list[str]:
    out: list[str] = []
    for delim_start, inc, _ in INCLUDE_RE.findall(text or ""):
        if delim_start == "<" and not include_angle:
            continue

        normalized = normalize_rel(inc)
        if not normalized or normalized.startswith("/"):
            continue
        if normalized.startswith(SYSTEM_PREFIXES):
            continue
        if delim_start == "<" and "/" not in normalized and "." not in Path(normalized).name:
            # Skip likely standard library includes like <vector>, <string>, <cmath>.
            continue
        out.append(normalized)
    return out


def parse_kernel_sources(text: str) -> list[str]:
    out: list[str] = []
    for source_rel in KERNEL_SOURCE_RE.findall(text or ""):
        normalized = normalize_rel(source_rel)
        if not normalized or normalized.startswith("/"):
            continue
        out.append(normalized)
    return out


def rewrite_flattened_relative_includes(project_dir: Path) -> int:
    rewritten = 0
    for src in project_dir.rglob("*"):
        if not src.is_file() or src.suffix.lower() not in SOURCE_EXTS:
            continue
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        src_rel = normalize_rel(str(src.relative_to(project_dir)))
        changed = False

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            delim_start, inc, delim_end = match.groups()
            if not inc.startswith("../"):
                return match.group(0)
            normalized = normalize_local_include_target(src_rel, inc)
            if not normalized or normalized == inc:
                return match.group(0)
            if not (project_dir / Path(normalized)).exists():
                return match.group(0)
            changed = True
            return f'#include {delim_start}{normalized}{delim_end}'

        updated = INCLUDE_RE.sub(repl, text)
        if not changed or updated == text:
            continue
        try:
            src.write_text(updated, encoding="utf-8")
        except OSError:
            continue
        rewritten += 1
    return rewritten


def common_prefix_depth(path_a: str, path_b: str) -> int:
    parts_a = [part for part in normalize_rel(path_a).split("/") if part]
    parts_b = [part for part in normalize_rel(path_b).split("/") if part]
    depth = 0
    for left, right in zip(parts_a, parts_b):
        if left != right:
            break
        depth += 1
    return depth


def choose_best_repo_match(paths: list[str], source_root: str) -> str | None:
    if not paths:
        return None
    ranked = sorted(
        ((common_prefix_depth(path, source_root), len(normalize_rel(path).split("/")), path) for path in paths),
        reverse=True,
    )
    if len(ranked) == 1:
        return ranked[0][2]
    best = ranked[0]
    second = ranked[1]
    if best[:2] == second[:2]:
        return None
    return best[2]


def resolve_repo_target(
    source_file_rel: str,
    include_rel: str,
    source_root: str,
    repo_files: set[str],
    include_roots: tuple[str, ...],
    resolve_anywhere: bool,
) -> str | None:
    src_parent = normalize_rel(str(Path(source_file_rel).parent))
    root_candidates = [normalize_rel(source_root), *[normalize_rel(root) for root in include_roots]]
    deduped_roots: list[str] = []
    for root in root_candidates:
        if root in deduped_roots:
            continue
        deduped_roots.append(root)

    candidates = []
    for root in deduped_roots:
        if src_parent and src_parent != ".":
            candidates.append(normalize_rel(posixpath.normpath(f"{root}/{src_parent}/{include_rel}")))
        candidates.append(normalize_rel(posixpath.normpath(f"{root}/{include_rel}")))

    for cand in candidates:
        if cand in repo_files:
            return cand

    include_name = Path(include_rel).name
    if include_name:
        suffix = f"/{include_name}"
        for root in deduped_roots:
            prefix = f"{root}/" if root else ""
            exact = normalize_rel(f"{root}/{include_name}" if root else include_name)
            suffix_hits = [p for p in repo_files if p.startswith(prefix) and (p.endswith(suffix) or p == exact)]
            if len(suffix_hits) == 1:
                return suffix_hits[0]

    if resolve_anywhere:
        if include_rel in repo_files:
            return include_rel

        repo_exact = [p for p in repo_files if p.endswith(f"/{include_rel}")]
        best_exact = choose_best_repo_match(repo_exact, source_root)
        if best_exact is not None:
            return best_exact

        if include_name:
            repo_name_hits = [p for p in repo_files if p == include_name or p.endswith(f"/{include_name}")]
            best_name = choose_best_repo_match(repo_name_hits, source_root)
            if best_name is not None:
                return best_name

    return None


def normalize_local_include_target(source_file_rel: str, include_rel: str) -> str:
    """Map include path to an in-project writable relative path.

    For paths with parent traversals (../..), we collapse and clamp to project root.
    """

    src_parent = normalize_rel(str(Path(source_file_rel).parent))
    joined = f"{src_parent}/{include_rel}" if src_parent and src_parent != "." else include_rel
    normalized = posixpath.normpath(joined).replace("\\", "/")
    while normalized.startswith("../"):
        normalized = normalized[3:]
    if normalized == "..":
        return Path(include_rel).name
    return normalize_rel(normalized)


def choose_local_target_rel(source_file_rel: str, include_rel: str, source_root: str, repo_rel: str) -> str:
    return normalize_local_include_target(source_file_rel, include_rel)


def fetch_repo_file_text(ctx: RepoContext, repo_rel_path: str, token: str | None) -> str:
    if ctx.local_root is not None:
        path = ctx.local_root / Path(repo_rel_path)
        return path.read_text(encoding="utf-8", errors="replace")

    raw_url = f"https://raw.githubusercontent.com/{ctx.repo}/{ctx.branch}/{quote(repo_rel_path)}"
    return request_text(raw_url, token)


def map_local_files_to_rel(project_dir: Path, project_meta: ProjectMeta) -> set[str]:
    rels: set[str] = set()
    root = project_meta.source_root
    for f in project_dir.rglob("*"):
        if not f.is_file():
            continue
        rel = normalize_rel(str(f.relative_to(project_dir)))
        if rel:
            rels.add(rel)
            rels.add(normalize_rel(f"{root}/{rel}"))
    return rels


def hydrate_project(
    project_dir: Path,
    meta: ProjectMeta,
    ctx: RepoContext,
    include_roots: tuple[str, ...],
    token: str | None,
    max_added: int,
    dry_run: bool,
    sleep_seconds: float,
    include_angle_includes: bool,
    resolve_anywhere_in_repo: bool,
) -> tuple[int, int, int]:
    added = 0
    errors = 0

    existing_project_files = {normalize_rel(str(p.relative_to(project_dir))) for p in project_dir.rglob("*") if p.is_file()}

    queue: deque[Path] = deque(
        p for p in project_dir.rglob("*") if p.is_file() and p.suffix.lower() in SOURCE_EXTS
    )
    seen_paths: set[str] = set()

    while queue and added < max_added:
        src = queue.popleft()
        src_key = normalize_rel(str(src.relative_to(project_dir)))
        if src_key in seen_paths:
            continue
        seen_paths.add(src_key)

        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            errors += 1
            continue

        includes = parse_includes(text, include_angle=include_angle_includes)
        kernel_sources = parse_kernel_sources(text)
        dependencies = includes + [src for src in kernel_sources if src not in includes]
        if not dependencies:
            continue

        src_rel = normalize_rel(str(src.relative_to(project_dir)))
        for inc in dependencies:
            local_candidate = normalize_local_include_target(src_rel, inc)
            local_direct = normalize_rel(inc)
            existing_target_rel: str | None = None
            should_replace_stub = False
            if local_candidate in existing_project_files:
                existing_target_rel = local_candidate
            elif local_direct in existing_project_files:
                existing_target_rel = local_direct

            if existing_target_rel is not None:
                existing_target_path = project_dir / Path(existing_target_rel)
                should_replace_stub = is_stub_file(existing_target_path)
                if not should_replace_stub:
                    continue

            repo_rel = resolve_repo_target(
                src_rel,
                inc,
                meta.source_root,
                ctx.files_set,
                include_roots,
                resolve_anywhere=resolve_anywhere_in_repo,
            )
            if repo_rel is None:
                continue

            local_rel = choose_local_target_rel(src_rel, inc, meta.source_root, repo_rel)
            if not local_rel:
                continue
            if local_rel in existing_project_files and not should_replace_stub:
                continue

            try:
                fetched = fetch_repo_file_text(ctx, repo_rel, token)
            except (OSError, HTTPError, URLError, TimeoutError):
                errors += 1
                continue

            target = project_dir / Path(local_rel)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(fetched, encoding="utf-8")
            existing_project_files.add(local_rel)
            added += 1

            if target.suffix.lower() in SOURCE_EXTS:
                queue.append(target)

            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

            if added >= max_added:
                break

    rewritten = 0 if dry_run else rewrite_flattened_relative_includes(project_dir)
    return added, errors, rewritten


def iter_projects(golden_root: Path) -> Iterable[Path]:
    for entry in sorted(golden_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in {".git", "__pycache__"}:
            continue
        if entry.name.lower() in {"common", "kernels"}:
            continue
        yield entry


def process_project(
    project_dir: Path,
    meta: ProjectMeta,
    ctx: RepoContext,
    token: str | None,
    max_added: int,
    dry_run: bool,
    sleep_seconds: float,
    include_angle_includes: bool,
    resolve_anywhere_in_repo: bool,
) -> dict[str, object]:
    include_roots = discover_include_roots(
        repo=meta.repo,
        branch=meta.branch,
        source_root=meta.source_root,
        repo_files=ctx.files_set,
        token=token,
        local_root=ctx.local_root,
    )
    added, errors, rewritten = hydrate_project(
        project_dir=project_dir,
        meta=meta,
        ctx=ctx,
        include_roots=include_roots,
        token=token,
        max_added=max_added,
        dry_run=dry_run,
        sleep_seconds=sleep_seconds,
        include_angle_includes=include_angle_includes,
        resolve_anywhere_in_repo=resolve_anywhere_in_repo,
    )
    return {
        "project": project_dir.name,
        "repo": meta.repo,
        "branch": meta.branch,
        "added": added,
        "errors": errors,
        "rewritten_includes": rewritten,
        "local_mirror": str(ctx.local_root) if ctx.local_root else None,
        "include_roots": list(include_roots),
    }


def main() -> int:
    args = parse_args()
    golden_root = Path(args.golden_root)
    manifest_path = Path(args.manifest)
    external_root = Path(args.external_root)

    if not golden_root.exists():
        print(f"[hydrate] missing golden root: {golden_root}", file=sys.stderr)
        return 2
    if not manifest_path.exists():
        print(f"[hydrate] missing manifest: {manifest_path}", file=sys.stderr)
        return 2

    manifest_map = load_manifest(manifest_path)
    args.github_token = load_env_token(args.github_token)
    local_lookup = build_local_repo_lookup(external_root)
    repo_cache: dict[tuple[str, str], RepoContext] = {}

    total_projects = 0
    matched_projects = 0
    total_added = 0
    total_errors = 0
    skipped_unmapped = 0
    selected: list[tuple[Path, ProjectMeta]] = []

    for project_dir in iter_projects(golden_root):
        total_projects += 1
        if args.limit_projects > 0 and matched_projects >= args.limit_projects:
            break
        meta = manifest_map.get(project_dir.name)
        if meta is None:
            skipped_unmapped += 1
            continue
        selected.append((project_dir, meta))
        matched_projects += 1

    repo_contexts: dict[tuple[str, str], RepoContext] = {}
    runnable: list[tuple[Path, ProjectMeta, RepoContext]] = []
    for project_dir, meta in selected:
        try:
            ctx = get_repo_context(meta.repo, meta.branch, local_lookup, args.github_token, repo_cache)
            repo_contexts[(meta.repo, meta.branch)] = ctx
            runnable.append((project_dir, meta, ctx))
        except Exception as exc:  # noqa: BLE001
            total_errors += 1
            print(
                json.dumps(
                    {
                        "project": project_dir.name,
                        "status": "repo_context_failed",
                        "repo": meta.repo,
                        "branch": meta.branch,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                ),
                flush=True,
            )

    matched_projects = len(runnable)
    started_at = time.time()
    completed = 0

    def emit_progress() -> None:
        elapsed = time.time() - started_at
        rate = completed / elapsed if elapsed > 0 else 0.0
        print(
            f"[hydrate progress] {completed}/{matched_projects} projects | added={total_added} | errors={total_errors} | {rate:.2f} proj/s",
            file=sys.stderr,
            flush=True,
        )

    if args.workers <= 1:
        for project_dir, meta, ctx in runnable:
            try:
                row = process_project(
                    project_dir=project_dir,
                    meta=meta,
                    ctx=ctx,
                    token=args.github_token,
                    max_added=args.max_added_per_project,
                    dry_run=args.dry_run,
                    sleep_seconds=args.sleep,
                    include_angle_includes=args.include_angle_includes,
                    resolve_anywhere_in_repo=args.resolve_anywhere_in_repo,
                )
            except Exception as exc:  # noqa: BLE001
                total_errors += 1
                row = {
                    "project": project_dir.name,
                    "status": "hydrate_failed",
                    "repo": meta.repo,
                    "branch": meta.branch,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            completed += 1
            total_added += int(row.get("added", 0) or 0)
            total_errors += int(row.get("errors", 0) or 0)
            print(json.dumps(row), flush=True)
            if completed == matched_projects or completed % max(1, args.progress_every) == 0:
                emit_progress()
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {
                executor.submit(
                    process_project,
                    project_dir=project_dir,
                    meta=meta,
                    ctx=ctx,
                    token=args.github_token,
                    max_added=args.max_added_per_project,
                    dry_run=args.dry_run,
                    sleep_seconds=args.sleep,
                    include_angle_includes=args.include_angle_includes,
                    resolve_anywhere_in_repo=args.resolve_anywhere_in_repo,
                ): (project_dir, meta)
                for project_dir, meta, ctx in runnable
            }
            for future in concurrent.futures.as_completed(future_map):
                project_dir, meta = future_map[future]
                try:
                    row = future.result()
                except Exception as exc:  # noqa: BLE001
                    total_errors += 1
                    row = {
                        "project": project_dir.name,
                        "status": "hydrate_failed",
                        "repo": meta.repo,
                        "branch": meta.branch,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                completed += 1
                total_added += int(row.get("added", 0) or 0)
                total_errors += int(row.get("errors", 0) or 0)
                print(json.dumps(row), flush=True)
                if completed == matched_projects or completed % max(1, args.progress_every) == 0:
                    emit_progress()

    summary = {
        "projects_seen": total_projects,
        "projects_processed": matched_projects,
        "projects_unmapped": skipped_unmapped,
        "files_added": total_added,
        "errors": total_errors,
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
