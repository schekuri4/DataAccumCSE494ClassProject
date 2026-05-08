#!/usr/bin/env python3
"""
Fetch un-cached AIE blobs from the GitHub API using cached tree listings.
- Reads all cached tree JSONs to find blob SHAs
- Skips blobs already in cache or already in any existing corpus file
- Fetches only AIE-relevant paths
- Appends new unique entries to data/raw/aie_local_corpus_p2.jsonl

Usage:
    python scripts/fetch_missing_blobs_p2.py

Set GITHUB_TOKEN env var for authenticated requests (5000 req/hr vs 60).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / ".cache" / "aie_source_corpus"
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_PATH = RAW_DIR / "aie_local_corpus_p2.jsonl"

EXISTING_CORPUS_FILES = [
    RAW_DIR / "aie_github_sources.jsonl",
    RAW_DIR / "aie_expanded_sources.jsonl",
]

SOURCE_SUFFIXES = {".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"}
MAX_BLOB_BYTES = 60_000
API_DELAY = 0.3  # seconds between requests (conservative for authenticated)

KERNEL_KEYWORDS = [
    "aie::vector", "input_buffer", "output_buffer", "input_stream", "output_stream",
    "chess_prepare_for_pipelining", "aie::begin_vector", "readincr", "writeincr",
    "input_window", "output_window", "aie::accum", "aie_api/aie.hpp", "#include <adf.h>",
]
GRAPH_KEYWORDS = [
    "adf::graph", "adf::connect", "adf::PLIO", "adf::GMIO", "kernel::create",
]

SKIP_PATH_FRAGMENTS = [
    "/.autopilot/", "/_x/", "/test/", "/tests/", "/experimental/",
    "/extern/", "/third_party/", "/__pycache__/", "/build/",
]

PATH_HINTS = [
    "aie", "graph", "kernel", "versal", "adf", "gmio", "plio", "beam",
    "fft", "fir", "matmul", "gemm", "channel", "demod", "coder", "cfar",
]


def content_hash(code: str) -> str:
    return hashlib.sha256(code.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def repo_full_name_from_tree(tree: dict) -> str:
    url = str(tree.get("url") or "")
    m = re.search(r"/repos/([^/]+/[^/]+)/git/trees/", url)
    return m.group(1) if m else ""


def cached_blob_path(full_name: str, blob_sha: str) -> Path:
    url = f"https://api.github.com/repos/{full_name}/git/blobs/{blob_sha}"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / "blobs" / f"{digest}.json"


def load_blob_from_cache(full_name: str, blob_sha: str) -> str | None:
    bp = cached_blob_path(full_name, blob_sha)
    if not bp.exists():
        return None
    blob = json.loads(bp.read_text(encoding="utf-8"))
    content = str(blob.get("content") or "")
    if not content:
        return ""
    if blob.get("encoding") == "base64":
        return base64.b64decode(content.encode("ascii")).decode("utf-8", errors="replace")
    return content


def is_valid_path(path: str) -> bool:
    suf = Path(path).suffix.lower()
    if suf not in SOURCE_SUFFIXES:
        return False
    low = "/" + path.replace("\\", "/").lower() + "/"
    if any(frag in low for frag in SKIP_PATH_FRAGMENTS):
        return False
    # Must contain at least one path hint
    return any(hint in low for hint in PATH_HINTS)


def is_aie_candidate(code: str) -> bool:
    low = code.lower()
    return any(k.lower() in low for k in KERNEL_KEYWORDS + GRAPH_KEYWORDS)


def detect_category(code: str) -> str:
    low = code.lower()
    if "adf::graph" in low or "connect<" in low or "kernel::create" in low:
        return "graph"
    return "kernel"


def detect_interfaces(code: str) -> list[str]:
    low = code.lower()
    out = []
    if any(t in low for t in ["input_buffer", "output_buffer", "input_window", "output_window"]):
        out.append("buffer")
    if any(t in low for t in ["input_stream", "output_stream", "readincr", "writeincr"]):
        out.append("stream")
    if "cascade" in low:
        out.append("cascade")
    if any(t in low for t in ["plio", "gmio"]):
        out.append("external_io")
    return out or ["unknown"]


def detect_data_types(code: str) -> list[str]:
    low = code.lower()
    return [t for t in ["int8","int16","int32","float","cint16","cint32","cfloat"] if t in low] or ["unknown"]


def detect_compute_patterns(code: str) -> list[str]:
    low = code.lower()
    checks = {
        "fir_filter": ["fir", "tap", "mac"],
        "fft_butterfly": ["fft", "butterfly", "twiddle"],
        "matrix_multiply": ["gemm", "matmul", "matrix"],
        "beamforming": ["beam", "steer"],
        "interpolation": ["interpol", "upsample"],
        "decimation": ["decim", "downsample"],
        "sorting_network": ["sort", "bitonic"],
        "peak_detection": ["peak", "threshold"],
        "qam_demodulation": ["qam", "llr"],
        "cfar_detection": ["cfar"],
        "digital_downconversion": ["downconversion", "nco", "mixer"],
        "ldpc_update": ["ldpc", "belief"],
        "viterbi_decoder": ["viterbi", "trellis"],
    }
    return [p for p, hints in checks.items() if any(h in low for h in hints)] or ["generic_aie"]


def load_existing_seen() -> tuple[set[str], set[str]]:
    hashes: set[str] = set()
    urls: set[str] = set()
    for path in EXISTING_CORPUS_FILES + [OUTPUT_PATH]:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    code = row.get("code", "")
                    if code:
                        hashes.add(content_hash(code))
                    url = row.get("source_url") or row.get("source") or ""
                    if url:
                        urls.add(url)
                except Exception:
                    pass
    return hashes, urls


class GitHubClient:
    def __init__(self, token: str | None) -> None:
        self.token = token
        self.last_request = 0.0
        self.requests_made = 0
        self.rate_limit_remaining = 5000

    def _headers(self) -> dict:
        h = {"User-Agent": "AIE-Corpus-Miner", "Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get_blob(self, full_name: str, blob_sha: str) -> str | None:
        """Fetch blob text, using cache if available, else API."""
        cached = load_blob_from_cache(full_name, blob_sha)
        if cached is not None:
            return cached

        elapsed = time.monotonic() - self.last_request
        if elapsed < API_DELAY:
            time.sleep(API_DELAY - elapsed)

        url = f"https://api.github.com/repos/{full_name}/git/blobs/{blob_sha}"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.rate_limit_remaining = int(resp.headers.get("X-RateLimit-Remaining", 5000))
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print(f"  [rate-limit] Sleeping 60s... remaining={self.rate_limit_remaining}")
                time.sleep(60)
                return None
            return None
        except Exception:
            return None

        self.last_request = time.monotonic()
        self.requests_made += 1

        # Cache it
        bp = cached_blob_path(full_name, blob_sha)
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        content = str(payload.get("content") or "")
        if not content:
            return ""
        if payload.get("encoding") == "base64":
            return base64.b64decode(content.encode("ascii")).decode("utf-8", errors="replace")
        return content


def build_source_url(full_name: str, branch: str, path: str) -> str:
    return f"https://github.com/{full_name}/blob/{branch}/{path}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch missing AIE blobs via GitHub API into corpus_p2.")
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--max-bytes", type=int, default=MAX_BLOB_BYTES)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N new entries (0=unlimited)")
    args = parser.parse_args()

    output_path = Path(args.output)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("WARNING: GITHUB_TOKEN not set — limited to 60 req/hr")
    client = GitHubClient(token)

    print("Loading existing corpus for dedup...")
    seen_hashes, seen_urls = load_existing_seen()
    print(f"  {len(seen_hashes)} content hashes, {len(seen_urls)} URLs")

    trees_dir = CACHE_DIR / "trees"
    tree_paths = sorted(trees_dir.glob("*.json"))
    print(f"Scanning {len(tree_paths)} cached trees for un-fetched AIE blobs...")

    # Collect all candidate blobs across all trees
    candidates: list[tuple[str, str, str, str, int]] = []  # (full_name, branch, rel_path, blob_sha, size)
    for tree_path in tree_paths:
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        full_name = repo_full_name_from_tree(tree)
        if not full_name:
            continue
        branch = "main"
        for entry in tree.get("tree", []):
            if entry.get("type") != "blob":
                continue
            rel_path = str(entry.get("path") or "").replace("\\", "/")
            if not is_valid_path(rel_path):
                continue
            blob_size = int(entry.get("size") or 0)
            if blob_size <= 0 or blob_size > args.max_bytes:
                continue
            blob_sha = str(entry.get("sha") or "")
            if not blob_sha:
                continue
            source_url = build_source_url(full_name, branch, rel_path)
            if source_url in seen_urls:
                continue
            # Skip if already cached (will be picked up by mine_cached_corpus_p2.py)
            if cached_blob_path(full_name, blob_sha).exists():
                continue
            candidates.append((full_name, branch, rel_path, blob_sha, blob_size))

    print(f"Found {len(candidates)} un-cached AIE-path blobs to fetch")

    if args.dry_run:
        repo_counts: Counter[str] = Counter(c[0] for c in candidates)
        print("\n  Breakdown by repo:")
        for repo, count in repo_counts.most_common():
            print(f"    {count:4d}  {repo}")
        print("\n[dry-run] No files written.")
        return

    new_entries: list[dict] = []
    skipped_not_aie = 0
    repo_counts = Counter()
    out_fh = output_path.open("a", encoding="utf-8")

    try:
        for i, (full_name, branch, rel_path, blob_sha, blob_size) in enumerate(candidates):
            if args.limit and len(new_entries) >= args.limit:
                break
            if (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(candidates)}] new={len(new_entries)} api_calls={client.requests_made} rate_remaining={client.rate_limit_remaining}")

            source_url = build_source_url(full_name, branch, rel_path)
            code = client.get_blob(full_name, blob_sha)
            if not code:
                continue
            code = code.replace("\r\n", "\n").strip()
            if not is_aie_candidate(code):
                skipped_not_aie += 1
                continue
            h = content_hash(code)
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            seen_urls.add(source_url)
            repo_counts[full_name] += 1

            entry = {
                "filename": Path(rel_path).name,
                "code": code,
                "source": source_url,
                "category": detect_category(code),
                "bug_type": None,
                "bug_explanation": None,
                "metadata": {
                    "path": rel_path,
                    "compute_pattern": detect_compute_patterns(code),
                    "data_types": detect_data_types(code),
                    "interfaces": detect_interfaces(code),
                    "blob_sha": blob_sha,
                    "blob_size": blob_size,
                    "source": "github_api_fetch",
                },
                "source_url": source_url,
                "repo": full_name,
                "branch": branch,
            }
            new_entries.append(entry)
            out_fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            out_fh.flush()
    finally:
        out_fh.close()

    print(f"\n=== Done ===")
    print(f"  New entries written:  {len(new_entries)}")
    print(f"  Skipped (not AIE):    {skipped_not_aie}")
    print(f"  API calls made:       {client.requests_made}")
    print(f"\n  Breakdown by repo:")
    for repo, count in repo_counts.most_common():
        print(f"    {count:4d}  {repo}")
    print(f"\nOutput: {output_path}")


if __name__ == "__main__":
    main()
