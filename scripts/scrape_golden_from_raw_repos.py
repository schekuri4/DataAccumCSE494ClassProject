#!/usr/bin/env python3
"""Scrape golden AIE examples from repos recovered from raw JSONL metadata."""

import argparse
import json
import os
import shutil
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError

import scrape_golden_aie_examples as scraper


FALLBACK_BRANCHES = [
    "main",
    "master",
    "2025.2",
    "2025.1",
    "2024.2",
    "2024.1",
    "2023.2",
    "2023.1",
    "devel",
    "sycl",
    "half-bridge-for-paper",
    "trilli-x",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", default="data/raw", help="folder containing raw JSONL files")
    parser.add_argument("--output", default="golden file generation full", help="destination folder")
    parser.add_argument("--limit", type=int, default=3000, help="maximum total candidate groups to attempt")
    parser.add_argument("--max-lines", type=int, default=250, help="maximum lines allowed in any saved file; use 0 for no limit")
    parser.add_argument("--force", action="store_true", help="replace output folder before scraping")
    parser.add_argument("--sleep", type=float, default=0.0, help="seconds to sleep between raw downloads")
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN"), help="GitHub token; defaults to GITHUB_TOKEN")
    return parser.parse_args()


def raw_repo_branches(raw_root: Path) -> tuple[Counter[str], dict[str, Counter[str]]]:
    repo_counts: Counter[str] = Counter()
    branch_counts: dict[str, Counter[str]] = defaultdict(Counter)
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
                branch = row.get("branch")
                if repo:
                    repo_counts[repo] += 1
                if repo and branch:
                    branch_counts[repo][branch] += 1
    return repo_counts, branch_counts


def repo_from_url(url: str) -> str | None:
    marker = "github.com/"
    if marker not in url:
        return None
    parts = url.split(marker, 1)[1].split("/")
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def branch_order(repo: str, branch_counts: dict[str, Counter[str]]) -> list[str]:
    branches = [branch for branch, _ in branch_counts.get(repo, Counter()).most_common()]
    for branch in FALLBACK_BRANCHES:
        if branch not in branches:
            branches.append(branch)
    return branches


def write_manifests(output_root: Path, records: list[dict]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / "manifest.jsonl").open("w", encoding="utf-8") as manifest:
        for record in records:
            manifest.write(json.dumps(record, sort_keys=True) + "\n")
    counts: Counter[str] = Counter(record["status"] for record in records)
    summary = {"counts": dict(counts), "total": len(records)}
    (output_root / "manifest_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    raw_root = Path(args.raw)
    output_root = Path(args.output)
    if args.force and output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    repo_counts, branch_counts = raw_repo_branches(raw_root)
    records: list[dict] = []
    attempted_groups = 0
    repos = [repo for repo, _ in repo_counts.most_common()]
    print(f"token_present={bool(args.github_token)}")
    print(f"repo_count={len(repos)}")
    for repo in repos:
        if attempted_groups >= args.limit:
            break
        branches = branch_order(repo, branch_counts)
        try:
            branch_files = scraper.find_branch_files(repo, branches, args.github_token)
            if not branch_files:
                record = {"status": "no_branch", "repo": repo, "branches": branches}
                records.append(record)
                print(json.dumps(record, sort_keys=True), flush=True)
                continue
            branch, repo_files = branch_files
            groups = scraper.discover_groups(repo_files)
            repo_file_index = {(file.repo, file.branch, file.path): file for file in repo_files}
            saved = 0
            for group in groups:
                if attempted_groups >= args.limit:
                    break
                print(
                    json.dumps(
                        {
                            "event": "group_start",
                            "repo": repo,
                            "branch": branch,
                            "source_path": group.directory,
                            "file_count": len(group.files),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                record = scraper.save_group(
                    group=group,
                    output_root=output_root,
                    max_lines=args.max_lines,
                    token=args.github_token,
                    force=True,
                    sleep_seconds=args.sleep,
                    repo_file_index=repo_file_index,
                )
                records.append(record)
                attempted_groups += 1
                if record["status"] == "saved":
                    saved += 1
                print(
                    json.dumps(
                        {
                            "event": "group_done",
                            "repo": repo,
                            "source_path": group.directory,
                            "status": record["status"],
                            "directory": record.get("directory"),
                            "reason": record.get("reason"),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            print(json.dumps({"repo": repo, "branch": branch, "groups": len(groups), "saved": saved}, sort_keys=True), flush=True)
        except (HTTPError, URLError, TimeoutError) as error:
            record = {"status": "scan_failed", "repo": repo, "reason": str(error)}
            records.append(record)
            print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
        except Exception as error:
            record = {
                "status": "unexpected_error",
                "repo": repo,
                "reason": f"{type(error).__name__}: {error}",
            }
            records.append(record)
            print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
            traceback.print_exc()

    write_manifests(output_root, records)
    saved = sum(1 for record in records if record["status"] == "saved")
    print(f"saved {saved} groups out of {attempted_groups} attempted", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())